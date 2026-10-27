from __future__ import annotations

from app.storage.store import ConversationStore


def test_session_lifecycle(tmp_path) -> None:
    store = ConversationStore(tmp_path / "test.db")
    session = store.create_session()
    assert session["id"]
    assert session["title"] == "新对话"

    store.add_message(session["id"], "user", "我身高175cm，体重80kg，想减脂")
    store.add_message(session["id"], "assistant", "建议先控制热量缺口。")

    messages = store.get_messages(session["id"])
    assert [message["role"] for message in messages] == ["user", "assistant"]

    updated = store.get_session(session["id"])
    assert updated["title"].startswith("我身高175cm")

    sessions = store.list_sessions()
    assert sessions[0]["message_count"] == 2

    assert store.delete_session(session["id"]) is True
    assert store.get_session(session["id"]) is None
    assert store.get_messages(session["id"]) == []


def test_history_limit_returns_latest_messages(tmp_path) -> None:
    store = ConversationStore(tmp_path / "test.db")
    session = store.create_session()
    for index in range(5):
        store.add_message(session["id"], "user", f"消息 {index}")

    messages = store.get_messages(session["id"], limit=2)
    assert [message["content"] for message in messages] == ["消息 3", "消息 4"]


def test_unknown_session_raises_key_error(tmp_path) -> None:
    store = ConversationStore(tmp_path / "test.db")
    try:
        store.add_message("missing", "user", "hello")
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("应当抛出 KeyError")


def test_clear_all_sessions_cascades_messages(tmp_path) -> None:
    store = ConversationStore(tmp_path / "test.db")
    first = store.create_session()
    second = store.create_session()
    store.add_message(first["id"], "user", "第一条")
    store.add_message(second["id"], "user", "第二条")

    assert store.clear_all_sessions() == 2
    assert store.list_sessions() == []
    assert store.get_messages(first["id"]) == []
    assert store.get_messages(second["id"]) == []
