from __future__ import annotations

from genius.official_like import evaluate_case_solution


def test_platform_example_courier_list_output_is_valid() -> None:
    case_text = (
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "T0001\tC001\t10\t0.5\n"
    )

    ev = evaluate_case_solution(case_text, [("T0001", ["C001"])])

    assert ev["valid"] is True
    assert ev["illegal_solution"] is False
    assert ev["covered_tasks"] == 1


def test_duplicate_task_rows_make_whole_case_illegal_with_max_penalty() -> None:
    case_text = (
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "T0001\tC001\t10\t0.5\n"
        "T0001\tC002\t11\t0.5\n"
        "T0002\tC002\t20\t0.5\n"
    )

    # Second row reuses the same task and should be marked invalid.
    raw_output = [
        ("T0001", "C001"),
        ("T0001", "C002"),
    ]

    ev = evaluate_case_solution(case_text, raw_output)

    assert ev["valid"] is False
    assert ev["illegal_solution"] is True
    assert ev["message"] == "解不合法"
    assert ev["invalid_rows"] == 1
    assert ev["covered_tasks"] == 0
    assert ev["uncovered_tasks"] == 2
    assert ev["official_like_score"] == 200.0


def test_duplicate_backup_courier_is_invalid_and_uncovered() -> None:
    case_text = (
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "T0001\tC001\t10\t0.5\n"
        "T0001\tC009\t11\t0.5\n"
        "T0002\tC002\t20\t0.5\n"
        "T0002\tC009\t21\t0.5\n"
    )

    raw_output = [
        ("T0001", "C001,C009"),
        ("T0002", "C002,C009"),
    ]

    ev = evaluate_case_solution(case_text, raw_output)

    assert ev["valid"] is False
    assert ev["illegal_solution"] is True
    assert ev["message"] == "解不合法"
    assert ev["invalid_rows"] == 1
    assert ev["covered_tasks"] == 0
    assert ev["uncovered_tasks"] == 2
    assert ev["official_like_score"] == 200.0
