"""Agent 构建与配置加载。

将模型配置、System Prompt 和工具注册集中在这里，CLI、API 和评测脚本共用同一套构建逻辑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from app.core.prompts import build_system_prompt
from app.core.tools import fitness_tools

# app/core/agent.py -> app/core -> app -> 项目根目录
PROJECT_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = PROJECT_DIR.parent


def load_environment() -> None:
    """同时支持项目目录和仓库根目录下的 .env。"""
    load_dotenv(ROOT_DIR / ".env")
    load_dotenv(PROJECT_DIR / ".env", override=True)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(raw: str | None, default: Path) -> Path:
    path = Path(raw or default)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


@dataclass(frozen=True)
class Settings:
    model: str
    base_url: str
    api_key: str
    temperature: float = 0.7
    timeout: float = 30.0
    max_retries: int = 2
    history_limit: int = 20
    db_path: Path = PROJECT_DIR / "data" / "fitness.db"
    mcp_enabled: bool = True
    timezone: str = "Asia/Shanghai"
    calendar_db_path: Path = PROJECT_DIR / "data" / "calendar.db"
    weather_offline: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        load_environment()
        return cls(
            model=os.getenv("MODEL", "").strip(),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "").strip(),
            api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            temperature=float(os.getenv("MODEL_TEMPERATURE", "0.7")),
            timeout=float(os.getenv("MODEL_TIMEOUT", "30")),
            max_retries=int(os.getenv("MODEL_MAX_RETRIES", "2")),
            history_limit=int(os.getenv("HISTORY_LIMIT", "20")),
            db_path=_resolve_path(
                os.getenv("FITNESS_DB_PATH"), PROJECT_DIR / "data" / "fitness.db"
            ),
            mcp_enabled=_env_bool("MCP_ENABLED", True),
            timezone=os.getenv("TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai",
            calendar_db_path=_resolve_path(
                os.getenv("CALENDAR_DB_PATH"), PROJECT_DIR / "data" / "calendar.db"
            ),
            weather_offline=_env_bool("WEATHER_MCP_OFFLINE", False),
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in {
                "MODEL": self.model,
                "DEEPSEEK_BASE_URL": self.base_url,
                "DEEPSEEK_API_KEY": self.api_key,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(
                "缺少环境变量: " + ", ".join(missing) + "。请复制 .env.example 为 .env 并填写。"
            )


def build_chat_model(settings: Settings) -> ChatOpenAI:
    settings.validate()
    return ChatOpenAI(
        model=settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        temperature=settings.temperature,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
    )


async def create_fitness_agent(settings: Settings | None = None):
    """创建无状态 Agent；会话历史由 store.py 持久化后按轮注入。

    MCP 工具的元数据加载是异步的，因此 Agent 构建也使用异步接口。工具调用时
    LangChain MCP Adapter 会为 stdio Server 建立一次短连接。
    """
    settings = settings or Settings.from_env()
    tools = list(fitness_tools)
    if settings.mcp_enabled:
        from app.integrations.mcp_client import load_mcp_tools

        try:
            mcp_tools, _client = await load_mcp_tools(settings)
        except Exception as exc:
            raise RuntimeError(f"MCP 工具加载失败: {exc}") from exc
        tools.extend(mcp_tools)
    return create_agent(
        model=build_chat_model(settings),
        tools=tools,
        system_prompt=build_system_prompt(settings.timezone),
    )
