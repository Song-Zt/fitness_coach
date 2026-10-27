from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.core.streaming import (
    astream_agent_events,
    extract_final_text,
    extract_tool_calls,
    stream_agent_events,
)


class FakeAgent:
    def stream(self, payload, stream_mode=None):
        yield ("messages", (AIMessageChunk(content="你好"), {}))
        yield ("messages", (AIMessageChunk(content="，我是教练。"), {}))
        yield (
            "updates",
            {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "calculate_bmi",
                                    "args": {"height_cm": 175, "weight_kg": 80},
                                    "id": "call-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            },
        )
        yield (
            "updates",
            {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content='{"bmi": 26.1}',
                            tool_call_id="call-1",
                            name="calculate_bmi",
                        )
                    ]
                }
            },
        )


class FallbackAgent:
    def stream(self, payload, stream_mode=None):
        raise RuntimeError("streaming unsupported")
        yield  # pragma: no cover

    def invoke(self, payload):
        return {
            "messages": [
                AIMessage(content="回退回答"),
            ]
        }


class AsyncFakeAgent:
    async def astream(self, payload, stream_mode=None):
        for item in FakeAgent().stream(payload, stream_mode):
            yield item


class AsyncFallbackAgent:
    async def astream(self, payload, stream_mode=None):
        raise RuntimeError("streaming unsupported")
        yield  # pragma: no cover

    async def ainvoke(self, payload):
        return {"messages": [AIMessage(content="异步回退回答")]}


class PartialAgent:
    def stream(self, payload, stream_mode=None):
        yield (
            "updates",
            {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "calendar_draft_workout",
                                    "args": {"title": "测试"},
                                    "id": "call-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            },
        )
        raise RuntimeError("stream interrupted")

    def invoke(self, payload):
        raise AssertionError("已有工具事件时不应回退，否则会重复执行写操作")


async def _collect_async(agent) -> list[dict]:
    return [event async for event in astream_agent_events(agent, [("user", "你好")])]


def test_stream_agent_events() -> None:
    events = list(stream_agent_events(FakeAgent(), [("user", "你好")]))
    text = "".join(event["text"] for event in events if event["type"] == "token")
    assert text == "你好，我是教练。"
    assert any(event["type"] == "tool_call" for event in events)
    assert any(event["type"] == "tool_result" for event in events)


def test_stream_falls_back_to_invoke() -> None:
    events = list(stream_agent_events(FallbackAgent(), [("user", "你好")]))
    assert events == [{"type": "token", "text": "回退回答"}]


def test_async_stream_agent_events() -> None:
    events = asyncio.run(_collect_async(AsyncFakeAgent()))
    text = "".join(event["text"] for event in events if event["type"] == "token")
    assert text == "你好，我是教练。"
    assert any(event["type"] == "tool_call" for event in events)
    assert any(event["type"] == "tool_result" for event in events)


def test_async_stream_falls_back_to_ainvoke() -> None:
    events = asyncio.run(_collect_async(AsyncFallbackAgent()))
    assert events == [{"type": "token", "text": "异步回退回答"}]


def test_no_fallback_after_tool_event() -> None:
    events: list[dict] = []
    try:
        for event in stream_agent_events(PartialAgent(), [("user", "安排训练")]):
            events.append(event)
    except RuntimeError as exc:
        assert "stream interrupted" in str(exc)
    else:
        raise AssertionError("流中断后应当抛出异常")
    assert events[0]["type"] == "tool_call"


def test_extract_helpers() -> None:
    result = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "calculate_bmi", "args": {}, "id": "1", "type": "tool_call"}
                ],
            ),
            AIMessage(content="最终回答"),
        ]
    }
    assert extract_tool_calls(result) == ["calculate_bmi"]
    assert extract_final_text(result) == "最终回答"
