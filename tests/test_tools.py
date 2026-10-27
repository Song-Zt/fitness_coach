from __future__ import annotations

import json

from app.core.tools import (
    answer_fitness_question,
    calculate_bmi,
    calculate_calories,
    fitness_tools,
    generate_diet_plan,
    generate_workout_plan,
)


def test_all_tools_registered() -> None:
    assert len(fitness_tools) == 5


def test_bmi_normal_range() -> None:
    result = json.loads(calculate_bmi.invoke({"height_cm": 175, "weight_kg": 68}))
    assert result["bmi"] == 22.2
    assert result["category"] == "正常"


def test_bmi_boundary_overweight() -> None:
    result = json.loads(calculate_bmi.invoke({"height_cm": 175, "weight_kg": 73.5}))
    assert result["bmi"] == 24.0
    assert result["category"] == "偏胖"


def test_calories_lose_weight() -> None:
    result = json.loads(
        calculate_calories.invoke(
            {
                "gender": "male",
                "age": 25,
                "height_cm": 175,
                "weight_kg": 80,
                "activity_level": "moderate",
                "goal": "lose_weight",
            }
        )
    )
    assert result["bmr"] == 1774
    assert result["target_calories"] < result["tdee"]
    assert set(result["macros"]) == {"protein_g", "carbs_g", "fat_g"}


def test_diet_plan_is_valid_json() -> None:
    result = json.loads(
        generate_diet_plan.invoke({"target_calories": 1800, "goal": "lose_weight"})
    )
    assert result["target_calories"] == 1800
    assert len(result["meals"]) >= 3
    assert any("早餐" == meal["meal"] for meal in result["meals"])


def test_workout_plan_contains_schedule() -> None:
    result = json.loads(
        generate_workout_plan.invoke(
            {
                "fitness_level": "beginner",
                "goal": "gain_muscle",
                "days_per_week": 3,
                "equipment": "gym",
            }
        )
    )
    assert "每周训练3天" in result["schedule"]
    assert result["workouts"]


def test_faq_keyword_fast_path() -> None:
    answer = answer_fitness_question.invoke({"question": "蛋白粉什么时候喝"})
    assert "蛋白粉" in answer
    assert "训练后" in answer
