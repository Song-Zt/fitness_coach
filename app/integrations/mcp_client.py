"""MCP 客户端：加载日历和天气 MCP Server 暴露的工具。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.core.agent import PROJECT_DIR, Settings

MCP_SERVERS_DIR = PROJECT_DIR / "mcp_servers"


def _base_env(extra: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()}
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            **extra,
        }
    )
    return env


def build_connections(settings: Settings) -> dict:
    calendar_path = Path(settings.calendar_db_path)
    if not calendar_path.is_absolute():
        calendar_path = PROJECT_DIR / calendar_path
    return {
        "calendar": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(MCP_SERVERS_DIR / "calendar_server.py")],
            "cwd": str(PROJECT_DIR),
            "env": _base_env(
                {
                    "CALENDAR_DB_PATH": str(calendar_path),
                    "CALENDAR_TIMEZONE": settings.timezone,
                }
            ),
        },
        "weather": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(MCP_SERVERS_DIR / "weather_server.py")],
            "cwd": str(PROJECT_DIR),
            "env": _base_env(
                {
                    "WEATHER_MCP_OFFLINE": "1" if settings.weather_offline else "0",
                    "WEATHER_TIMEZONE": settings.timezone,
                }
            ),
        },
    }


async def load_mcp_tools(settings: Settings) -> tuple[list, MultiServerMCPClient | None]:
    if not settings.mcp_enabled:
        return [], None
    client = MultiServerMCPClient(build_connections(settings))
    tools = await client.get_tools()
    return tools, client
