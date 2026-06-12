"""Tests for homework-score parsing and unit_6 completion (全作業加權平均)."""

from __future__ import annotations

import asyncio
import io

from openpyxl import Workbook

from app.services.excel_import import (
    COMPLETION_HEADER_MAP,
    HOMEWORK_ASSIGNMENTS,
    HOMEWORK_TOTAL_WEIGHT,
    compute_homework_completion,
    parse_score_excel,
)
from app.services.scoring import get_available_options


# ─── Helpers ──────────────────────────────────────────────────────────


def _build_score_workbook(rows: list[list[object]]) -> bytes:
    """Build a TronClass-style score_list workbook.

    Row 1: group headers (ignored by the parser)
    Row 2: column headers
    Row 3+: data rows (student_id in column 0)
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["帳號", "姓名", "作業(48.0)%"])
    header = ["", ""]
    header.extend(f"{name}({weight:.2f}%)" for name, weight in HOMEWORK_ASSIGNMENTS.items())
    header.append("第一章 課後測驗(7.00%)")
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─── compute_homework_completion ─────────────────────────────────────


def test_total_weight_is_55():
    assert HOMEWORK_TOTAL_WEIGHT == 55.0
    assert len(HOMEWORK_ASSIGNMENTS) == 8


def test_completion_all_full_marks():
    scores = {name: 100.0 for name in HOMEWORK_ASSIGNMENTS}
    assert compute_homework_completion(scores) == 100.0


def test_completion_all_eighty():
    scores = {name: 80.0 for name in HOMEWORK_ASSIGNMENTS}
    assert compute_homework_completion(scores) == 80.0


def test_completion_missing_assignments_count_as_zero():
    # Only the 6% assignment graded at 100 → 600 / 55 = 10.9
    scores = {"初玩類神經網路": 100.0}
    assert compute_homework_completion(scores) == 10.9


def test_completion_empty_is_zero():
    assert compute_homework_completion({}) == 0.0


# ─── parse_score_excel homework columns ──────────────────────────────


def test_parse_homework_columns():
    names = list(HOMEWORK_ASSIGNMENTS)
    row = ["410512345", "王小明"]
    row.extend([90, 85, "未繳", "未批改", 70, 60, 50, "未繳"])
    row.append(88)  # 第一章 課後測驗
    content = _build_score_workbook([row])

    result = parse_score_excel(content)

    assert not result.parse_errors
    hw = {r.assignment_name: r.score for r in result.homework}
    assert hw[names[0]] == 90.0
    assert hw[names[1]] == 85.0
    # 未繳 / 未批改 一律以 0 分計
    assert hw[names[2]] == 0.0
    assert hw[names[3]] == 0.0
    assert hw[names[7]] == 0.0
    assert len(result.homework) == 8

    # quiz columns still parsed into StudentRecord
    assert any(
        r.unit_code == "unit_1" and r.quiz_score == 88.0 for r in result.records
    )

    # homework headers must not be reported as unrecognized
    assert not any(
        name in h for h in result.unrecognized_headers for name in HOMEWORK_ASSIGNMENTS
    )


def test_parse_homework_skips_empty_cells():
    row = ["410512345", "王小明"]
    row.extend([None] * 8)
    row.append(None)
    content = _build_score_workbook([row])

    result = parse_score_excel(content)
    assert result.homework == []


# ─── completion report no longer touches unit_6 ──────────────────────


def test_completion_header_map_excludes_unit_6():
    assert "unit_6" not in COMPLETION_HEADER_MAP.values()


# ─── scoring: unit_6 uses completion_rate as full EXP ────────────────


def test_unit_6_options_full_completion_reaches_tier_s():
    options = asyncio.run(
        get_available_options("unit_6", completion_rate=95.0)
    )
    assert options["expression"]["options"] == ["regal"]
    assert options["pose"]["options"] == ["charging"]


def test_unit_6_options_mid_completion():
    options = asyncio.run(
        get_available_options("unit_6", completion_rate=65.0)
    )
    assert options["expression"]["options"] == ["confident"]
    assert options["pose"]["options"] == ["standing"]


def test_unit_6_options_zero_completion_is_tier_d():
    options = asyncio.run(
        get_available_options("unit_6", completion_rate=0.0)
    )
    assert options["expression"]["options"] == ["weary"]
