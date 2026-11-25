"""Routes, containment, static mounting, and the no-subprocess-on-read invariant."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from forge_web.app import create_app

RID = "11111111-1111-4111-a111-111111111111"


@pytest.fixture
def client(checkout, tmp_path):
    root = tmp_path / "proj"
    (root / ".dev" / "proposals").mkdir(parents=True)
    (root / ".dev" / "attention" / "payloads").mkdir(parents=True)
    (root / ".dev" / "forge-context.forge-x.yml").write_text(
        "active_pipeline: slug\nlast_stage_completed: coding\nlast_stage_status: done\n"
        "next_stage: qa\nupdated_at: \"2026-09-14T10:00:00Z\"\nnotes: []\n"
    )
    (root / ".dev" / "forge-status.forge-x.md").write_text("# status\n")
    checkout.set_sessions([("forge-x", str(root), "$4", "1700000000")])
    with TestClient(create_app(checkout.cfg())) as test_client:
        test_client.forge_root = root                        # type: ignore[attr-defined]
        yield test_client


def wait_for_session(client):
    for _ in range(80):
        fleet = client.get("/api/fleet").json()
        if fleet["sessions"]:
            return fleet
        import time

        time.sleep(0.1)
    raise AssertionError("the collector never published a session")


def test_fleet_renders_the_session_with_its_physical_identity(client):
    fleet = wait_for_session(client)
    identity = fleet["sessions"][0]["identity"]
    assert identity["name"] == "forge-x"
    assert identity["session_id"] == "$4"
    assert fleet["revision"] >= 1
    assert fleet["server_instance"]


def test_the_session_detail_route_resolves_by_handle_only(client):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    assert client.get("/api/sessions/%s" % key).status_code == 200
    # A path, a name, or a guessed key are all simply unknown.
    for bogus in ("forge-x", "s-deadbeef", "%2e%2e%2f"):
        assert client.get("/api/sessions/%s" % bogus).status_code in (404, 422)


def test_read_handlers_never_spawn_a_subprocess(client, checkout):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    before = len(checkout.calls())
    for _ in range(5):
        client.get("/api/fleet")
        client.get("/api/sessions/%s" % key)
    assert len(checkout.calls()) == before, (
        "a GET must return the cached projection; only the COLLECTOR runs `forge board --json`"
    )


def test_the_collector_runs_board_and_nothing_else(client, checkout):
    wait_for_session(client)
    client.post("/api/refresh")
    import time

    time.sleep(1.0)
    verbs = {tuple(call[:3]) for call in checkout.calls()}
    for call in verbs:
        assert call[1] in ("board", "web-contract-version"), (
            "the collector called %r; it may only run `forge board --json`" % (call,)
        )


def test_a_source_must_be_server_registered(client):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    detail = client.get("/api/sessions/%s" % key).json()
    registered = detail["sources"][0]["key"]
    assert client.get("/api/sessions/%s/sources/%s" % (key, registered)).status_code == 200
    assert client.get("/api/sessions/%s/sources/%s" % (key, "made-up")).status_code == 404


def test_a_symlinked_source_escaping_dev_is_refused(client, tmp_path):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    secret = tmp_path / "secret"
    secret.write_text("not yours")
    status_path = client.forge_root / ".dev" / "forge-status.forge-x.md"
    status_path.unlink()
    status_path.symlink_to(secret)
    detail = client.get("/api/sessions/%s" % key).json()
    status_key = next(s["key"] for s in detail["sources"] if s["key"].endswith(":status"))
    response = client.get("/api/sessions/%s/sources/%s" % (key, status_key))
    assert response.status_code == 422
    assert "after symlink resolution" in response.json()["detail"]


def test_a_source_is_served_as_text_never_html(client):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    detail = client.get("/api/sessions/%s" % key).json()
    source_key = next(s["key"] for s in detail["sources"] if s["key"].endswith(":status"))
    response = client.get("/api/sessions/%s/sources/%s" % (key, source_key))
    assert response.headers["content-type"].startswith("text/plain")


def test_static_assets_do_not_shadow_the_api(checkout):
    dist = checkout.root / "web" / "frontend" / "dist"
    (dist / "index.html").write_text("<!doctype html><title>app</title>")
    with TestClient(create_app(checkout.cfg())) as client:
        assert client.get("/api/fleet").status_code == 200
        assert client.get("/api/fleet").headers["content-type"].startswith("application/json")
        assert "<title>app</title>" in client.get("/").text


def test_a_duplicate_post_returns_the_same_receipt(client):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    body = {"request_id": RID, "revision": fleet["revision"], "text": "hello"}
    first = client.post("/api/sessions/%s/actions/message" % key, json=body)
    second = client.post("/api/sessions/%s/actions/message" % key, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["operation_id"] == second.json()["operation_id"]
    assert second.json()["duplicate"] is True


def test_the_same_id_with_a_different_body_is_a_conflict(client):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    base = {"request_id": RID, "revision": fleet["revision"]}
    client.post("/api/sessions/%s/actions/message" % key, json={**base, "text": "one"})
    conflict = client.post("/api/sessions/%s/actions/message" % key, json={**base, "text": "two"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "request_id_conflict"


def test_a_revision_from_the_future_is_refused(client):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    response = client.post(
        "/api/sessions/%s/actions/message" % key,
        json={"request_id": RID, "revision": fleet["revision"] + 10_000, "text": "hi"},
    )
    assert response.status_code == 409 and response.json()["code"] == "stale_revision"


def test_an_unknown_operation_is_unknown_not_a_404(client):
    body = client.get("/api/operations/some-instance:000042").json()
    assert body["state"] == "unknown"


def test_the_lifespan_stops_the_collector(checkout):
    app = create_app(checkout.cfg())
    with TestClient(app):
        pass
    assert app.state.collector._tasks == []


def test_a_malformed_body_is_rejected_before_any_resolution(client):
    fleet = wait_for_session(client)
    key = fleet["sessions"][0]["identity"]["key"]
    for body in (
        {"request_id": "not-a-uuid", "revision": 1, "text": "x"},
        {"request_id": RID, "revision": 1},                          # missing text
        {"request_id": RID, "revision": 1, "text": "x", "cwd": "/"},  # extra="forbid"
    ):
        assert client.post("/api/sessions/%s/actions/message" % key,
                           data=json.dumps(body)).status_code == 422