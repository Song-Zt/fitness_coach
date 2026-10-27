"""Weather MCP server.

使用 Open-Meteo 免费接口查询当前天气和未来几天预报，并给出户外训练建议。
设置 WEATHER_MCP_OFFLINE=1 可在无网络环境返回固定示例数据。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP

TIMEZONE = os.getenv("WEATHER_TIMEZONE", "Asia/Shanghai")
TZ = ZoneInfo(TIMEZONE)
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴大冰雹",
}

mcp = FastMCP(
    "fitness-weather",
    instructions=(
        "查询城市天气并判断是否适合户外训练。"
        "用于回答天气、温度、降雨概率和户外/室内训练建议。"
    ),
)


def _error(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra}, ensure_ascii=False)


def _condition(code: int | None) -> str:
    return WEATHER_CODES.get(int(code) if code is not None else -1, "未知天气")


def _offline_daily(days: int) -> list[dict]:
    today = datetime.now(TZ).date()
    rows = []
    for offset in range(days):
        rows.append(
            {
                "date": (today + timedelta(days=offset)).isoformat(),
                "condition": "多云",
                "weather_code": 2,
                "temp_max_c": 25.0,
                "temp_min_c": 17.0,
                "precipitation_probability": 20,
                "wind_kmh": 12.0,
            }
        )
    return rows


def _offline_forecast(city: str, days: int) -> dict:
    return {
        "ok": True,
        "offline": True,
        "city": city,
        "timezone": TIMEZONE,
        "days": _offline_daily(days),
    }


async def _geocode(client: httpx.AsyncClient, city: str) -> dict:
    response = await client.get(
        GEOCODE_URL,
        params={"name": city, "count": 1, "language": "zh", "format": "json"},
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        raise ValueError(f"找不到城市: {city}")
    result = results[0]
    return {
        "name": result.get("name", city),
        "country": result.get("country", ""),
        "latitude": result["latitude"],
        "longitude": result["longitude"],
    }


async def _fetch_forecast(city: str, days: int) -> dict:
    if os.getenv("WEATHER_MCP_OFFLINE", "0") == "1":
        return _offline_forecast(city, days)

    async with httpx.AsyncClient(timeout=10) as client:
        location = await _geocode(client, city)
        response = await client.get(
            FORECAST_URL,
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,wind_speed_10m_max"
                ),
                "timezone": TIMEZONE,
                "forecast_days": days,
            },
        )
        response.raise_for_status()
        payload = response.json()

    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    rows = []
    for index, day in enumerate(dates):
        rows.append(
            {
                "date": day,
                "condition": _condition(daily.get("weather_code", [None])[index]),
                "weather_code": daily.get("weather_code", [None])[index],
                "temp_max_c": daily.get("temperature_2m_max", [None])[index],
                "temp_min_c": daily.get("temperature_2m_min", [None])[index],
                "precipitation_probability": daily.get(
                    "precipitation_probability_max", [None]
                )[index],
                "wind_kmh": daily.get("wind_speed_10m_max", [None])[index],
            }
        )
    return {
        "ok": True,
        "city": location["name"],
        "country": location["country"],
        "timezone": TIMEZONE,
        "current": payload.get("current"),
        "days": rows,
    }


@mcp.tool()
async def weather_current(city: str) -> str:
    """查询城市当前天气。

    Args:
        city: 城市名，例如“北京”“上海”
    """
    try:
        if os.getenv("WEATHER_MCP_OFFLINE", "0") == "1":
            daily = _offline_daily(1)[0]
            return json.dumps(
                {
                    "ok": True,
                    "offline": True,
                    "city": city,
                    "timezone": TIMEZONE,
                    "temperature_c": daily["temp_max_c"],
                    "precipitation_mm": 0.0,
                    "wind_kmh": daily["wind_kmh"],
                    "condition": daily["condition"],
                },
                ensure_ascii=False,
            )
        forecast = await _fetch_forecast(city, 1)
        current = forecast.get("current") or {}
        return json.dumps(
            {
                "ok": True,
                "city": forecast["city"],
                "country": forecast.get("country", ""),
                "timezone": forecast["timezone"],
                "time": current.get("time"),
                "temperature_c": current.get("temperature_2m"),
                "precipitation_mm": current.get("precipitation"),
                "wind_kmh": current.get("wind_speed_10m"),
                "condition": _condition(current.get("weather_code")),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return _error(str(exc), hint="请检查城市名、网络或 Open-Meteo 服务状态")


@mcp.tool()
async def weather_forecast(city: str, days: int = 3) -> str:
    """查询城市未来几天的天气预报。

    Args:
        city: 城市名，例如“北京”“上海”
        days: 预报天数，1-7 天
    """
    try:
        if not 1 <= days <= 7:
            return _error("days 必须在 1-7 之间")
        return json.dumps(await _fetch_forecast(city, days), ensure_ascii=False)
    except Exception as exc:
        return _error(str(exc), hint="请检查城市名、网络或 Open-Meteo 服务状态")


@mcp.tool()
async def weather_outdoor_advice(city: str, target_date: str | None = None) -> str:
    """判断某天是否适合户外训练，并给出室内/户外建议。

    Args:
        city: 城市名，例如“北京”“上海”
        target_date: 目标日期，格式 YYYY-MM-DD；为空时使用今天
    """
    try:
        forecast = await _fetch_forecast(city, 7)
        day = target_date or datetime.now(TZ).date().isoformat()
        matched = next((row for row in forecast["days"] if row["date"] == day), None)
        if matched is None:
            return _error(f"预报中没有日期 {day}，请选择未来 7 天内")

        code = matched["weather_code"]
        precip = matched["precipitation_probability"] or 0
        wind = matched["wind_kmh"] or 0
        temp_max = matched["temp_max_c"]
        temp_min = matched["temp_min_c"]
        severe = code in {65, 75, 82, 95, 96, 99}

        reasons = []
        if severe or precip >= 60:
            reasons.append("降雨或强对流概率较高")
        elif precip >= 40:
            reasons.append("有一定降雨概率")
        if wind >= 35:
            reasons.append("风力较大")
        if temp_max is not None and temp_max >= 33:
            reasons.append("气温偏高")
        if temp_min is not None and temp_min <= 0:
            reasons.append("气温偏低")

        suitable = not reasons
        if suitable:
            recommendation = "适合户外训练，注意热身、补水和防晒。"
        elif severe or precip >= 60:
            recommendation = "建议改为室内训练，或把户外运动改到天气较好的日期。"
        else:
            recommendation = "可以降低户外训练强度，或改为室内训练，注意根据天气调整。"

        return json.dumps(
            {
                "ok": True,
                "city": forecast["city"],
                "date": day,
                "suitable": suitable,
                "reason": "；".join(reasons) if reasons else "天气条件适合户外训练",
                "recommendation": recommendation,
                "weather": matched,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return _error(str(exc), hint="请检查城市名、网络或 Open-Meteo 服务状态")


if __name__ == "__main__":
    mcp.run(transport="stdio")
