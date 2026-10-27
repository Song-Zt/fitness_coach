from __future__ import annotations

import asyncio
import json

import pytest

from app.core.agent import Settings
from app.integrations.mcp_client import build_connections, load_mcp_tools
from mcp_servers import calendar_server, weather_server


def _payload(raw: str) -> dict:
    return json.loads(raw)


def test_mcp_servers_register_expected_tools() -> None:
    calendar_tools = {
        tool.name for tool in asyncio.run(calendar_server.mcp.list_tools())
    }
    weather_tools = {
        tool.name for tool in asyncio.run(weather_server.mcp.list_tools())
    }

    assert calendar_tools == {
        "calendar_list_events",
        "calendar_find_free_slots",
        "calendar_draft_workout",
        "calendar_confirm_event",
        "calendar_cancel_event",
    }
    assert weather_tools == {
        "weather_current",
        "weather_forecast",
        "weather_outdoor_advice",
    }


def test_calendar_draft_confirm_cancel_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CALENDAR_DB_PATH", str(tmp_path / "calendar.db"))

    slots = _payload(
        calendar_server.calendar_find_free_slots(
            start_date="2030-01-02",
            end_date="2030-01-02",
            duration_minutes=60,
            preferred_start="18:00",
            preferred_end="22:00",
            max_slots=3,
        )
    )
    assert slots["ok"] is True
    assert len(slots["slots"]) == 3

    draft = _payload(
        calendar_server.calendar_draft_workout(
            title="全身力量训练",
            start="2030-01-02T18:00",
            duration_minutes=60,
            plan_summary="深蹲、卧推、划船",
        )
    )
    assert draft["ok"] is True
    assert draft["requires_confirmation"] is True
    event = draft["event"]
    assert event["status"] == "draft"

    after_draft = _payload(
        calendar_server.calendar_find_free_slots(
            start_date="2030-01-02",
            end_date="2030-01-02",
            duration_minutes=60,
            preferred_start="18:00",
            preferred_end="22:00",
            max_slots=3,
        )
    )
    assert all(slot["start"] != event["start"] for slot in after_draft["slots"])

    listed = _payload(
        calendar_server.calendar_list_events("2030-01-02", "2030-01-02")
    )
    assert listed["events"][0]["id"] == event["id"]
    assert listed["events"][0]["status"] == "draft"

    confirmed = _payload(calendar_server.calendar_confirm_event(event["id"]))
    assert confirmed["ok"] is True
    assert confirmed["event"]["status"] == "confirmed"

    again = _payload(calendar_server.calendar_confirm_event(event["id"]))
    assert again["already_confirmed"] is True

    cancelled = _payload(calendar_server.calendar_cancel_event(event["id"]))
    assert cancelled["event"]["status"] == "cancelled"
    visible = _payload(
        calendar_server.calendar_list_events("2030-01-02", "2030-01-02")
    )
    assert visible["events"] == []


def test_calendar_rejects_conflicts_and_unknown_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CALENDAR_DB_PATH", str(tmp_path / "calendar.db"))

    first = _payload(
        calendar_server.calendar_draft_workout(
            title="晨跑", start="2030-01-03T07:00", duration_minutes=45
        )
    )
    assert first["ok"] is True

    conflict = _payload(
        calendar_server.calendar_draft_workout(
            title="力量训练", start="2030-01-03T07:30", duration_minutes=45
        )
    )
    assert conflict["ok"] is False
    assert "已有安排" in conflict["error"]

    missing_confirm = _payload(calendar_server.calendar_confirm_event("missing"))
    assert missing_confirm["ok"] is False
    missing_cancel = _payload(calendar_server.calendar_cancel_event("missing"))
    assert missing_cancel["ok"] is False


def test_weather_mcp_offline_mode(monkeypatch) -> None:
    monkeypatch.setenv("WEATHER_MCP_OFFLINE", "1")

    current = _payload(asyncio.run(weather_server.weather_current("上海")))
    assert current["ok"] is True
    assert current["offline"] is True
    assert current["city"] == "上海"

    forecast = _payload(asyncio.run(weather_server.weather_forecast("上海", 3)))
    assert len(forecast["days"]) == 3

    advice = _payload(
        asyncio.run(
            weather_server.weather_outdoor_advice(
                "上海", target_date=forecast["days"][0]["date"]
            )
        )
    )
    assert advice["ok"] is True
    assert advice["suitable"] is True
    assert "户外训练" in advice["recommendation"]


def test_mcp_client_builds_two_stdio_connections(tmp_path) -> None:
    settings = Settings(
        model="test-model",
        base_url="http://localhost",
        api_key="test",
        mcp_enabled=True,
        weather_offline=True,
        calendar_db_path=tmp_path / "calendar.db",
    )
    connections = build_connections(settings)

    assert set(connections) == {"calendar", "weather"}
    assert connections["calendar"]["transport"] == "stdio"
    assert connections["weather"]["transport"] == "stdio"
    assert connections["calendar"]["env"]["CALENDAR_DB_PATH"].endswith("calendar.db")
    assert connections["weather"]["env"]["WEATHER_MCP_OFFLINE"] == "1"


def test_mcp_tools_load_over_stdio(tmp_path) -> None:
    settings = Settings(
        model="test-model",
        base_url="http://localhost",
        api_key="test",
        mcp_enabled=True,
        weather_offline=True,
        calendar_db_path=tmp_path / "calendar.db",
    )

    async def discover() -> set[str]:
        tools, _client = await load_mcp_tools(settings)
        return {tool.name for tool in tools}

    try:
        names = asyncio.run(discover())
    except PermissionError as exc:
        pytest.skip(f"当前环境禁止启动 MCP stdio 子进程: {exc}")

    assert {
        "calendar_list_events",
        "calendar_find_free_slots",
        "calendar_draft_workout",
        "calendar_confirm_event",
        "calendar_cancel_event",
        "weather_current",
        "weather_forecast",
        "weather_outdoor_advice",
    } <= names
