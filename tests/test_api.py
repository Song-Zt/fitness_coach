from __future__ import annotations

import pytest


pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import AIMessageChunk  # noqa: E402

from app.core.agent import Settings  # noqa: E402
from app.web.api import create_app  # noqa: E402


class FakeAgent:
    def stream(self, payload, stream_mode=None):
        yield ("messages", (AIMessageChunk(content="你好，"), {}))
        yield ("messages", (AIMessageChunk(content="我是健身教练。"), {}))
        yield ("updates", {})

    async def astream(self, payload, stream_mode=None):
        for item in self.stream(payload, stream_mode):
            yield item


def make_settings(db_path) -> Settings:
    return Settings(
        model="test-model",
        base_url="http://localhost",
        api_key="test-key",
        db_path=db_path,
    )


def make_client(db_path) -> TestClient:
    app = create_app(
        make_settings(db_path),
        agent_factory=lambda settings: FakeAgent(),
    )
    return TestClient(app)


def test_health(tmp_path) -> None:
    client = make_client(tmp_path / "test.db")
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_page(tmp_path) -> None:
    client = make_client(tmp_path / "test.db")
    response = client.get("/")
    assert response.status_code == 200
    assert "AI Fitness Coach" in response.text


def test_chat_stream_persists_messages(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    app = create_app(make_settings(db_path), agent_factory=lambda settings: FakeAgent())
    client = TestClient(app)

    with client.stream("POST", "/api/chat", json={"message": "你好"}) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: meta" in body
    assert "event: token" in body
    assert "event: done" in body

    sessions = client.get("/api/sessions").json()["sessions"]
    assert len(sessions) == 1
    session_id = sessions[0]["id"]
    detail = client.get(f"/api/sessions/{session_id}").json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]


def test_chat_rejects_unknown_session(tmp_path) -> None:
    client = make_client(tmp_path / "test.db")
    response = client.post("/api/chat", json={"message": "你好", "session_id": "missing"})
    assert response.status_code == 404


def test_delete_session(tmp_path) -> None:
    client = make_client(tmp_path / "test.db")
    session = client.post("/api/sessions", json={"title": "测试"}).json()
    assert client.delete(f"/api/sessions/{session['id']}").status_code == 200
    assert client.get(f"/api/sessions/{session['id']}").status_code == 404


def test_clear_all_sessions(tmp_path) -> None:
    client = make_client(tmp_path / "test.db")
    client.post("/api/sessions", json={"title": "测试 1"})
    client.post("/api/sessions", json={"title": "测试 2"})

    response = client.delete("/api/sessions")
    assert response.status_code == 200
    assert response.json()["deleted"] == 2
    assert client.get("/api/sessions").json()["sessions"] == []
