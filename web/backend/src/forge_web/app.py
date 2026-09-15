"""The FastAPI application: six routes, one lifespan, one static mount.

READ HANDLERS RETURN CACHED PROJECTIONS AND NEVER SPAWN A SUBPROCESS. The collector runs
`forge board --json` and nothing else — never `forge-bridge status`, never `forge-watch
check`. That is asserted by a test with a spawn spy, not just stated here.

STATIC ASSETS MOUNT AFTER THE API ROUTES. FastAPI matches in declaration order, so a mount at
"/" declared first would shadow /api/*. Dev uses the Vite proxy instead and the mount is
simply absent when the dist directory has not been built.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Callable

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .actions import ActionRefused, ActionRunner, Plan
from .ack import ack_scope
from .collector import Collector
from .config import Config, load, probe_contract
from .handles import HandleError, HandleTable, ReceiptConflict, ReceiptStore, canonical_body_sha
from .models import (
    AckRequest,
    AnswerRequest,
    ControlRequest,
    FleetSnapshot,
    MessageRequest,
    OperationReceipt,
    ParkRequest,
    Problem,
    ResolveRequest,
    SessionDetailView,
    SpawnRequest,
)
from .sampler import BoardSampler

SOURCE_MAX_BYTES = 1 << 20

_STATUS_FOR_CODE = {
    "mutations_disabled": 409,
    "contract_mismatch": 409,
    "object_changed": 409,
    "target_vanished": 409,
    "target_changed": 409,
    "request_id_conflict": 409,
    "duplicate_unresolved": 409,
    "unknown_object": 404,
    "wrong_object_kind": 404,
    "unknown_session": 404,
    "not_actionable": 422,
    "stale_revision": 409,
}


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()
    handles = HandleTable()
    receipts = ReceiptStore()
    sampler = BoardSampler()

    contract = probe_contract(cfg)
    mutations_enabled = cfg.mutations_enabled_at_launch and contract.ok
    mutations_detail = (
        "" if mutations_enabled else
        ("read-only: %s" % (contract.detail if not contract.ok
                            else "the launcher disabled mutations"))
    )

    collector = Collector(
        cfg, handles=handles, sampler=sampler, server_instance=receipts.instance,
        mutations_enabled=mutations_enabled, mutations_detail=mutations_detail,
    )

    state: dict[str, Any] = {"contract": contract}

    def mutations_gate() -> tuple[bool, str]:
        if not cfg.mutations_enabled_at_launch:
            return False, "the launcher started this server with mutations disabled"
        if not state["contract"].ok:
            return False, state["contract"].detail
        return True, ""

    runner = ActionRunner(
        cfg, receipts=receipts, sampler=sampler,
        live_identity=collector.live_identity, mutations_enabled=mutations_gate,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        await collector.start()
        try:
            yield
        finally:
            await collector.stop()

    app = FastAPI(title="Forge control center", lifespan=lifespan, docs_url=None, redoc_url=None)

    def problem(code: str, detail: str, revision: int | None = None,
                current_object_key: str | None = None) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS_FOR_CODE.get(code, 400),
            content=Problem(code=code, detail=detail, revision=revision,
                            current_object_key=current_object_key).model_dump(),
        )

    @app.exception_handler(ActionRefused)
    async def _refused(_request: Request, exc: ActionRefused) -> JSONResponse:
        return problem(exc.code, exc.detail, current_object_key=exc.current_object_key)

    @app.exception_handler(HandleError)
    async def _handle(_request: Request, exc: HandleError) -> JSONResponse:
        return problem(exc.code, exc.detail)

    @app.exception_handler(ReceiptConflict)
    async def _conflict(_request: Request, exc: ReceiptConflict) -> JSONResponse:
        return problem("request_id_conflict", exc.detail)

    @app.middleware("http")
    async def reconcile_before_resolution(request: Request, call_next):
        if request.method == "POST" and (request.url.path.startswith("/api/sessions/") or request.url.path == "/api/spawn"):
            try:
                body = await request.json()
            except ValueError:
                return await call_next(request)
            if not isinstance(body, dict):
                return await call_next(request)
            expected = body.get("expected_server_instance")
            if expected is not None and expected != receipts.instance:
                return problem("request_id_conflict", "Server restarted; outcome uncertain. This intent was not replayed.")
            action = request.url.path.rsplit("/", 1)[-1]
            owner = request.url.path.split("/")[3] if action != "spawn" else None
            # Defaults must match Pydantic's canonical submitted body.
            body.setdefault("expected_server_instance", None)
            if action == "spawn": body.setdefault("name", None)
            if action == "resolve": body.setdefault("note", "")
            if action == "ack": body.setdefault("slug_key", None)
            reservation = receipts._slots.get(str(body.get("request_id") or ""))
            if reservation is not None:
                if (reservation.action, reservation.session_key, reservation.body_sha) != (action, owner, canonical_body_sha(body)):
                    return problem("request_id_conflict", "request ID was used for a different action, target, or body")
                await reservation.ready.wait()
                if reservation.operation_id:
                    receipt = runner.operation(reservation.operation_id).model_copy(update={"duplicate": True})
                    return JSONResponse(receipt.model_dump(mode="json"))
        return await call_next(request)

    # ── reads ────────────────────────────────────────────────────────────────

    @app.get("/api/fleet", response_model=FleetSnapshot)
    def get_fleet() -> FleetSnapshot:
        return collector.snapshot().fleet

    @app.get("/api/sessions/{key}", response_model=SessionDetailView)
    def get_session(key: str):
        snapshot = collector.snapshot()
        detail = snapshot.details.get(key)
        if detail is None:
            return problem("unknown_session",
                           "no live session for handle %s in revision %d"
                           % (key, snapshot.revision), revision=snapshot.revision)
        return detail

    @app.get("/api/sessions/{key}/sources/{source_key:path}")
    def get_source(key: str, source_key: str):
        """SERVER-REGISTERED sources only.

        `source_key` is looked up in a map the COLLECTOR built; a client cannot name a path,
        and the realpath containment check is a second line of defence against a symlink that
        appeared under `.dev/` between registration and read.
        """
        import os

        snapshot = collector.snapshot()
        if key not in snapshot.details:
            return problem("unknown_session", "no live session for handle %s" % key)
        path = snapshot.sources.get(source_key)
        if path is None:
            return problem("unknown_object",
                           "source %s is not registered for this snapshot" % source_key)
        root = snapshot.details[key].identity.root
        real = os.path.realpath(path)
        dev = os.path.realpath(os.path.join(root, ".dev"))
        from .readers import contained
        if not source_key.startswith(key + ":") or not contained(root, path):
            return problem("not_actionable",
                           "source resolves outside %s after symlink resolution" % dev)
        from .readers import read_text

        result = read_text(real, cap=SOURCE_MAX_BYTES)
        if result.state != "ok":
            return problem("not_actionable", "%s: %s" % (result.state, result.detail))
        # text/plain, never text/html: the browser must not be handed a source file to
        # execute. The frontend renders it escaped as well.
        return PlainTextResponse(result.text or "", media_type="text/plain; charset=utf-8")

    @app.get("/api/operations/{operation_id}", response_model=OperationReceipt)
    def get_operation(operation_id: str) -> OperationReceipt:
        return runner.operation(operation_id)

    # ── mutations ────────────────────────────────────────────────────────────

    def _session(key: str):
        snapshot = collector.snapshot()
        detail = snapshot.details.get(key)
        if detail is None:
            raise ActionRefused("unknown_session", "no live session for handle %s" % key)
        return snapshot, detail

    def _revision_guard(snapshot, revision: int) -> None:
        """A displayed revision from the FUTURE is a client bug; an older one is normal (the
        operator composed for a while). Only the impossible case is refused."""
        if revision > snapshot.revision:
            raise ActionRefused(
                "stale_revision",
                "the request cites revision %d but this server has only produced %d"
                % (revision, snapshot.revision),
            )

    def _pending_plan(action: str, detail, body, handles_key: str, note_or_reason: str) -> Plan:
        ref = handles.resolve(handles_key, expect_kind="pending")
        identity = detail.identity
        slug = ref.payload["slug"]
        stage = ref.payload["stage"]
        pending_ts = ref.payload["timestamp"]
        if ref.session_key != identity.key:
            raise ActionRefused("object_changed", "pending handle belongs to another session")
        callback_id, callback_sha = ref.payload["callback_id"], ref.payload["callback_sha"]
        if callback_id == "ambiguous":
            raise ActionRefused("object_changed", "callback selection is ambiguous")

        def revalidate() -> tuple[str, str]:
            from .readers import read_log_entries, callback_binding
            entries, clean, result = read_log_entries(identity.root, slug)
            matches = [e for e in entries if str(e.get("timestamp") or "") == pending_ts
                and e.get("session") == identity.name
                and str(e.get("incarnation") or "") == identity.created]
            if not clean or result.state != "ok" or len(matches) != 1:
                return "gone", ""
            entry = matches[0]
            cid, sha = callback_binding(identity.root, slug, entry, identity.name, identity.created, pending_entries=entries)
            version = "%s|%s|%s|%s|%s" % (entry.get("response") is None,
                str(entry.get("parked_at") or ""), str(entry.get("prompt_sha256") or ""), cid, sha)
            return version, HandleTable.compute_key(
                "pending", identity.key, "%s/%s" % (slug, pending_ts), version)

        if action == "park":
            argv = runner.commands.park(
                identity.name, identity.root, identity.session_id, identity.created,
                slug=slug, stage=stage, reason=note_or_reason, pending_ts=pending_ts,
                callback_id=callback_id, callback_sha=callback_sha,
            )
        else:
            argv = runner.commands.resolve(
                identity.name, identity.root, identity.session_id, identity.created,
                slug=slug, stage=stage, note=note_or_reason, pending_ts=pending_ts,
                callback_id=callback_id, callback_sha=callback_sha,
            )
        return Plan(
            action=action, argv=argv, cwd=identity.root, lock_key=identity.key,
            lock_kind="session", session_name=identity.name, session_id=identity.session_id,
            session_created=identity.created, session_root=identity.root, object_ref=ref,
            revalidate_object=revalidate,
        )

    @app.post("/api/sessions/{key}/actions/message", response_model=OperationReceipt)
    async def post_message(key: str, payload: MessageRequest) -> OperationReceipt:
        snapshot, detail = _session(key)
        _revision_guard(snapshot, payload.revision)
        identity = detail.identity

        def factory() -> Plan:
            return Plan(
                action="message",
                argv=runner.commands.message(identity.name, identity.root, identity.session_id,
                                             identity.created, payload.text, None),
                cwd=identity.root, lock_key=identity.key, lock_kind="session",
                session_name=identity.name, session_id=identity.session_id,
                session_created=identity.created, session_root=identity.root,
            )

        return await runner.submit("message", payload.model_dump(), factory, key)

    @app.post("/api/sessions/{key}/actions/answer", response_model=OperationReceipt)
    async def post_answer(key: str, payload: AnswerRequest) -> OperationReceipt:
        snapshot, detail = _session(key)
        _revision_guard(snapshot, payload.revision)
        identity = detail.identity
        ref = handles.resolve(payload.ask_key, expect_kind="ask")
        if ref.session_key != identity.key:
            raise ActionRefused("object_changed", "ask handle belongs to another session")
        card = next((a for a in detail.asks if a.object_key == payload.ask_key), None)
        if card is not None and not card.actionable:
            raise ActionRefused("not_actionable", card.ownership_note)

        def revalidate() -> tuple[str, str]:
            from .readers import read_json

            record = read_json(ref.payload["path"])
            if record.state != "ok" or not isinstance(record.data, dict):
                return "gone", ""
            version = "%s|%s" % (
                str(record.data.get("question_sha256") or ""),
                str(record.data.get("answered_at") or ""),
            )
            return version, HandleTable.compute_key("ask", identity.key,
                                                    ref.natural_id, version)

        def factory() -> Plan:
            return Plan(
                action="answer",
                argv=runner.commands.message(identity.name, identity.root, identity.session_id,
                                             identity.created, payload.text, ref.payload["ask_id"]),
                cwd=identity.root, lock_key=identity.key, lock_kind="session",
                session_name=identity.name, session_id=identity.session_id,
                session_created=identity.created, session_root=identity.root,
                object_ref=ref, answers_ask_id=ref.payload["ask_id"],
                revalidate_object=revalidate,
            )

        return await runner.submit("answer", payload.model_dump(), factory, key)

    async def _control(key: str, payload: ControlRequest) -> OperationReceipt:
        snapshot, detail = _session(key)
        _revision_guard(snapshot, payload.revision)
        identity = detail.identity

        def factory() -> Plan:
            return Plan(
                action=payload.control,
                argv=runner.commands.control(identity.name, identity.root, identity.session_id,
                                             identity.created, payload.control),
                cwd=identity.root, lock_key=identity.key, lock_kind="session",
                session_name=identity.name, session_id=identity.session_id,
                session_created=identity.created, session_root=identity.root,
            )

        return await runner.submit(payload.control, payload.model_dump(), factory, key)

    @app.post("/api/sessions/{key}/actions/pause", response_model=OperationReceipt)
    async def post_pause(key: str, payload: ControlRequest = Body(...)) -> OperationReceipt:
        payload.control = "pause"
        return await _control(key, payload)

    @app.post("/api/sessions/{key}/actions/stop", response_model=OperationReceipt)
    async def post_stop(key: str, payload: ControlRequest = Body(...)) -> OperationReceipt:
        payload.control = "stop"
        return await _control(key, payload)

    @app.post("/api/sessions/{key}/actions/ack", response_model=OperationReceipt)
    async def post_ack(key: str, payload: AckRequest) -> OperationReceipt:
        import os

        snapshot, detail = _session(key)
        _revision_guard(snapshot, payload.revision)
        identity = detail.identity
        if payload.scope == "session":
            target = identity.name
        elif payload.scope == "root":
            target = identity.root
        elif payload.scope == "root_basename":
            target = os.path.basename(identity.root.rstrip("/"))
        else:
            if payload.slug_key:
                ref = handles.resolve(payload.slug_key, expect_kind="pending")
                if ref.session_key != identity.key:
                    raise ActionRefused("object_changed", "slug handle belongs to another session")
                target = ref.payload["slug"]
            else:
                target = detail.stage.active_pipeline or ""
                from .readers import valid_slug
                if not valid_slug(target):
                    raise ActionRefused("not_actionable", "no active pipeline is available")
        fleet_roots = {card.identity.root for card in snapshot.fleet.sessions}
        scope = ack_scope(collector.board(), target, payload.scope, fleet_roots)
        if not scope.available:
            raise ActionRefused("not_actionable",
                                "ack is disabled: %s" % scope.unavailable_reason)

        def factory() -> Plan:
            return Plan(
                action="ack", argv=runner.commands.ack(target), cwd=identity.root,
                ack_visible={(row.condition, row.msg) for row in scope.visible},
                lock_key=identity.key, lock_kind="session", session_name=identity.name,
                session_id=identity.session_id, session_created=identity.created,
                session_root=identity.root,
            )

        return await runner.submit("ack", payload.model_dump(), factory, key)

    @app.post("/api/sessions/{key}/actions/park", response_model=OperationReceipt)
    async def post_park(key: str, payload: ParkRequest) -> OperationReceipt:
        snapshot, detail = _session(key)
        _revision_guard(snapshot, payload.revision)
        return await runner.submit(
            "park", payload.model_dump(),
            lambda: _pending_plan("park", detail, payload, payload.pending_key, payload.reason),
            key,
        )

    @app.post("/api/sessions/{key}/actions/resolve", response_model=OperationReceipt)
    async def post_resolve(key: str, payload: ResolveRequest) -> OperationReceipt:
        snapshot, detail = _session(key)
        _revision_guard(snapshot, payload.revision)
        return await runner.submit(
            "resolve", payload.model_dump(),
            lambda: _pending_plan("resolve", detail, payload, payload.pending_key, payload.note),
            key,
        )

    @app.post("/api/spawn", response_model=OperationReceipt)
    async def post_spawn(payload: SpawnRequest) -> OperationReceipt:
        snapshot = collector.snapshot()
        _revision_guard(snapshot, payload.revision)
        ref = handles.resolve(payload.root_key, expect_kind="root")
        root = ref.payload["root"]

        def factory() -> Plan:
            return Plan(
                action="spawn", argv=runner.commands.spawn(root, payload.name), cwd=root,
                lock_key=root, lock_kind="root",
            )

        return await runner.submit("spawn", payload.model_dump(), factory, None)

    @app.post("/api/refresh")
    def post_refresh() -> dict[str, Any]:
        """Coalesced refresh request. It does NOT bypass the cooldown or the budget; it only
        renews the client lease and records that a refresh is pending."""
        collector.request_refresh()
        return {"coalesced": sampler.coalesced_requests,
                "next_attempt_in_s": max(0.0, sampler.state.next_attempt_at - time.monotonic())}

    # ── static, declared LAST so nothing shadows /api ────────────────────────
    if (cfg.frontend_dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(cfg.frontend_dist), html=True), name="app")

    app.state.collector = collector
    app.state.runner = runner
    app.state.handles = handles
    app.state.receipts = receipts
    app.state.sampler = sampler
    app.state.config = cfg
    return app