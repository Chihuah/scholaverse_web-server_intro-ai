"""Tests for copy-records-to-admin endpoint."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.dependencies import require_admin
from app.models.learning_record import LearningRecord
from app.models.student import Student
from app.models.unit import Unit
from main import app


@pytest.fixture()
async def admin_user(db_session):
    """Create an admin user as the copy target."""
    admin = Student(
        email="admin@example.com",
        student_id="ADMIN001",
        name="Admin",
        role="admin",
        tokens=0,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)
    return admin


@pytest.fixture()
async def source_student(db_session):
    """Create a regular student as the copy source."""
    student = Student(
        email="student@example.com",
        student_id="S001",
        name="王小明",
        role="student",
        tokens=0,
    )
    db_session.add(student)
    await db_session.commit()
    await db_session.refresh(student)
    return student


@pytest.fixture()
async def six_units(db_session):
    """Create 6 units for use in tests."""
    units = [
        Unit(code=f"unit_{i}", name=f"Unit {i}", unlock_attribute=f"attr_{i}", sort_order=i)
        for i in range(1, 7)
    ]
    db_session.add_all(units)
    await db_session.commit()
    for u in units:
        await db_session.refresh(u)
    return units


@pytest.fixture(autouse=True)
async def override_admin(admin_user):
    """Bypass auth and require_admin checks for copy-records tests."""
    app.dependency_overrides[require_admin] = lambda: admin_user
    yield
    app.dependency_overrides.pop(require_admin, None)


async def test_happy_path_copies_six_records(client, db_session, source_student, six_units):
    """Admin copies from a student with 6 records — all score fields match and copied=6."""
    # Arrange: create 6 learning records for source student
    records = [
        LearningRecord(
            student_id=source_student.id,
            unit_id=u.id,
            preview_score=float(10 * i),
            pretest_score=float(20 * i),
            completion_rate=float(5 * i),
            quiz_score=float(15 * i),
        )
        for i, u in enumerate(six_units, start=1)
    ]
    db_session.add_all(records)
    await db_session.commit()

    # Act
    response = await client.post(
        f"/api/admin/students/{source_student.id}/copy-records-to-admin"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["copied"] == 6
    assert data["source_name"] == "王小明"

    # Verify DB: admin should have exactly 6 records matching source scores
    admin = (await db_session.execute(
        select(Student).where(Student.email == "admin@example.com")
    )).scalar_one()
    admin_records = (await db_session.execute(
        select(LearningRecord).where(LearningRecord.student_id == admin.id)
        .order_by(LearningRecord.unit_id)
    )).scalars().all()
    assert len(admin_records) == 6
    for src, dst in zip(
        sorted(records, key=lambda r: r.unit_id),
        sorted(admin_records, key=lambda r: r.unit_id),
    ):
        assert dst.preview_score == src.preview_score
        assert dst.pretest_score == src.pretest_score
        assert dst.completion_rate == src.completion_rate
        assert dst.quiz_score == src.quiz_score


async def test_full_sync_removes_extra_admin_records(
    client, db_session, source_student, six_units
):
    """Admin has records for units 1-6, source has only units 1-3 — after copy admin has only 1-3."""
    admin = (await db_session.execute(
        select(Student).where(Student.email == "admin@example.com")
    )).scalar_one()

    # Admin pre-has records for all 6 units
    admin_records = [
        LearningRecord(
            student_id=admin.id,
            unit_id=u.id,
            preview_score=99.0,
            pretest_score=99.0,
            completion_rate=99.0,
            quiz_score=99.0,
        )
        for u in six_units
    ]
    db_session.add_all(admin_records)

    # Source has only first 3 units
    source_records = [
        LearningRecord(
            student_id=source_student.id,
            unit_id=u.id,
            preview_score=50.0,
            pretest_score=50.0,
            completion_rate=50.0,
            quiz_score=50.0,
        )
        for u in six_units[:3]
    ]
    db_session.add_all(source_records)
    await db_session.commit()

    response = await client.post(
        f"/api/admin/students/{source_student.id}/copy-records-to-admin"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["copied"] == 3

    # Admin should only have 3 records now
    remaining = (await db_session.execute(
        select(LearningRecord).where(LearningRecord.student_id == admin.id)
    )).scalars().all()
    assert len(remaining) == 3
    unit_ids = {r.unit_id for r in remaining}
    assert unit_ids == {six_units[0].id, six_units[1].id, six_units[2].id}


async def test_self_copy_returns_skipped(client, db_session, six_units):
    """Admin copying from their own PK returns status=skipped and DB is unchanged."""
    admin = (await db_session.execute(
        select(Student).where(Student.email == "admin@example.com")
    )).scalar_one()

    # Give admin one record
    record = LearningRecord(
        student_id=admin.id,
        unit_id=six_units[0].id,
        preview_score=77.0,
        pretest_score=77.0,
        completion_rate=77.0,
        quiz_score=77.0,
    )
    db_session.add(record)
    await db_session.commit()

    response = await client.post(
        f"/api/admin/students/{admin.id}/copy-records-to-admin"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "skipped"
    assert data["copied"] == 0

    # DB unchanged — admin still has exactly 1 record
    records_after = (await db_session.execute(
        select(LearningRecord).where(LearningRecord.student_id == admin.id)
    )).scalars().all()
    assert len(records_after) == 1
    assert records_after[0].preview_score == 77.0


async def test_source_not_found_returns_404(client):
    """Non-existent source PK should return 404."""
    response = await client.post("/api/admin/students/99999/copy-records-to-admin")
    assert response.status_code == 404


async def test_teacher_role_is_forbidden(client, db_session, source_student):
    """A teacher (non-admin) calling the endpoint should get 403."""
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

    # Override require_admin to return a teacher — it should raise 403
    from app.dependencies import get_current_user

    app.dependency_overrides[require_admin] = lambda: (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(status_code=403, detail="Admin access required")
    )

    try:
        response = await client.post(
            f"/api/admin/students/{source_student.id}/copy-records-to-admin"
        )
        assert response.status_code == 403
    finally:
        # Restore admin override for subsequent tests
        from app.dependencies import require_admin as _req_admin
        admin = (await db_session.execute(
            select(Student).where(Student.email == "admin@example.com")
        )).scalar_one()
        app.dependency_overrides[_req_admin] = lambda: admin


async def test_source_with_zero_records_clears_admin(client, db_session, source_student, six_units):
    """Source with zero records → copied=0, admin's records are cleared."""
    admin = (await db_session.execute(
        select(Student).where(Student.email == "admin@example.com")
    )).scalar_one()

    # Admin pre-has 2 records
    admin_records = [
        LearningRecord(
            student_id=admin.id,
            unit_id=u.id,
            preview_score=88.0,
            pretest_score=88.0,
            completion_rate=88.0,
            quiz_score=88.0,
        )
        for u in six_units[:2]
    ]
    db_session.add_all(admin_records)
    await db_session.commit()

    # Source has no records at all
    response = await client.post(
        f"/api/admin/students/{source_student.id}/copy-records-to-admin"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["copied"] == 0

    # Admin's records should be empty now
    remaining = (await db_session.execute(
        select(LearningRecord).where(LearningRecord.student_id == admin.id)
    )).scalars().all()
    assert len(remaining) == 0
