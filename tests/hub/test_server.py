"""Integration tests for mclaude.hub.server via FastAPI TestClient."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(app_client: TestClient) -> None:
    resp = app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_and_get_project(app_client: TestClient) -> None:
    resp = app_client.post("/api/projects", json={"id": "proj-1", "name": "My Project"})
    assert resp.status_code == 201
    assert resp.json()["id"] == "proj-1"

    resp = app_client.get("/api/projects/proj-1")
    assert resp.status_code == 200
    assert resp.json()["name"] == "My Project"


def test_register_identity(app_client: TestClient) -> None:
    app_client.post("/api/projects", json={"id": "default", "name": "P"})
    resp = app_client.post("/api/identities", json={
        "name": "ani",
        "owner": "Anastasia",
        "roles": ["infra", "ml"],
        "notify": {"telegram": "123"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "ani"
    assert data["owner"] == "Anastasia"

    resp = app_client.get("/api/identities")
    assert resp.status_code == 200
    names = {i["name"] for i in resp.json()}
    assert "ani" in names


def test_session_start_heartbeat_end(app_client: TestClient) -> None:
    app_client.post("/api/projects", json={"id": "default", "name": "P"})
    app_client.post("/api/identities", json={"name": "ani"})

    resp = app_client.post("/api/sessions", json={"identity": "ani", "machine": "test-machine"})
    assert resp.status_code == 201
    session = resp.json()
    session_id = session["id"]

    resp = app_client.post(f"/api/sessions/{session_id}/heartbeat")
    assert resp.status_code == 200

    resp = app_client.get("/api/sessions")
    assert resp.status_code == 200
    assert any(s["id"] == session_id for s in resp.json())

    resp = app_client.post(f"/api/sessions/{session_id}/end")
    assert resp.status_code == 200

    resp = app_client.get("/api/sessions")
    assert not any(s["id"] == session_id for s in resp.json())


def test_lock_claim_and_conflict(app_client: TestClient) -> None:
    app_client.post("/api/projects", json={"id": "default", "name": "P"})

    # First claim succeeds
    resp = app_client.post("/api/locks/claim", json={
        "slug": "fix-auth",
        "session_id": "s1",
        "identity": "ani",
        "description": "First claim",
        "files": ["src/auth.py"],
    })
    assert resp.status_code == 200
    assert resp.json()["slug"] == "fix-auth"

    # Second claim collides
    resp = app_client.post("/api/locks/claim", json={
        "slug": "fix-auth",
        "session_id": "s2",
        "identity": "vasya",
        "description": "Conflict",
    })
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "already_held"
    assert detail["lock"]["session_id"] == "s1"

    # Owner releases
    resp = app_client.post("/api/locks/fix-auth/release", params={"session_id": "s1"})
    assert resp.status_code == 200

    # After release, new claim succeeds
    resp = app_client.post("/api/locks/claim", json={
        "slug": "fix-auth",
        "session_id": "s3",
        "identity": "petya",
    })
    assert resp.status_code == 200


def test_lock_release_wrong_session(app_client: TestClient) -> None:
    app_client.post("/api/projects", json={"id": "default", "name": "P"})
    app_client.post("/api/locks/claim", json={
        "slug": "work-1",
        "session_id": "owner",
        "identity": "ani",
    })
    resp = app_client.post("/api/locks/work-1/release", params={"session_id": "intruder"})
    assert resp.status_code == 404


def test_create_event_and_list(app_client: TestClient) -> None:
    app_client.post("/api/projects", json={"id": "default", "name": "P"})
    resp = app_client.post("/api/events", json={
        "type": "message_question",
        "from_identity": "ani",
        "to_identity": "vasya",
        "subject": "Test question",
        "body": "Hello",
    })
    assert resp.status_code == 201
    event = resp.json()
    assert event["subject"] == "Test question"

    resp = app_client.get("/api/events", params={"to_identity": "vasya"})
    assert resp.status_code == 200
    events = resp.json()
    assert any(e["subject"] == "Test question" for e in events)


def test_create_event_invalid_type(app_client: TestClient) -> None:
    app_client.post("/api/projects", json={"id": "default", "name": "P"})
    resp = app_client.post("/api/events", json={
        "type": "not_a_real_type",
        "from_identity": "ani",
    })
    assert resp.status_code == 422


def test_websocket_subscribe_and_receive(app_client: TestClient) -> None:
    app_client.post("/api/projects", json={"id": "default", "name": "P"})

    with app_client.websocket_connect("/ws") as ws:
        # Auth handshake (anonymous mode)
        ws.send_json({"project_id": "default"})
        ack = ws.receive_json()
        assert ack["status"] == "subscribed"

        # Now create an event via REST - it should arrive via WS
        import threading
        import time

        received: list[dict] = []

        def collect() -> None:
            try:
                msg = ws.receive_json()
                received.append(msg)
            except Exception:
                pass

        t = threading.Thread(target=collect, daemon=True)
        t.start()

        # Give the subscriber a tick
        time.sleep(0.05)

        resp = app_client.post("/api/events", json={
            "type": "notify_task_complete",
            "from_identity": "ani",
            "subject": "tests passed",
        })
        assert resp.status_code == 201

        t.join(timeout=2)
        assert len(received) == 1
        assert received[0]["type"] == "notify_task_complete"
        assert received[0]["subject"] == "tests passed"
