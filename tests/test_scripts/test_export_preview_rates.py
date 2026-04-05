"""Tests for the preview rate export script."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from scripts.export_preview_rates import (
    PreviewRateError,
    format_dt,
    run,
)


def write_workbook(path: Path, rows: list[list[object]]) -> None:
    """Create a simple workbook with one worksheet."""

    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_run_exports_preview_rates_with_header_drift(tmp_path: Path) -> None:
    """The exporter should match video columns by code, not by column position."""

    write_workbook(
        tmp_path / "checkpoint.xlsx",
        [
            ["video", "time"],
            ["1-1-01 Intro Video", datetime(2026, 3, 19, 9, 10)],
            ["1-1-02 Second Video", datetime(2026, 3, 26, 10, 30)],
            ["2-1-01 MLP Video", datetime(2026, 3, 26, 10, 30)],
            ["3-1-01 Future Video", None],
        ],
    )

    write_workbook(
        tmp_path / "202603190700.xlsx",
        [
            ["帳號", "1-1-01 Intro Video", "1-1-02 Second Video", "2-1-01 MLP Video"],
            ["alice", "已完成", "未完成", "完成79.9%"],
            ["bob", "完成79.9%", "未完成", "已完成"],
        ],
    )
    write_workbook(
        tmp_path / "202603261010.xlsx",
        [
            [
                "帳號",
                "第一章 課後測驗",
                "1-1-02 Second Video",
                "1-1-01 Intro Video",
                "2-1-01 MLP Video",
            ],
            ["alice", "未完成", "100.0%", "已完成", "完成80.0%"],
            ["bob", "未完成", "完成50.0%", "已完成", "完成20.0%"],
        ],
    )

    output_path = run(tmp_path)
    assert output_path == tmp_path / "preview_rates.csv"

    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows == [
        {
            "student_id": "alice",
            "unit_code": "unit_1",
            "preview_score": "100.00",
            "eligible_video_count": "2",
            "previewed_video_count": "2",
            "latest_checkpoint_at": "2026-03-26 10:30",
            "source_snapshot_at": "2026-03-26 10:10",
        },
        {
            "student_id": "alice",
            "unit_code": "unit_2",
            "preview_score": "100.00",
            "eligible_video_count": "1",
            "previewed_video_count": "1",
            "latest_checkpoint_at": "2026-03-26 10:30",
            "source_snapshot_at": "2026-03-26 10:10",
        },
        {
            "student_id": "bob",
            "unit_code": "unit_1",
            "preview_score": "0.00",
            "eligible_video_count": "2",
            "previewed_video_count": "0",
            "latest_checkpoint_at": "2026-03-26 10:30",
            "source_snapshot_at": "2026-03-26 10:10",
        },
        {
            "student_id": "bob",
            "unit_code": "unit_2",
            "preview_score": "0.00",
            "eligible_video_count": "1",
            "previewed_video_count": "0",
            "latest_checkpoint_at": "2026-03-26 10:30",
            "source_snapshot_at": "2026-03-26 10:10",
        },
    ]


def test_run_raises_when_no_snapshot_exists_before_checkpoint(tmp_path: Path) -> None:
    """A checkpoint without any eligible snapshot should fail the whole run."""

    write_workbook(
        tmp_path / "checkpoint.xlsx",
        [
            ["video", "time"],
            ["1-1-01 Intro Video", datetime(2026, 3, 10, 9, 10)],
        ],
    )
    write_workbook(
        tmp_path / "202603190700.xlsx",
        [
            ["帳號", "1-1-01 Intro Video"],
            ["alice", "已完成"],
        ],
    )

    try:
        run(tmp_path)
    except PreviewRateError as exc:
        assert "No snapshot found for video 1-1-01" in str(exc)
    else:
        raise AssertionError("Expected PreviewRateError for missing eligible snapshot.")


def test_run_falls_back_to_student_id_header_for_legacy_exports(tmp_path: Path, capsys) -> None:
    """Legacy workbooks using 學號 should still be readable with a warning."""

    write_workbook(
        tmp_path / "checkpoint.xlsx",
        [
            ["video", "time"],
            ["1-1-01 Intro Video", datetime(2026, 3, 19, 9, 10)],
        ],
    )
    write_workbook(
        tmp_path / "202603190700.xlsx",
        [
            ["學號", "1-1-01 Intro Video"],
            ["411234567", "已完成"],
        ],
    )

    output_path = run(tmp_path)
    captured = capsys.readouterr()

    assert "uses '學號' instead of '帳號'" in captured.out
    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows == [
        {
            "student_id": "411234567",
            "unit_code": "unit_1",
            "preview_score": "100.00",
            "eligible_video_count": "1",
            "previewed_video_count": "1",
            "latest_checkpoint_at": format_dt(datetime(2026, 3, 19, 9, 10)),
            "source_snapshot_at": format_dt(datetime(2026, 3, 19, 7, 0)),
        }
    ]


def test_run_ignores_inactive_checkpoint_rows_without_video_codes(tmp_path: Path) -> None:
    """Rows without a checkpoint time should be ignored even if the title is malformed."""

    write_workbook(
        tmp_path / "checkpoint.xlsx",
        [
            ["video", "time"],
            ["1-1-01 Intro Video", datetime(2026, 3, 19, 9, 10)],
            ["4-3 AI in Finance: Predicting Google Stock with Recurrent Neural Networks", None],
        ],
    )
    write_workbook(
        tmp_path / "202603190700.xlsx",
        [
            ["帳號", "1-1-01 Intro Video"],
            ["alice", "已完成"],
        ],
    )

    output_path = run(tmp_path)

    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert len(rows) == 1
    assert rows[0]["unit_code"] == "unit_1"


def test_run_aligns_full_account_ids_with_short_student_ids(tmp_path: Path) -> None:
    """The exporter should align mixed 帳號/學號 formats by numeric suffix."""

    write_workbook(
        tmp_path / "checkpoint.xlsx",
        [
            ["video", "time"],
            ["1-1-01 Intro Video", datetime(2026, 3, 12, 9, 10)],
            ["1-1-02 Second Video", datetime(2026, 3, 19, 9, 10)],
        ],
    )
    write_workbook(
        tmp_path / "202603120522.xlsx",
        [
            ["帳號", "1-1-01 Intro Video", "1-1-02 Second Video"],
            ["413570036", "已完成", "未完成"],
        ],
    )
    write_workbook(
        tmp_path / "202603190700.xlsx",
        [
            ["學號", "1-1-01 Intro Video", "1-1-02 Second Video"],
            ["0036", "已完成", "已完成"],
        ],
    )

    output_path = run(tmp_path)

    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows == [
        {
            "student_id": "413570036",
            "unit_code": "unit_1",
            "preview_score": "100.00",
            "eligible_video_count": "2",
            "previewed_video_count": "2",
            "latest_checkpoint_at": "2026-03-19 09:10",
            "source_snapshot_at": "2026-03-19 07:00",
        }
    ]
