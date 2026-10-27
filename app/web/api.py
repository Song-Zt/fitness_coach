"""FastAPI 服务：会话管理 + SSE 流式对话。"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.agent import PROJECT_DIR, Settings, create_fitness_agent
from app.core.streaming import astream_agent_events
from app.storage.store import ConversationStore

STATIC_DIR = PROJECT_DIR / "static"
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class SessionCreateRequest(BaseModel):
    title: str = Field(default="新对话", max_length=60)


def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _error_hint(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "authentication" in name or "401" in text or "invalid api key" in text:
        return "认证失败：请检查 .env 中的 DEEPSEEK_API_KEY 是否正确、是否已过期。"
    if "ratelimit" in name or "429" in text:
        return "请求频率或额度受限：请稍后重试，或检查账户余额。"
    if "notfound" in name or "404" in text:
        return "模型或接口地址不存在：请检查 .env 中的 MODEL 和 DEEPSEEK_BASE_URL。"
    if "connection" in name or "timeout" in name or "connect" in text:
        return (
            "模型服务连接失败：请检查网络、代理、DEEPSEEK_BASE_URL，"
            "或确认当前网络能访问模型服务。"
        )
    if "mcp" in name or "mcp" in text or "stdio" in text:
        return (
            "MCP 工具调用失败：请确认已安装 mcp 与 langchain-mcp-adapters，"
            "并检查日历/天气 Server 的日志。"
        )
    return "请查看后端日志，并检查 .env 配置。"


def create_app(
    settings: Settings | None = None,
    agent_factory: Callable = create_fitness_agent,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = ConversationStore(settings.db_path)

    async def get_agent():
        if app.state.agent is None:
            async with app.state.agent_lock:
                if app.state.agent is None:
                    try:
                        result = agent_factory(settings)
                        if inspect.isawaitable(result):
                            result = await result
                        app.state.agent = result
                        app.state.agent_error = None
                    except Exception as exc:
                        app.state.agent_error = str(exc)
                        raise
        return app.state.agent

    @asynccontextmanager
    async def lifespan(_app):
        try:
            await get_agent()
        except Exception as exc:
            logger.warning("Agent 初始化失败: %s", exc)
        yield

    app = FastAPI(title="AI Fitness Coach", version="1.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.agent = None
    app.state.agent_error = None
    app.state.agent_lock = asyncio.Lock()

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "model": settings.model or "unset",
            "configured": all([settings.model, settings.base_url, settings.api_key]),
            "agent_ready": app.state.agent is not None,
            "agent_error": app.state.agent_error,
            "mcp_enabled": settings.mcp_enabled,
            "weather_offline": settings.weather_offline,
            "sessions": len(store.list_sessions(limit=1000)),
        }

    @app.get("/api/config")
    def config() -> dict:
        return {
            "model": settings.model or "unset",
            "history_limit": settings.history_limit,
            "mcp_enabled": settings.mcp_enabled,
            "timezone": settings.timezone,
            "weather_offline": settings.weather_offline,
        }

    @app.post("/api/sessions")
    def create_session(body: SessionCreateRequest) -> dict:
        return store.create_session(body.title)

    @app.get("/api/sessions")
    def list_sessions(limit: int = 50) -> dict:
        return {"sessions": store.list_sessions(limit=max(1, min(limit, 200)))}

    @app.delete("/api/sessions")
    def clear_sessions() -> dict:
        return {"deleted": store.clear_all_sessions()}

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"session": session, "messages": store.get_messages(session_id)}

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str) -> dict:
        if not store.delete_session(session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"deleted": session_id}

    @app.post("/api/chat")
    async def chat(body: ChatRequest) -> StreamingResponse:
        message = body.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="消息不能为空")

        if body.session_id:
            session = store.get_session(body.session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="会话不存在")
            session_id = body.session_id
        else:
            session = store.create_session(message[:30])
            session_id = session["id"]

        try:
            agent = await get_agent()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Agent 初始化失败: {exc}") from exc

        history = store.get_messages(session_id, limit=settings.history_limit)
        store.add_message(session_id, "user", message)
        messages = [(item["role"], item["content"]) for item in history]
        messages.append(("user", message))

        async def event_stream():
            started = time.perf_counter()
            chunks: list[str] = []
            yield _sse("meta", {"session_id": session_id, "title": session["title"]})
            try:
                async for event in astream_agent_events(agent, messages):
                    if event["type"] == "token":
                        chunks.append(event["text"])
                    yield _sse(event["type"], event)
                answer = "".join(chunks).strip()
                if answer:
                    store.add_message(session_id, "assistant", answer)
                yield _sse(
                    "done",
                    {
                        "session_id": session_id,
                        "answer": answer,
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                    },
                )
            except Exception as exc:
                yield _sse(
                    "error",
                    {
                        "message": str(exc),
                        "type": type(exc).__name__,
                        "hint": _error_hint(exc),
                    },
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/")
    def index():
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="前端页面不存在")
        return FileResponse(index_path)

    return app


