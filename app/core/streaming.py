"""把 LangGraph Agent 的流式输出转换为统一的 SSE 事件。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _events_from_updates(data: dict) -> tuple[list[dict], str]:
    """从 LangGraph 的 updates 事件提取工具调用、工具结果和回退文本。"""
    events: list[dict] = []
    fallback_text = ""
    for update in data.values():
        if not isinstance(update, dict):
            continue
        for message in update.get("messages", []) or []:
            if isinstance(message, AIMessage) and not isinstance(message, AIMessageChunk):
                text = message_text(message.content)
                if text:
                    fallback_text = text
                for tool_call in message.tool_calls or []:
                    events.append(
                        {
                            "type": "tool_call",
                            "name": tool_call.get("name", ""),
                            "args": tool_call.get("args", {}),
                        }
                    )
            elif isinstance(message, ToolMessage):
                events.append(
                    {
                        "type": "tool_result",
                        "name": message.name or "",
                        "content": message_text(message.content)[:2000],
                    }
                )
    return events, fallback_text


def stream_agent_events(agent, messages: list[tuple[str, str]]) -> Iterator[dict]:
    """优先 token 流式输出；模型不支持流式时回退到一次性 invoke。"""
    emitted_text = False
    emitted_event = False
    fallback_text = ""
    try:
        for item in agent.stream(
            {"messages": messages},
            stream_mode=["messages", "updates"],
        ):
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            mode, data = item

            if mode == "messages":
                chunk, _metadata = data
                if isinstance(chunk, (AIMessage, AIMessageChunk)):
                    text = message_text(chunk.content)
                    if text:
                        emitted_text = True
                        emitted_event = True
                        yield {"type": "token", "text": text}

            elif mode == "updates" and isinstance(data, dict):
                events, text = _events_from_updates(data)
                if text:
                    fallback_text = text
                    emitted_event = True
                if events:
                    emitted_event = True
                yield from events
        if not emitted_text and fallback_text:
            yield {"type": "token", "text": fallback_text}
    except Exception:
        if emitted_event:
            raise
        result = agent.invoke({"messages": messages})
        yield from _events_from_result(result)


async def astream_agent_events(
    agent, messages: list[tuple[str, str]]
) -> AsyncIterator[dict]:
    """异步版事件流，支持 LangChain MCP 提供的异步工具。"""
    emitted_text = False
    emitted_event = False
    fallback_text = ""
    try:
        async for item in agent.astream(
            {"messages": messages},
            stream_mode=["messages", "updates"],
        ):
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            mode, data = item

            if mode == "messages":
                chunk, _metadata = data
                if isinstance(chunk, (AIMessage, AIMessageChunk)):
                    text = message_text(chunk.content)
                    if text:
                        emitted_text = True
                        emitted_event = True
                        yield {"type": "token", "text": text}

            elif mode == "updates" and isinstance(data, dict):
                events, text = _events_from_updates(data)
                if text:
                    fallback_text = text
                    emitted_event = True
                if events:
                    emitted_event = True
                for event in events:
                    yield event
        if not emitted_text and fallback_text:
            yield {"type": "token", "text": fallback_text}
    except Exception:
        if emitted_event:
            raise
        result = await agent.ainvoke({"messages": messages})
        for event in _events_from_result(result):
            yield event


def _events_from_result(result: dict) -> Iterator[dict]:
    messages = result.get("messages", [])
    for message in messages:
        if isinstance(message, AIMessage) and not isinstance(message, AIMessageChunk):
            for tool_call in message.tool_calls or []:
                yield {
                    "type": "tool_call",
                    "name": tool_call.get("name", ""),
                    "args": tool_call.get("args", {}),
                }
        elif isinstance(message, ToolMessage):
            yield {
                "type": "tool_result",
                "name": message.name or "",
                "content": message_text(message.content)[:2000],
            }
    final_text = extract_final_text(result)
    if final_text:
        yield {"type": "token", "text": final_text}


def extract_final_text(result: dict) -> str:
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage) and not isinstance(message, AIMessageChunk):
            text = message_text(message.content)
            if text:
                return text
    return ""


def extract_tool_calls(result: dict) -> list[str]:
    names: list[str] = []
    for message in result.get("messages", []):
        if isinstance(message, AIMessage) and not isinstance(message, AIMessageChunk):
            names.extend(tool_call.get("name", "") for tool_call in message.tool_calls or [])
    return names


def extract_tool_trajectory(result: dict) -> list[dict]:
    """提取工具名和参数，供评测校验日期、时长、草稿 ID 等行为。"""
    trajectory: list[dict] = []
    for message in result.get("messages", []):
        if isinstance(message, AIMessage) and not isinstance(message, AIMessageChunk):
            for tool_call in message.tool_calls or []:
                trajectory.append(
                    {
                        "name": tool_call.get("name", ""),
                        "args": tool_call.get("args", {}),
                    }
                )
    return trajectory
