"""Tests for preview-rate import endpoints."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.dependencies import require_teacher
from app.models.learning_record import LearningRecord
from app.models.student import Student
from app.models.unit import Unit
from main import app


@pytest.fixture()
async def teacher_user(db_session):
    """Create a teacher user for admin endpoint access."""

    teacher = Student(
        email="teacher@example.com",
        student_id="T001",
        name="Teacher",
        role="teacher",
        tokens=0,
    )
    db_session.add(teacher)
    await db_session.commit()
    await db_session.refresh(teacher)
    return teacher


@pytest.fixture()
async def preview_import_data(db_session):
    """Create students and units required by the preview-rate import."""

    student_one = Student(
        email="s1@example.com",
        student_id="413570036",
        name="Student One",
        role="student",
        tokens=0,
    )
    student_two = Student(
        email="s2@example.com",
        student_id="413570062",
        name="Student Two",
        role="student",
        tokens=0,
    )
    unit_one = Unit(
        code="unit_1",
        name="Unit 1",
        unlock_attribute="race_gender",
        sort_order=1,
    )
    unit_two = Unit(
        code="unit_2",
        name="Unit 2",
        unlock_attribute="class_body",
        sort_order=2,
    )
    db_session.add_all([student_one, student_two, unit_one, unit_two])
    await db_session.commit()
    await db_session.refresh(student_one)
    await db_session.refresh(student_two)
    await db_session.refresh(unit_one)
    await db_session.refresh(unit_two)
    return {
        "students": [student_one, student_two],
        "units": [unit_one, unit_two],
    }


@pytest.fixture()
def preview_rates_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point DATA_DIR to a temp folder containing preview_rates.csv."""

    csv_path = tmp_path / "preview_rates.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "student_id",
                "unit_code",
                "preview_score",
                "eligible_video_count",
                "previewed_video_count",
                "latest_checkpoint_at",
                "source_snapshot_at",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "student_id": "0036",
                "unit_code": "unit_1",
                "preview_score": "100.00",
                "eligible_video_count": "5",
                "previewed_video_count": "5",
                "latest_checkpoint_at": "2026-03-26 10:30",
                "source_snapshot_at": "2026-03-26 10:10",
            }
        )
        writer.writerow(
            {
                "student_id": "413570062",
                "unit_code": "unit_2",
                "preview_score": "50.00",
                "eligible_video_count": "2",
                "previewed_video_count": "1",
                "latest_checkpoint_at": "2026-03-26 10:30",
                "source_snapshot_at": "2026-03-26 10:10",
            }
        )
        writer.writerow(
            {
                "student_id": "9999",
                "unit_code": "unit_1",
                "preview_score": "25.00",
                "eligible_video_count": "4",
                "previewed_video_count": "1",
                "latest_checkpoint_at": "2026-03-19 09:10",
                "source_snapshot_at": "2026-03-19 07:00",
            }
        )

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    return csv_path


@pytest.fixture(autouse=True)
async def override_teacher(teacher_user):
    """Bypass auth and teacher checks for admin tests."""

    app.dependency_overrides[require_teacher] = lambda: teacher_user
    yield
    app.dependency_overrides.pop(require_teacher, None)


async def test_preview_rates_preview_reads_csv(
    client, preview_import_data, preview_rates_csv
):
    """Preview endpoint should summarize file-driven updates."""

    response = await client.post("/api/admin/import-preview-rates/preview")

    assert response.status_code == 200
    assert "preview_rates.csv" in response.text
    assert "9999" in response.text


async def test_preview_rates_commit_updates_learning_records(
    client, db_session, preview_import_data, preview_rates_csv
):
    """Commit endpoint should upsert preview_score from preview_rates.csv."""

    response = await client.post("/api/admin/import-preview-rates/commit")

    assert response.status_code == 200

    rows = (
        await db_session.execute(
            select(LearningRecord).order_by(LearningRecord.student_id, LearningRecord.unit_id)
        )
    ).scalars().all()

    assert len(rows) == 2
    assert rows[0].preview_score == 100.0
    assert rows[1].preview_score == 50.0
