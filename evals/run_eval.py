"""运行 Agent 离线评测，输出准确率、MCP 行为、延迟和逐条结果。

用法:
    python evals/run_eval.py --dry-run
    python evals/run_eval.py --limit 10
    python evals/run_eval.py --output evals/report.json

评测使用独立的日历数据库，并默认开启天气离线模式，避免修改真实日程或依赖外部
天气服务。MCP 工具仍通过真实 stdio Server 发现和调用。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from app.core.agent import Settings, create_fitness_agent  # noqa: E402
from app.core.streaming import (  # noqa: E402
    extract_final_text,
    extract_tool_calls,
    extract_tool_trajectory,
)
from mcp_servers.calendar_server import SCHEMA  # noqa: E402

DEFAULT_CASES = PROJECT_DIR / "evals" / "cases.json"
DEFAULT_OUTPUT = PROJECT_DIR / "evals" / "report.json"
DEFAULT_EVAL_DB = PROJECT_DIR / "evals" / ".calendar-eval.db"
TIMEZONE = ZoneInfo("Asia/Shanghai")
EXPECTED_CASE_COUNT = 100

LOCAL_TOOL_NAMES = {
    "calculate_bmi",
    "calculate_calories",
    "generate_diet_plan",
    "generate_workout_plan",
    "answer_fitness_question",
}
MCP_TOOL_NAMES = {
    "calendar_list_events",
    "calendar_find_free_slots",
    "calendar_draft_workout",
    "calendar_confirm_event",
    "calendar_cancel_event",
    "weather_current",
    "weather_forecast",
    "weather_outdoor_advice",
}
ALL_TOOL_NAMES = LOCAL_TOOL_NAMES | MCP_TOOL_NAMES


def load_cases(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        cases = json.load(handle)
    if not isinstance(cases, list):
        raise ValueError("评测集必须是 JSON 数组")
    if len(cases) != EXPECTED_CASE_COUNT:
        raise ValueError(
            f"评测集应包含 {EXPECTED_CASE_COUNT} 条用例，当前为 {len(cases)} 条"
        )

    ids = [case.get("id") for case in cases]
    duplicates = sorted(identifier for identifier, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"用例 ID 重复: {duplicates}")
    for case in cases:
        _validate_case(case)

    expected_mcp_tools = {
        tool for case in cases for tool in case.get("expected_tools", []) if tool in MCP_TOOL_NAMES
    }
    missing_mcp_tools = sorted(MCP_TOOL_NAMES - expected_mcp_tools)
    if missing_mcp_tools:
        raise ValueError(f"以下 MCP 工具没有被任何用例覆盖: {missing_mcp_tools}")
    return cases


def _validate_case(case: dict) -> None:
    required = {"id", "category", "query", "expected_tools"}
    missing = required - set(case)
    if missing:
        raise ValueError(f"用例 {case.get('id', '?')} 缺少字段: {sorted(missing)}")

    expected = case["expected_tools"]
    allowed = case.get("allowed_tools", expected)
    if not isinstance(expected, list) or not isinstance(allowed, list):
        raise ValueError(f"用例 {case['id']} 的工具字段必须是数组")
    if len(expected) != len(set(expected)):
        raise ValueError(f"用例 {case['id']} 的 expected_tools 有重复项")
    unknown_tools = (set(expected) | set(allowed)) - ALL_TOOL_NAMES
    if unknown_tools:
        raise ValueError(f"用例 {case['id']} 包含未知工具: {sorted(unknown_tools)}")
    if not set(expected) <= set(allowed):
        raise ValueError(f"用例 {case['id']} 的 expected_tools 必须是 allowed_tools 的子集")

    expected_args = case.get("expected_args_contains", {})
    if not isinstance(expected_args, dict):
        raise ValueError(f"用例 {case['id']} 的 expected_args_contains 必须是对象")
    unknown_arg_tools = set(expected_args) - set(expected) - set(allowed)
    if unknown_arg_tools:
        raise ValueError(
            f"用例 {case['id']} 的参数校验引用了未允许的工具: {sorted(unknown_arg_tools)}"
        )

    history = case.get("history", [])
    if not isinstance(history, list) or any(
        not isinstance(item, list) or len(item) != 2 or item[0] not in {"user", "assistant"}
        for item in history
    ):
        raise ValueError(f"用例 {case['id']} 的 history 格式无效")

    setup = case.get("setup", {})
    if not isinstance(setup, dict):
        raise ValueError(f"用例 {case['id']} 的 setup 必须是对象")
    seeded_ids = set()
    for event in setup.get("calendar_events", []):
        if not {"id", "title", "start", "status"} <= set(event):
            raise ValueError(f"用例 {case['id']} 的日历种子缺少必要字段")
        if event["id"] in seeded_ids:
            raise ValueError(f"用例 {case['id']} 的日历种子 ID 重复")
        if event["status"] not in {"draft", "confirmed", "cancelled"}:
            raise ValueError(f"用例 {case['id']} 的日历种子状态无效")
        seeded_ids.add(event["id"])

    for event_id in case.get("expected_calendar_status", {}):
        if event_id not in seeded_ids:
            raise ValueError(f"用例 {case['id']} 校验了未预置的日历 ID: {event_id}")


def reset_calendar_db(db_path: Path, events: list[dict] | None = None) -> None:
    """清空并预置评测专用日历库；绝不操作生产日历。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.executescript(SCHEMA)
        connection.execute("DELETE FROM calendar_events")
        now = datetime.now(TIMEZONE).isoformat(timespec="minutes")
        for event in events or []:
            start = _parse_datetime(event["start"])
            if event.get("end"):
                end = _parse_datetime(event["end"])
            else:
                end = start + timedelta(minutes=int(event.get("duration_minutes", 60)))
            connection.execute(
                """
                INSERT INTO calendar_events
                    (id, title, start_at, end_at, status, plan_summary, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    event["title"],
                    _format_datetime(start),
                    _format_datetime(end),
                    event["status"],
                    event.get("plan_summary", ""),
                    event.get("created_at", now),
                    event.get("updated_at", now),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def read_calendar_events(db_path: Path) -> list[dict]:
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM calendar_events ORDER BY start_at").fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "start": row["start_at"],
                "end": row["end_at"],
                "status": row["status"],
                "plan_summary": row["plan_summary"],
            }
            for row in rows
        ]
    finally:
        connection.close()


def check_calendar_state(case: dict, db_path: Path) -> tuple[bool, bool]:
    """返回 (是否执行了状态校验, 状态是否通过)。"""
    has_state_expectation = any(
        key in case
        for key in (
            "expected_calendar_status",
            "expected_calendar_counts",
            "expected_calendar_events",
        )
    )
    if not has_state_expectation:
        return False, True

    events = read_calendar_events(db_path)
    status_map = {event["id"]: event["status"] for event in events}
    for event_id, expected_status in case.get("expected_calendar_status", {}).items():
        if status_map.get(event_id) != expected_status:
            return True, False

    counts = Counter(event["status"] for event in events)
    for status, expected_count in case.get("expected_calendar_counts", {}).items():
        if counts[status] != expected_count:
            return True, False

    for expected_event in case.get("expected_calendar_events", []):
        if not any(_event_matches(actual, expected_event) for actual in events):
            return True, False
    return True, True


def _event_matches(actual: dict, expected: dict) -> bool:
    if "title_contains" in expected and expected["title_contains"] not in actual["title"]:
        return False
    if "status" in expected and actual["status"] != expected["status"]:
        return False
    if "start" in expected and not _value_matches(expected["start"], actual["start"]):
        return False
    if "duration_minutes" in expected:
        duration = _parse_datetime(actual["end"]) - _parse_datetime(actual["start"])
        if duration != timedelta(minutes=int(expected["duration_minutes"])):
            return False
    return True


def check_tool_args(trajectory: list[dict], expected: dict) -> tuple[bool, bool]:
    """返回 (是否执行了参数校验, 参数是否通过)。"""
    if not expected:
        return False, True
    for tool_name, partial_args in expected.items():
        calls = [call for call in trajectory if call["name"] == tool_name]
        if not any(_value_matches(partial_args, call.get("args", {})) for call in calls):
            return True, False
    return True, True


def _value_matches(expected: object, actual: object) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _value_matches(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, bool):
        return isinstance(actual, bool) and expected is actual
    if isinstance(expected, (int, float)):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str):
        if not isinstance(actual, str):
            return False
        expected_time = _as_datetime(expected)
        actual_time = _as_datetime(actual)
        if expected_time is not None and actual_time is not None:
            if len(expected.strip()) == 10:
                return expected_time.date() == actual_time.date()
            return expected_time == actual_time
        return expected.strip().lower() in actual.strip().lower()
    return expected == actual


def _as_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}(?:[T ]|$)", value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return parsed.astimezone(TIMEZONE)


def _parse_datetime(value: str) -> datetime:
    parsed = _as_datetime(value)
    if parsed is None:
        raise ValueError(f"无效时间: {value}")
    return parsed


def _format_datetime(value: datetime) -> str:
    return value.astimezone(TIMEZONE).isoformat(timespec="minutes")


async def evaluate_case(agent, case: dict, eval_db: Path) -> dict:
    setup = case.get("setup", {})
    if setup.get("clear_calendar", True):
        reset_calendar_db(eval_db, setup.get("calendar_events", []))

    messages = [(role, content) for role, content in case.get("history", [])]
    messages.append(("user", case["query"]))
    started = time.perf_counter()
    error = None
    answer = ""
    actual_tools: list[str] = []
    trajectory: list[dict] = []
    try:
        result = await agent.ainvoke({"messages": messages})
        answer = extract_final_text(result)
        actual_tools = extract_tool_calls(result)
        trajectory = extract_tool_trajectory(result)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.perf_counter() - started) * 1000)

    expected = list(dict.fromkeys(case["expected_tools"]))
    allowed = list(dict.fromkeys(case.get("allowed_tools", expected)))
    expected_set = set(expected)
    actual_set = set(actual_tools)
    allowed_set = set(allowed)
    selection_ok = expected_set <= actual_set <= allowed_set and error is None

    args_checked, args_ok = check_tool_args(
        trajectory, case.get("expected_args_contains", {})
    )
    args_ok = args_ok and error is None
    state_checked, state_ok = check_calendar_state(case, eval_db)
    state_ok = state_ok and error is None

    keywords = case.get("expected_keywords", [])
    keyword_ok = not keywords or any(keyword in answer for keyword in keywords)
    behavior_ok = selection_ok and args_ok and state_ok and keyword_ok
    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "expected_tools": expected,
        "allowed_tools": allowed,
        "actual_tools": actual_tools,
        "actual_tool_calls": trajectory,
        "selection_ok": selection_ok,
        "args_checked": args_checked,
        "args_ok": args_ok,
        "state_checked": state_checked,
        "state_ok": state_ok,
        "keyword_ok": keyword_ok,
        "behavior_ok": behavior_ok,
        "latency_ms": latency_ms,
        "error": error,
        "answer_preview": answer[:300],
    }


def _rate(rows: list[dict], key: str) -> float:
    if not rows:
        return 0.0
    return round(100 * sum(1 for row in rows if row[key]) / len(rows), 1)


def _optional_rate(rows: list[dict], checked_key: str, result_key: str) -> float | None:
    checked = [row for row in rows if row.get(checked_key)]
    return _rate(checked, result_key) if checked else None


def summarize(results: list[dict], settings: Settings) -> dict:
    errors = [row for row in results if row["error"]]
    latencies = [row["latency_ms"] for row in results]
    expected_calls = sum(len(row["expected_tools"]) for row in results)
    actual_calls = sum(len(row["actual_tools"]) for row in results)
    matched_calls = sum(
        len(set(row["expected_tools"]) & set(row["actual_tools"])) for row in results
    )
    precision = matched_calls / actual_calls if actual_calls else 0.0
    recall = matched_calls / expected_calls if expected_calls else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    if len(latencies) >= 20:
        p95_latency = round(statistics.quantiles(latencies, n=20)[18])
    else:
        p95_latency = max(latencies) if latencies else 0

    mcp_rows = [
        row
        for row in results
        if row["category"].startswith("mcp_")
        or set(row["expected_tools"]) & MCP_TOOL_NAMES
        or set(row["actual_tools"]) & MCP_TOOL_NAMES
    ]
    categories = sorted({row["category"] for row in results})
    by_category = {}
    for category in categories:
        rows = [row for row in results if row["category"] == category]
        by_category[category] = {
            "cases": len(rows),
            "tool_selection_accuracy": _rate(rows, "selection_ok"),
            "argument_accuracy": _optional_rate(rows, "args_checked", "args_ok"),
            "state_accuracy": _optional_rate(rows, "state_checked", "state_ok"),
            "keyword_hit_rate": _rate(rows, "keyword_ok"),
            "behavior_accuracy": _rate(rows, "behavior_ok"),
            "avg_latency_ms": round(statistics.mean(row["latency_ms"] for row in rows)),
        }

    return {
        "model": settings.model,
        "cases": len(results),
        "errors": len(errors),
        "tool_selection_accuracy": _rate(results, "selection_ok"),
        "tool_argument_accuracy": _optional_rate(results, "args_checked", "args_ok"),
        "calendar_state_accuracy": _optional_rate(results, "state_checked", "state_ok"),
        "keyword_hit_rate": _rate(results, "keyword_ok"),
        "behavior_accuracy": _rate(results, "behavior_ok"),
        "tool_precision": round(100 * precision, 1),
        "tool_recall": round(100 * recall, 1),
        "tool_f1": round(100 * f1, 1),
        "arg_checked_cases": sum(1 for row in results if row["args_checked"]),
        "state_checked_cases": sum(1 for row in results if row["state_checked"]),
        "mcp_cases": len(mcp_rows),
        "mcp_tool_selection_accuracy": _rate(mcp_rows, "selection_ok"),
        "mcp_behavior_accuracy": _rate(mcp_rows, "behavior_ok"),
        "avg_latency_ms": round(statistics.mean(latencies)) if latencies else 0,
        "p95_latency_ms": p95_latency,
        "by_category": by_category,
    }


def _format_optional_rate(value: float | None) -> str:
    return "未校验" if value is None else f"{value}%"


def render_markdown(summary: dict) -> str:
    lines = [
        "# Agent 评测报告",
        "",
        f"- 模型: `{summary['model']}`",
        f"- 用例数: {summary['cases']}",
        f"- 运行错误: {summary['errors']}",
        f"- MCP 用例数: {summary['mcp_cases']}",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 工具选择准确率 | {summary['tool_selection_accuracy']}% |",
        f"| 工具参数准确率 | {_format_optional_rate(summary['tool_argument_accuracy'])}"
        f"（{summary['arg_checked_cases']} 条校验） |",
        f"| 日历状态准确率 | {_format_optional_rate(summary['calendar_state_accuracy'])}"
        f"（{summary['state_checked_cases']} 条校验） |",
        f"| 端到端行为准确率 | {summary['behavior_accuracy']}% |",
        f"| MCP 工具选择准确率 | {summary['mcp_tool_selection_accuracy']}% |",
        f"| MCP 端到端行为准确率 | {summary['mcp_behavior_accuracy']}% |",
        f"| 关键词命中率 | {summary['keyword_hit_rate']}% |",
        f"| Tool Precision | {summary['tool_precision']}% |",
        f"| Tool Recall | {summary['tool_recall']}% |",
        f"| Tool F1 | {summary['tool_f1']}% |",
        f"| 平均延迟 | {summary['avg_latency_ms']} ms |",
        f"| P95 延迟 | {summary['p95_latency_ms']} ms |",
        "",
        "## 分类结果",
        "",
        "| 类别 | 用例数 | 工具准确率 | 参数准确率 | 状态准确率 "
        "| 关键词命中率 | 行为准确率 | 平均延迟 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for category, row in summary["by_category"].items():
        lines.append(
            f"| {category} | {row['cases']} | {row['tool_selection_accuracy']}% | "
            f"{_format_optional_rate(row['argument_accuracy'])} | "
            f"{_format_optional_rate(row['state_accuracy'])} | "
            f"{row['keyword_hit_rate']}% | {row['behavior_accuracy']}% | "
            f"{row['avg_latency_ms']} ms |"
        )
    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description="运行 AI 健身教练离线评测")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--eval-calendar-db", type=Path, default=DEFAULT_EVAL_DB)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", default=None, help="只运行指定用例，多个 ID 用逗号分隔")
    parser.add_argument("--dry-run", action="store_true", help="只校验用例，不调用模型")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.ids:
        selected_ids = {item.strip() for item in args.ids.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in selected_ids]
        missing_ids = selected_ids - {case["id"] for case in cases}
        if missing_ids:
            print(f"找不到用例: {', '.join(sorted(missing_ids))}", file=sys.stderr)
            return 1
    if args.limit:
        cases = cases[: args.limit]
    print(f"已加载 {len(cases)} 条用例", file=sys.stderr)
    if args.dry_run:
        categories = sorted({case["category"] for case in cases})
        mcp_cases = sum(
            1
            for case in cases
            if case["category"].startswith("mcp_")
            or set(case["expected_tools"]) & MCP_TOOL_NAMES
        )
        print(f"用例校验通过，共 {len(cases)} 条，{len(categories)} 个类别", file=sys.stderr)
        print(f"MCP 用例 {mcp_cases} 条，8 个 MCP 工具均已覆盖", file=sys.stderr)
        print(f"类别: {', '.join(categories)}", file=sys.stderr)
        return 0

    settings = replace(
        Settings.from_env(),
        mcp_enabled=True,
        weather_offline=True,
        calendar_db_path=args.eval_calendar_db.resolve(),
    )
    print(f"评测日历库: {args.eval_calendar_db.resolve()}", file=sys.stderr)
    print("天气 MCP: 离线确定性模式", file=sys.stderr)
    try:
        agent = await create_fitness_agent(settings)
    except Exception as exc:
        print(f"Agent 初始化失败: {exc}", file=sys.stderr)
        return 2

    results = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['id']}", file=sys.stderr)
        results.append(await evaluate_case(agent, case, settings.calendar_db_path))

    summary = summarize(results, settings)
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": summary,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    print(render_markdown(summary))
    print(f"报告已写入 {args.output} 和 {markdown_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
