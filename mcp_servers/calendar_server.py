"""Calendar MCP server.

暴露查询空闲时间、创建训练草稿、确认写入和取消日程等工具。
写操作采用两阶段流程：先 draft，用户确认后再 confirm。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_DIR / "data" / "calendar.db"
TIMEZONE = os.getenv("CALENDAR_TIMEZONE", "Asia/Shanghai")
TZ = ZoneInfo(TIMEZONE)
MAX_RANGE_DAYS = 31

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'confirmed', 'cancelled')),
    plan_summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_events_time
    ON calendar_events(start_at, end_at, status);
"""

mcp = FastMCP(
    "fitness-calendar",
    instructions=(
        "管理用户训练日程。查询和找空闲时间可以直接执行；"
        "写入日历必须先用 calendar_draft_workout 创建草稿，"
        "只有用户明确确认后才能调用 calendar_confirm_event。"
    ),
)


def _db_path() -> Path:
    raw = os.getenv("CALENDAR_DB_PATH", str(DEFAULT_DB))
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> datetime:
    return datetime.now(TZ)


def _parse_dt(value: str) -> datetime:
    value = value.strip()
    if len(value) == 10:
        parsed = datetime.combine(date.fromisoformat(value), time.min)
    else:
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def _date_bounds(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    start_day = date.fromisoformat(start_date)
    end_day = date.fromisoformat(end_date)
    if end_day < start_day:
        raise ValueError("end_date 不能早于 start_date")
    if (end_day - start_day).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"日期范围最多 {MAX_RANGE_DAYS} 天")
    start = datetime.combine(start_day, time.min, tzinfo=TZ)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=TZ)
    return start, end


def _iso(value: datetime) -> str:
    return value.astimezone(TZ).isoformat(timespec="minutes")


def _event(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "start": row["start_at"],
        "end": row["end_at"],
        "status": row["status"],
        "plan_summary": row["plan_summary"],
    }


def _events_between(conn: sqlite3.Connection, start: datetime, end: datetime) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM calendar_events
        WHERE status != 'cancelled' AND start_at < ? AND end_at > ?
        ORDER BY start_at ASC
        """,
        (_iso(end), _iso(start)),
    ).fetchall()


def _error(message: str, **extra) -> str:
    return json.dumps({"ok": False, "error": message, **extra}, ensure_ascii=False)


@mcp.tool()
def calendar_list_events(
    start_date: str,
    end_date: str,
    include_cancelled: bool = False,
) -> str:
    """列出指定日期范围内的训练日程。

    Args:
        start_date: 开始日期，格式 YYYY-MM-DD
        end_date: 结束日期，格式 YYYY-MM-DD
        include_cancelled: 是否包含已取消日程
    """
    try:
        start, end = _date_bounds(start_date, end_date)
        with _connect() as conn:
            if include_cancelled:
                rows = conn.execute(
                    """
                    SELECT * FROM calendar_events
                    WHERE start_at < ? AND end_at > ?
                    ORDER BY start_at ASC
                    """,
                    (_iso(end), _iso(start)),
                ).fetchall()
            else:
                rows = _events_between(conn, start, end)
        return json.dumps(
            {"ok": True, "timezone": TIMEZONE, "events": [_event(row) for row in rows]},
            ensure_ascii=False,
        )
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
def calendar_find_free_slots(
    start_date: str,
    end_date: str,
    duration_minutes: int = 60,
    preferred_start: str = "18:00",
    preferred_end: str = "22:00",
    max_slots: int = 5,
) -> str:
    """查找适合安排训练的连续空闲时间。

    Args:
        start_date: 查询开始日期，格式 YYYY-MM-DD
        end_date: 查询结束日期，格式 YYYY-MM-DD
        duration_minutes: 需要的训练时长，15-240 分钟
        preferred_start: 每天可训练窗口开始时间，格式 HH:MM
        preferred_end: 每天可训练窗口结束时间，格式 HH:MM
        max_slots: 最多返回多少个候选时间
    """
    try:
        if not 15 <= duration_minutes <= 240:
            return _error("duration_minutes 必须在 15-240 之间")
        if not 1 <= max_slots <= 20:
            return _error("max_slots 必须在 1-20 之间")
        window_start = time.fromisoformat(preferred_start)
        window_end = time.fromisoformat(preferred_end)
        start, end = _date_bounds(start_date, end_date)
        duration = timedelta(minutes=duration_minutes)
        with _connect() as conn:
            busy = [
                (_parse_dt(row["start_at"]), _parse_dt(row["end_at"]))
                for row in _events_between(conn, start, end)
            ]

        slots: list[dict] = []
        day = date.fromisoformat(start_date)
        end_day = date.fromisoformat(end_date)
        while day <= end_day and len(slots) < max_slots:
            win_start = datetime.combine(day, window_start, tzinfo=TZ)
            win_end = datetime.combine(day, window_end, tzinfo=TZ)
            if win_end <= win_start:
                return _error("preferred_end 必须晚于 preferred_start")
            cursor = win_start
            while cursor + duration <= win_end and len(slots) < max_slots:
                conflict = next(
                    ((busy_start, busy_end) for busy_start, busy_end in busy
                     if busy_start < cursor + duration and busy_end > cursor),
                    None,
                )
                if conflict is None:
                    slots.append({"start": _iso(cursor), "end": _iso(cursor + duration)})
                    cursor += timedelta(minutes=30)
                else:
                    cursor = max(cursor + timedelta(minutes=30), conflict[1])
            day += timedelta(days=1)
        return json.dumps(
            {"ok": True, "timezone": TIMEZONE, "slots": slots},
            ensure_ascii=False,
        )
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
def calendar_draft_workout(
    title: str,
    start: str,
    duration_minutes: int = 60,
    plan_summary: str = "",
) -> str:
    """创建一条待确认的训练日程草稿，不会直接写入用户日历。

    Args:
        title: 日程标题，例如“全身力量训练”
        start: 开始时间，格式 YYYY-MM-DDTHH:MM
        duration_minutes: 训练时长，15-240 分钟
        plan_summary: 这次训练的简短说明
    """
    try:
        if not 15 <= duration_minutes <= 240:
            return _error("duration_minutes 必须在 15-240 之间")
        start_dt = _parse_dt(start)
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        with _connect() as conn:
            conflicts = conn.execute(
                """
                SELECT * FROM calendar_events
                WHERE status IN ('draft', 'confirmed') AND start_at < ? AND end_at > ?
                ORDER BY start_at ASC
                """,
                (_iso(end_dt), _iso(start_dt)),
            ).fetchall()
            if conflicts:
                return _error(
                    "该时间段已有安排，请先换个时间",
                    conflicts=[_event(row) for row in conflicts],
                )
            event_id = "draft-" + uuid.uuid4().hex[:8]
            now = _iso(_now())
            conn.execute(
                """
                INSERT INTO calendar_events
                    (id, title, start_at, end_at, status, plan_summary, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)
                """,
                (event_id, title.strip(), _iso(start_dt), _iso(end_dt), plan_summary, now, now),
            )
            row = conn.execute(
                "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
            ).fetchone()
        return json.dumps(
            {
                "ok": True,
                "requires_confirmation": True,
                "event": _event(row),
                "message": "草稿已创建。请让用户确认后再调用 calendar_confirm_event。",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
def calendar_confirm_event(draft_id: str) -> str:
    """把训练草稿写入日历。只有用户明确确认后才能调用。

    Args:
        draft_id: calendar_draft_workout 返回的草稿 ID
    """
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM calendar_events WHERE id = ?", (draft_id,)
            ).fetchone()
            if row is None:
                return _error("草稿不存在")
            if row["status"] == "confirmed":
                return json.dumps(
                    {"ok": True, "already_confirmed": True, "event": _event(row)},
                    ensure_ascii=False,
                )
            if row["status"] != "draft":
                return _error(f"当前状态是 {row['status']}，不能确认")
            conn.execute(
                "UPDATE calendar_events SET status = 'confirmed', updated_at = ? WHERE id = ?",
                (_iso(_now()), draft_id),
            )
            row = conn.execute(
                "SELECT * FROM calendar_events WHERE id = ?", (draft_id,)
            ).fetchone()
        return json.dumps({"ok": True, "event": _event(row)}, ensure_ascii=False)
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
def calendar_cancel_event(event_id: str) -> str:
    """取消一条训练日程。只有用户明确要求取消后才能调用。

    Args:
        event_id: 日程 ID
    """
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
            ).fetchone()
            if row is None:
                return _error("日程不存在")
            if row["status"] == "cancelled":
                return json.dumps(
                    {"ok": True, "already_cancelled": True, "event": _event(row)},
                    ensure_ascii=False,
                )
            conn.execute(
                "UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (_iso(_now()), event_id),
            )
            row = conn.execute(
                "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
            ).fetchone()
        return json.dumps({"ok": True, "event": _event(row)}, ensure_ascii=False)
    except Exception as exc:
        return _error(str(exc))


if __name__ == "__main__":
    mcp.run(transport="stdio")
