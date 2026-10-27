import sqlite3
from pathlib import Path

from app.core.agent import Settings
from evals.run_eval import (
    DEFAULT_CASES,
    check_calendar_state,
    check_tool_args,
    load_cases,
    render_markdown,
    reset_calendar_db,
    summarize,
)


def test_summarize_and_render() -> None:
    results = [
        {
            "id": "diet_001",
            "category": "diet",
            "expected_tools": ["generate_diet_plan"],
            "actual_tools": ["generate_diet_plan"],
            "selection_ok": True,
                "args_checked": False,
                "args_ok": True,
                "state_checked": False,
                "state_ok": True,
            "keyword_ok": True,
                "behavior_ok": True,
            "latency_ms": 100,
            "error": None,
        },
        {
            "id": "workout_001",
            "category": "workout",
            "expected_tools": ["generate_workout_plan"],
            "actual_tools": [],
            "selection_ok": False,
                "args_checked": False,
                "args_ok": True,
                "state_checked": False,
                "state_ok": True,
            "keyword_ok": False,
                "behavior_ok": False,
            "latency_ms": 300,
            "error": None,
        },
    ]
    settings = Settings(
        model="test-model",
        base_url="http://localhost",
        api_key="test",
        db_path=Path("unused.db"),
    )
    summary = summarize(results, settings)

    assert summary["cases"] == 2
    assert summary["tool_selection_accuracy"] == 50.0
    assert summary["tool_precision"] == 100.0
    assert summary["tool_recall"] == 50.0
    assert summary["p95_latency_ms"] == 300
    assert summary["behavior_accuracy"] == 50.0
    assert summary["tool_argument_accuracy"] is None

    markdown = render_markdown(summary)
    assert "Agent 评测报告" in markdown
    assert "| diet | 1 | 100.0% | 未校验 | 未校验 | 100.0% | 100.0% | 100 ms |" in markdown


def test_eval_cases_cover_all_mcp_tools() -> None:
    cases = load_cases(DEFAULT_CASES)

    assert len(cases) == 100
    assert len({case["id"] for case in cases}) == 100
    assert sum(1 for case in cases if case["category"].startswith("mcp_")) >= 60


def test_check_tool_args_normalizes_datetime() -> None:
    trajectory = [
        {
            "name": "calendar_draft_workout",
            "args": {"start": "2030-03-09T19:00:00+08:00", "duration_minutes": 60},
        }
    ]

    checked, ok = check_tool_args(
        trajectory,
        {"calendar_draft_workout": {"start": "2030-03-09T19:00", "duration_minutes": 60}},
    )
    assert checked is True
    assert ok is True

    _, wrong = check_tool_args(
        trajectory,
        {"calendar_draft_workout": {"start": "2030-03-10T19:00"}},
    )
    assert wrong is False


def test_calendar_state_setup_and_assertion() -> None:
    db_path = Path(__file__).resolve().parent.parent / ".tmp" / "test-eval-calendar.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    case = {
        "expected_calendar_status": {"draft-1": "confirmed"},
        "expected_calendar_counts": {"draft": 0, "confirmed": 1},
    }
    reset_calendar_db(
        db_path,
        [
            {
                "id": "draft-1",
                "title": "力量训练",
                "start": "2030-03-09T19:00+08:00",
                "duration_minutes": 60,
                "status": "draft",
            }
        ],
    )

    checked, ok = check_calendar_state(case, db_path)
    assert checked is True
    assert ok is False

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE calendar_events SET status = 'confirmed' WHERE id = 'draft-1'"
        )
    checked, ok = check_calendar_state(case, db_path)
    assert checked is True
    assert ok is True
