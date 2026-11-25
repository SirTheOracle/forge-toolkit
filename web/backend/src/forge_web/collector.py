"""One lifespan-owned collector with TWO INDEPENDENT LOOPS.

This split is the direct answer to R14's last requirement. The board scan costs seconds and
is rate-limited to a cooldown measured in tens of seconds; the tmux inventory, the file
readers and the feed cost milliseconds and must keep their 2 s cadence. One loop doing both
would make every inventory update wait behind a board scan — and under sustained contention,
behind a 60 s backoff.

    _fast_loop   every 2 s: tmux inventory, bounded file reads, feed cursors, projection.
                 Never runs `forge board`. Never blocks on the board loop.
    _board_loop  scheduled entirely by BoardSampler. Publishes into `_board` and nothing else.

They share only `_board` (a plain reference swap) and the snapshot publish, so neither can
stall the other. READ HANDLERS TOUCH NEITHER: they return the last published snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from . import readers
from .ack import ack_scope
from .config import Config, child_env
from .discovery import Inventory, LiveSession, collect, label_for, list_sessions, spawn_roots, unowned_roots
from .feed import FeedState, LogCursor, RoundRobin, attention_entries, events_to_entries, response_for_dispatch, current_session_reply, structured_entries
from .handles import HandleTable
from .models import (
    AckScope,
    AskCard,
    AskTextState,
    BoardFreshness,
    BoardItem,
    FleetSnapshot,
    LabelSource,
    RootNotice,
    SessionCard,
    SessionDetailView,
    SessionIdentity,
    SourceState,
    SourceStatus,
    SpawnRoot,
)
from .projection import stage_facts
from .sampler import BoardOutcome, BoardSampler, MAX_SCAN_S

FAST_TICK_S = 2.0
BOARD_TICK_S = 0.25


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Snapshot:
    revision: int
    generated_at: str
    fleet: FleetSnapshot
    details: dict[str, SessionDetailView] = field(default_factory=dict)
    scopes: dict[str, AckScope] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)      # source_key -> absolute path


class Collector:
    def __init__(self, cfg: Config, *, handles: HandleTable, sampler: BoardSampler,
                 server_instance: str, mutations_enabled: bool, mutations_detail: str) -> None:
        self.cfg = cfg
        self.handles = handles
        self.sampler = sampler
        self.server_instance = server_instance
        self.mutations_enabled = mutations_enabled
        self.mutations_detail = mutations_detail
        self._revision = 0
        self._board: dict[str, Any] | None = None
        self._board_fresh = BoardFreshness()
        self._feeds: dict[str, FeedState] = {}
        self._rr = RoundRobin()
        self._log_cache: dict[str, dict[str, Any]] = {}
        self._ask_cache: dict[str, dict[str, Any]] = {}
        self._last_reads: dict[tuple[str, str], readers.ReadResult] = {}
        self._projected: dict[str, Any] = {}
        self._project_index = 0
        self._last_success_mono: float | None = None
        self._snapshot = Snapshot(
            revision=0,
            generated_at=_now_iso(),
            fleet=FleetSnapshot(
                revision=0, generated_at=_now_iso(), server_instance=server_instance,
                mutations_enabled=mutations_enabled, mutations_detail=mutations_detail,
                board=BoardFreshness(),
            ),
        )
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._fast_loop(), name="forge-web-fast"),
            asyncio.create_task(self._board_loop(), name="forge-web-board"),
        ]

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    # -- reads ---------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        self.sampler.note_client_read(time.monotonic())
        if self._last_success_mono is not None:
            self._snapshot.fleet.board.last_success_age_s = time.monotonic() - self._last_success_mono
        return self._snapshot

    def request_refresh(self) -> None:
        self.sampler.request_refresh(time.monotonic())

    def board(self) -> dict[str, Any] | None:
        """The last GOOD board payload, or None if there has never been one. Read-only;
        callers must not mutate it."""
        return self._board

    async def live_identity(self, name: str) -> dict[str, str] | None:
        """A FRESH tmux probe for the revalidation step. Deliberately not the cached
        inventory: revalidation exists precisely because the cache may be two seconds old."""
        loop = asyncio.get_running_loop()
        sessions, state, _ = await loop.run_in_executor(None, list_sessions, self.cfg)
        if state != "ok":
            return None
        for session in sessions:
            if session.name == name:
                return {
                    "name": session.name,
                    "root": session.root,
                    "session_id": session.session_id,
                    "created": session.created,
                }
        return None

    # -- loops ---------------------------------------------------------------

    async def _fast_loop(self) -> None:
        while not self._stopping.is_set():
            started = time.monotonic()
            try:
                loop = asyncio.get_running_loop()
                inventory = await loop.run_in_executor(None, collect, self.cfg)
                await loop.run_in_executor(None, self._publish, inventory)
            except Exception as exc:                       # noqa: BLE001 - never die
                self._snapshot.fleet.tmux_detail = "collection error: %s" % exc
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.0, FAST_TICK_S - elapsed))

    async def _board_loop(self) -> None:
        while not self._stopping.is_set():
            now = time.monotonic()
            allowed, reason = self.sampler.should_start(now)
            if not allowed:
                self._board_fresh.collection_state = (
                    "suspended" if "suspended" in reason else self._board_fresh.collection_state
                )
                self._board_fresh.next_attempt_in_s = max(
                    0.0, self.sampler.state.next_attempt_at - now
                )
                await asyncio.sleep(BOARD_TICK_S)
                continue
            self.sampler.started(now)
            outcome, duration, payload, rc = await self._scan_board()
            delay = self.sampler.record(outcome, duration, time.monotonic())
            self._apply_board(outcome, payload, rc, delay)

    async def _scan_board(self) -> tuple[BoardOutcome, float, dict[str, Any] | None, int | None]:
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.cfg.board_cmd,
                cwd=str(self.cfg.checkout),
                env=child_env(self.cfg),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            return BoardOutcome.ERROR, time.monotonic() - started, None, None
        total = 0
        async def capture(stream):
            nonlocal total
            chunks = []
            while True:
                block = await stream.read(65536)
                if not block:
                    return b"".join(chunks)
                total += len(block)
                if total > readers.SUBPROCESS_MAX:
                    raise ValueError("board output exceeds 4 MiB")
                chunks.append(block)

        async def reap():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            async def discard(stream):
                while await stream.read(65536):
                    pass
            await asyncio.gather(discard(proc.stdout), discard(proc.stderr))
            await proc.wait()

        jobs = [asyncio.create_task(capture(proc.stdout)), asyncio.create_task(capture(proc.stderr))]
        try:
            out, _err = await asyncio.wait_for(asyncio.gather(*jobs), timeout=MAX_SCAN_S)
            await proc.wait()
        except (asyncio.TimeoutError, ValueError) as exc:
            await reap()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            return (BoardOutcome.TIMEOUT if isinstance(exc, asyncio.TimeoutError) else BoardOutcome.INVALID,
                    time.monotonic() - started, None, None)
        except asyncio.CancelledError:
            await reap()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            raise
        duration = time.monotonic() - started
        rc = proc.returncode
        try:
            payload = json.loads(out.decode("utf-8", "replace"))
        except ValueError:
            payload = None
        if rc == 75 and isinstance(payload, dict) and payload.get("busy"):
            return BoardOutcome.BUSY, duration, payload, rc
        if rc != 0:
            return BoardOutcome.ERROR, duration, payload, rc
        if not isinstance(payload, dict) or payload.get("schema") != "cc-board/1":
            return BoardOutcome.INVALID, duration, None, rc
        return BoardOutcome.SUCCESS, duration, payload, rc

    def _apply_board(self, outcome: BoardOutcome, payload: dict[str, Any] | None,
                     rc: int | None, delay: float) -> None:
        fresh = self._board_fresh
        fresh.exit_code = rc
        fresh.last_attempt_at = _now_iso()
        fresh.next_attempt_in_s = delay
        if outcome is BoardOutcome.SUCCESS and payload is not None:
            self._board = payload
            self._last_success_mono = time.monotonic()
            fresh.schema_name = str(payload.get("schema") or "")
            fresh.generated_at = payload.get("generated_at")
            fresh.heartbeat = payload.get("heartbeat")
            fresh.heartbeat_age_s = payload.get("heartbeat_age_s")
            fresh.stale = payload.get("stale")
            fresh.busy = False
            fresh.unavailable_reason = None
            fresh.last_success_at = _now_iso()
            fresh.last_success_age_s = 0.0
            fresh.collection_state = "ok"
            return
        # BUSY / INVALID / TIMEOUT / ERROR all RETAIN the last good board, VISIBLY STALE.
        # No prior success means UNAVAILABLE, never "clear".
        if outcome is BoardOutcome.BUSY and payload is not None:
            fresh.busy = True
            # `stale` is deliberately ABSENT from a busy payload; do not invent a value.
            fresh.unavailable_reason = str(payload.get("unavailable_reason") or "state_lock_busy")
        else:
            fresh.busy = False
            fresh.unavailable_reason = {
                BoardOutcome.INVALID: "invalid_board_output",
                BoardOutcome.TIMEOUT: "board_scan_timeout",
                BoardOutcome.ERROR: "board_exit_%s" % rc,
            }.get(outcome, "unknown")
        fresh.collection_state = "unavailable" if self._board is None else (
            "busy" if fresh.busy else "stale"
        )
        if self.sampler.state.last_success_at is not None:
            fresh.last_success_age_s = time.monotonic() - self.sampler.state.last_success_at

    # -- projection ----------------------------------------------------------

    def _publish(self, inventory: Inventory) -> None:
        if inventory.state != "ok":
            self._snapshot.fleet.tmux_state = "unavailable"
            self._snapshot.fleet.tmux_detail = inventory.detail + "; retaining last successful inventory"
            self._snapshot.fleet.board = self._board_fresh.model_copy()
            return
        board = self._board or {}
        rows_by_root: dict[str, list[BoardItem]] = {}
        for key in ("hot", "active", "parked"):
            for row in board.get(key) or []:
                if not isinstance(row, dict):
                    continue
                item = BoardItem(
                    condition=str(row.get("condition") or ""),
                    row_class=str(row.get("class") or key),
                    state=row.get("state"),
                    msg=str(row.get("msg") or ""),
                    slug=row.get("slug"),
                    stage=row.get("stage"),
                    acked=bool(row.get("acked")),
                    session=row.get("session"),
                    incarnation=str(row["incarnation"]) if row.get("incarnation") is not None else None,
                    root=os.path.realpath(str(row.get("root") or "")),
                    label=row.get("label"),
                )
                rows_by_root.setdefault(item.root, []).append(item)

        cards: list[SessionCard] = []
        details: dict[str, SessionDetailView] = {}
        scopes: dict[str, AckScope] = {}
        sources: dict[str, str] = {}
        fleet_roots = {s.root for s in inventory.sessions}

        feed_keys = [s.root + s.identity_version for s in inventory.sessions]
        due = set(self._rr.take(feed_keys))

        started = time.monotonic()
        sessions = inventory.sessions
        ordered = sessions[self._project_index:] + sessions[:self._project_index]
        refreshed = 0
        for session in ordered:
            cache_key = session.root + session.identity_version
            if refreshed >= 8 or (refreshed and time.monotonic() - started > 0.25):
                cached = self._projected.get(cache_key)
                if cached is not None:
                    identity, prior_card, prior_detail, scope, source_map = cached
                    card = prior_card.model_copy(deep=True)
                    detail = prior_detail.model_copy(deep=True)
                    for source in card.sources:
                        source.state = SourceState.STALE
                        source.detail = "Collection budget deferred this source; last good retained"
                    detail.sources = card.sources
                    cards.append(card)
                    details[identity.key] = detail
                    scopes[identity.key] = scope
                    sources.update(source_map)
                continue
            refreshed += 1
            identity, card, detail, scope, source_map = self._project_session(
                session, inventory, [r for r in rows_by_root.get(session.root, [])
                    if r.session == session.name and (not r.incarnation or r.incarnation == session.created)],
                refresh_feed=(session.root + session.identity_version) in due,
                fleet_roots=fleet_roots, board=board,
            )
            self._projected[cache_key] = (identity, card, detail, scope, source_map)
            cards.append(card)
            details[identity.key] = detail
            scopes[identity.key] = scope
            sources.update(source_map)

        self._project_index = (self._project_index + refreshed) % max(1, len(sessions))
        keep_projection = {s.root + s.identity_version for s in sessions}
        self._projected = {k: v for k, v in self._projected.items() if k in keep_projection}
        notices = [
            RootNotice(
                root=root,
                label=label_for(root, inventory.registry)[0],
                detail="Sessionless project evidence; no owning session is established and no session action is offered.",
                board_items=[r for r in rows_by_root.get(root, []) if not r.session],
            )
            for root in rows_by_root if any(not r.session for r in rows_by_root[root])
        ]

        # Evict departed sessions ONLY after a SUCCESSFUL inventory.
        if inventory.state == "ok":
            keep = {s.root + s.identity_version for s in inventory.sessions}
            for key in [k for k in self._feeds if k not in keep]:
                self._feeds.pop(key, None)

        live_handles = {HandleTable.compute_key("session", None, s.root, s.identity_version)
                        for s in inventory.sessions}
        self._last_reads = {k: v for k, v in self._last_reads.items() if k[0] in live_handles}
        self._log_cache = {k: v for k, v in self._log_cache.items() if k in live_handles}
        self._ask_cache = {k: v for k, v in self._ask_cache.items() if k in live_handles}
        self._revision += 1
        fleet = FleetSnapshot(
            revision=self._revision,
            generated_at=_now_iso(),
            server_instance=self.server_instance,
            mutations_enabled=self.mutations_enabled,
            mutations_detail=self.mutations_detail,
            tmux_state="ok" if inventory.state == "ok" else "unavailable",
            tmux_detail=inventory.detail,
            board=self._board_fresh.model_copy(),
            sessions=cards,
            root_notices=notices,
            spawn_roots=self._spawn_roots(inventory),
        )
        for card in cards:
            details[card.identity.key].revision = self._revision
        self._snapshot = Snapshot(
            revision=self._revision, generated_at=fleet.generated_at, fleet=fleet,
            details=details, scopes=scopes, sources=sources,
        )

    def _spawn_roots(self, inventory: Inventory) -> list[SpawnRoot]:
        """Issue a handle per offered root. The version is the root's own realpath, so a
        root keeps its key across polls and a client can never name a path."""
        live = {s.root for s in inventory.sessions}
        registered = {e.path for e in inventory.registry.values()}
        out: list[SpawnRoot] = []
        for root in spawn_roots(inventory):
            key = self.handles.issue("root", None, root, root, payload={"root": root})
            out.append(SpawnRoot(
                key=key, root=root, label=label_for(root, inventory.registry)[0],
                live=root in live, registered=root in registered,
            ))
        return out

    def _project_session(
        self, session: LiveSession, inventory: Inventory, rows: list[BoardItem],
        *, refresh_feed: bool, fleet_roots: set[str], board: dict[str, Any],
    ) -> tuple[SessionIdentity, SessionCard, SessionDetailView, AckScope, dict[str, str]]:
        label, label_source, alias, repo_id = label_for(session.root, inventory.registry)
        key = self.handles.issue(
            "session", None, session.root, session.identity_version,
            payload={"name": session.name, "root": session.root,
                     "session_id": session.session_id, "created": session.created},
        )
        identity = SessionIdentity(
            key=key, name=session.name, root=session.root, session_id=session.session_id,
            created=session.created, label=label, label_source=LabelSource(label_source),
            repo_alias=alias, repo_id=repo_id, has_dev=session.has_dev,
        )

        statuses: list[SourceStatus] = []
        source_map: dict[str, str] = {}

        def note(skey: str, label_text: str, result: readers.ReadResult) -> None:
            statuses.append(SourceStatus(
                key="%s:%s" % (key, skey), label=label_text,
                state=SourceState(result.state if result.state in
                                  {s.value for s in SourceState} else "malformed"),
                detail=result.detail, observed_at=result.observed_at,
                source_at=result.source_at, size_bytes=result.size_bytes,
                read_limit_hit=result.limit_hit,
            ))
            source_map["%s:%s" % (key, skey)] = result.path

        def retained(kind, result):
            cache_key = (key, kind)
            if result.state == "ok":
                self._last_reads[cache_key] = result
            elif result.state != "absent" and readers.safe_source(result.path) and cache_key in self._last_reads:
                old = self._last_reads[cache_key]
                result.text, result.data = old.text, old.data
                result.state = "stale"
                result.detail += "; showing last successful read at " + old.observed_at
            elif result.state == "absent" or not readers.safe_source(result.path):
                self._last_reads.pop(cache_key, None)
            return result

        context = retained("context", readers.context_for(session.root, session.name))
        note("context", ".dev/forge-context.%s.yml" % session.name, context)
        status = retained("status", readers.status_for(session.root, session.name))
        note("status", ".dev/forge-status.%s.md" % session.name, status)
        usage = retained("usage", readers.usage_for(session.root, session.name))
        note("usage", ".dev/forge-usage.%s.yml" % session.name, usage)

        context_data = context.data if isinstance(context.data, dict) else {}
        active_slug = str(context_data.get("active_pipeline") or "") or None

        logs: dict[str, tuple[list[dict[str, Any]], bool]] = self._log_cache.setdefault(key, {})
        # Active slug plus rotating bounded proposals: no full history walk per tick.
        slug_names = readers.rotating_names(os.path.join(session.root, ".dev", "proposals"), 8, "logs:" + key)
        for slug in dict.fromkeys(([active_slug] if active_slug else []) + slug_names):
            entries, clean, result = readers.read_log_entries(session.root, slug)
            if result.state == "ok":
                logs[slug] = (entries, clean)
            elif result.state == "absent" or (result.path and not readers.safe_source(result.path)):
                logs.pop(slug, None)
            elif slug not in logs:
                logs[slug] = (entries, False)
            else:
                result.state = "stale"
                result.detail += "; retaining previously read log"
            if slug == active_slug:
                note("log", ".dev/proposals/%s/forge-log.yml" % slug, result)

        while len(logs) > 64:
            victim = next(k for k in logs if k != active_slug)
            logs.pop(victim)

        facts = stage_facts(
            session=session, session_key=key, handles=self.handles, logs=logs,
            context=context_data, board_rows=rows,
            status_excerpt=(status.text or "")[:2000] if status.text else None,
        )

        objective, objective_source = (None, None)
        if active_slug:
            objective, objective_source = readers.objective_excerpt(session.root, active_slug)

        created_epoch = int(session.created) if session.created.isdigit() else None
        ask_cache = self._ask_cache.setdefault(key, {})
        for record in readers.open_asks(session.root, session.name, created_epoch):
            ask_cache[record["_path"]] = record
        for path in list(ask_cache):
            if not os.path.isfile(path) or not readers.safe_source(path):
                ask_cache.pop(path)
        if len(ask_cache) > 200:
            ask_cache = dict(list(ask_cache.items())[-200:])
            self._ask_cache[key] = ask_cache
        asks = [self._ask_card(key, record) for record in ask_cache.values()]

        feed_key = session.root + session.identity_version
        state = self._feeds.get(feed_key)
        if state is None:
            state = FeedState(cursor=LogCursor(
                path=os.path.join(session.root, ".dev", "forge-tmp", "orchestrator-events.log")
            ))
            self._feeds[feed_key] = state
        if refresh_feed:
            lines = state.cursor.read_new()
            entries, omitted = events_to_entries(
                lines, state.cursor, name=session.name,
                session_id=session.session_id, created=session.created,
            )
            state.omitted_without_proof += omitted
            state.extend(entries)
            state.extend(attention_entries(
                session.root, name=session.name, created_epoch=created_epoch,
                seen=state.attention_seen,
            ))

            structured = []
            for record in readers.callbacks_for(session.root, None):
                if not record.get("_overflow") and not record.get("_invalid"):
                    structured.append(("callback:" + str(record.get("callback_id") or ""),
                                       {**record, "root": session.root}))
            for kind in ("tasks", "episodes"):
                for record in (board.get(kind) or [])[:200]:
                    source_identity = record.get("task_id" if kind == "tasks" else "episode_id") if isinstance(record, dict) else None
                    if source_identity and record.get("root"):
                        structured.append((kind + ":" + os.path.realpath(str(record["root"])) + ":" + str(source_identity), record))
            state.extend(structured_entries(session.root, name=session.name, created_epoch=created_epoch,
                         records=structured, seen=state.attention_seen))

        responses = []
        for entry in reversed(state.entries):
            did = entry.detail.get("dispatch_id")
            if not did:
                continue
            card = response_for_dispatch(session.root, session.name, did, created_epoch)
            if card is not None:
                responses.append(card)
            if len(responses) >= 20:
                break

        latest = current_session_reply(session.root, session.name, created_epoch)
        if latest and not any(r.dispatch_id in latest.attributed_dispatch_ids for r in responses):
            responses.append(latest)

        scopes = {kind: ack_scope(board or None, target, kind, fleet_roots)
            for kind, target in (("session", session.name), ("root", session.root),
                                 ("root_basename", os.path.basename(session.root)))}
        if active_slug:
            scopes['slug'] = ack_scope(board or None, active_slug, 'slug', fleet_roots)
        scope = scopes["session"]

        statuses.append(SourceStatus(key=key + ":collection-budget", label="Collection budget",
            state=SourceState.OK, detail="At most 8 sessions / 250 ms of completed work per tick; 8 proposal and 128 attention names per session. Large inventories rotate; cached sources may lag."))
        card = SessionCard(
            identity=identity, objective_excerpt=objective, objective_source=objective_source,
            stage=facts, attention_count=sum(1 for r in rows if not r.acked and r.row_class == "hot"), open_ask_count=sum(1 for a in asks if a.actionable),
            board_items=rows, sources=statuses,
            partial_startup=not session.has_dev,
        )
        detail = SessionDetailView(
            revision=self._revision, generated_at=_now_iso(), identity=identity,
            objective_excerpt=objective, objective_source=objective_source, stage=facts,
            asks=asks, board_items=rows, feed=list(reversed(state.entries[-200:])),
            feed_backlog=state.cursor.backlog, feed_backlog_detail=state.cursor.backlog_detail,
            responses=responses, sources=statuses,
            callbacks=facts.parked, ack_scope=scope, ack_scopes=scopes,
        )
        return identity, card, detail, scope, source_map

    def _ask_card(self, session_key: str, record: dict[str, Any]) -> AskCard:
        text, text_state, label, saved, full = readers.ask_text_projection(record)
        ask_id = str(record.get("ask_id") or "")
        object_key = self.handles.issue(
            "ask", session_key, ask_id,
            str(record.get("question_sha256") or "") + "|" + str(record.get("answered_at") or ""),
            payload={"ask_id": ask_id, "slug": str(record.get("slug") or ""),
                     "stage": str(record.get("stage") or ""),
                     "path": str(record.get("_path") or "")},
        )
        return AskCard(
            object_key=object_key, ask_id=ask_id, mode=str(record.get("mode") or ""),
            emitted_at=str(record.get("emitted_at") or ""),
            slug=record.get("slug"), stage=record.get("stage"), worker=record.get("worker"),
            snippet=str(record.get("question_snippet") or ""),
            question_text=text, text_state=AskTextState(text_state), text_label=label,
            saved_bytes=saved, full_redacted_bytes=full,
            actionable=bool(record.get("_actionable")),
            ownership_note=str(record.get("_ownership_note") or ""),
        )