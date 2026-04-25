"""Section 8: chat API persistence + SSE stream frames."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_create_session_returns_uuid(client):
    r = client.post("/chat")
    assert r.status_code == 201
    body = r.json()
    assert "session_id" in body and len(body["session_id"]) >= 32


def test_list_sessions_includes_created(client):
    created = client.post("/chat").json()
    listing = client.get("/chat").json()
    assert any(s["session_id"] == created["session_id"] for s in listing)


def test_post_message_streams_and_persists(client):
    sid = client.post("/chat").json()["session_id"]

    # The stream should yield user_message_persisted, message_complete, assistant_message_persisted
    # in fake mode (no tool calls). Content-Type is text/event-stream.
    with client.stream("POST", f"/chat/{sid}/message", json={"content": "Can I keep XOM?"}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        kinds = []
        for line in resp.iter_lines():
            if not line:
                continue
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            kinds.append(payload.get("kind"))

    assert "user_message_persisted" in kinds
    assert "message_complete" in kinds
    assert "assistant_message_persisted" in kinds

    # Both messages should now be on the session.
    msgs = client.get(f"/chat/{sid}").json()
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"]
    assert "Not investment advice" in msgs[1]["content"]


def test_delete_session_removes_messages(client):
    sid = client.post("/chat").json()["session_id"]
    client.post(f"/chat/{sid}/message", json={"content": "hi"}).read()
    deleted = client.delete(f"/chat/{sid}")
    assert deleted.status_code == 204
    after = client.get(f"/chat/{sid}")
    assert after.status_code == 404
