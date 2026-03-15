"""Admin dashboard routes — HTML pages and API endpoints."""

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy import case, delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_teacher
from app.models.attribute_rule import AttributeRule
from app.models.card import Card
from app.models.card_config import CardConfig
from app.models.learning_record import LearningRecord
from app.models.student import Student
from app.models.token_transaction import TokenTransaction
from app.models.unit import Unit
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# ─── HTML Pages ───────────────────────────────────────────────────────


@router.get("/admin")
async def admin_dashboard(
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Admin dashboard overview."""
    # Total students
    total_students = (
        await db.execute(select(func.count(Student.id)))
    ).scalar() or 0

    # Total cards
    total_cards = (
        await db.execute(select(func.count(Card.id)))
    ).scalar() or 0

    # Completed cards
    completed_cards = (
        await db.execute(
            select(func.count(Card.id)).where(Card.status == "completed")
        )
    ).scalar() or 0

    # Average scores per unit
    units_result = await db.execute(select(Unit).order_by(Unit.sort_order))
    units = units_result.scalars().all()

    unit_stats = []
    for unit in units:
        avg_quiz = (
            await db.execute(
                select(func.avg(LearningRecord.quiz_score)).where(
                    LearningRecord.unit_id == unit.id
                )
            )
        ).scalar()
        avg_completion = (
            await db.execute(
                select(func.avg(LearningRecord.completion_rate)).where(
                    LearningRecord.unit_id == unit.id
                )
            )
        ).scalar()
        record_count = (
            await db.execute(
                select(func.count(LearningRecord.id)).where(
                    LearningRecord.unit_id == unit.id
                )
            )
        ).scalar() or 0

        unit_stats.append({
            "code": unit.code,
            "name": unit.name,
            "avg_quiz": round(avg_quiz, 1) if avg_quiz else 0,
            "avg_completion": round(avg_completion, 1) if avg_completion else 0,
            "record_count": record_count,
        })

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "total_students": total_students,
            "total_cards": total_cards,
            "completed_cards": completed_cards,
            "unit_stats": unit_stats,
        },
    )


@router.get("/admin/students")
async def admin_students(
    request: Request,
    q: str = "",
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Student list with optional search."""
    role_order = case(
        (Student.role == "admin", 0),
        (Student.role == "teacher", 1),
        else_=2,
    )
    query = select(Student).order_by(role_order, Student.student_id.asc())
    if q:
        query = query.where(
            Student.name.contains(q) | Student.student_id.contains(q)
        )
    result = await db.execute(query)
    students = result.scalars().all()

    # Get card counts per student
    student_data = []
    for s in students:
        card_count = (
            await db.execute(
                select(func.count(Card.id)).where(Card.student_id == s.id)
            )
        ).scalar() or 0
        student_data.append({"student": s, "card_count": card_count})

    return templates.TemplateResponse(
        request,
        "admin/students.html",
        {"user": user, "student_data": student_data, "search_query": q},
    )


@router.get("/admin/students/{student_pk}")
async def admin_student_detail(
    request: Request,
    student_pk: int,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Single student detail page."""
    result = await db.execute(select(Student).where(Student.id == student_pk))
    student = result.scalar_one_or_none()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")

    # All units for editable records table
    units_result = await db.execute(select(Unit).order_by(Unit.sort_order))
    units = units_result.scalars().all()

    # Learning records indexed by unit_id
    lr_result = await db.execute(
        select(LearningRecord).where(LearningRecord.student_id == student.id)
    )
    records_by_unit = {r.unit_id: r for r in lr_result.scalars().all()}

    # Cards
    cards_result = await db.execute(
        select(Card)
        .where(Card.student_id == student.id)
        .order_by(Card.created_at.desc())
    )
    cards = cards_result.scalars().all()

    # Token transactions
    from app.models.token_transaction import TokenTransaction
    txn_result = await db.execute(
        select(TokenTransaction)
        .where(TokenTransaction.student_id == student.id)
        .order_by(TokenTransaction.created_at.desc())
    )
    token_transactions = txn_result.scalars().all()

    # Achievements
    from app.models.achievement import StudentAchievement, ACHIEVEMENT_TYPES
    ach_result = await db.execute(
        select(StudentAchievement)
        .where(StudentAchievement.student_id == student.id)
    )
    student_achievements = ach_result.scalars().all()
    earned_keys = {a.achievement_key: a for a in student_achievements}

    return templates.TemplateResponse(
        request,
        "admin/student_detail.html",
        {
            "user": user,
            "student": student,
            "units": units,
            "records_by_unit": records_by_unit,
            "cards": cards,
            "token_transactions": token_transactions,
            "earned_keys": earned_keys,
            "achievement_types": ACHIEVEMENT_TYPES,
        },
    )


@router.get("/admin/import")
async def admin_import_page(
    request: Request,
    user: Student = Depends(require_teacher),
):
    """CSV import page."""
    return templates.TemplateResponse(
        request,
        "admin/import.html",
        {"user": user},
    )


@router.get("/admin/roster")
async def admin_roster_page(
    request: Request,
    user: Student = Depends(require_teacher),
):
    """Roster CSV import page."""
    return templates.TemplateResponse(
        request,
        "admin/roster.html",
        {"user": user},
    )


# ─── API Endpoints ────────────────────────────────────────────────────


@router.get("/api/admin/dashboard")
async def api_admin_dashboard(
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Return JSON statistics for admin dashboard."""
    total_students = (
        await db.execute(select(func.count(Student.id)))
    ).scalar() or 0
    total_cards = (
        await db.execute(select(func.count(Card.id)))
    ).scalar() or 0
    completed_cards = (
        await db.execute(
            select(func.count(Card.id)).where(Card.status == "completed")
        )
    ).scalar() or 0

    return {
        "total_students": total_students,
        "total_cards": total_cards,
        "completed_cards": completed_cards,
    }


@router.put("/api/admin/students/{student_pk}")
async def api_admin_update_student(
    student_pk: int,
    payload: dict = Body(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Update student info."""
    result = await db.execute(select(Student).where(Student.id == student_pk))
    student = result.scalar_one_or_none()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")

    if "name" in payload and payload["name"]:
        student.name = str(payload["name"]).strip()

    if "nickname" in payload:
        nick = payload["nickname"]
        if nick is not None and nick != "":
            nick = str(nick).strip()
            if not re.match(r'^[a-zA-Z0-9]{1,18}$', nick):
                raise HTTPException(
                    status_code=400,
                    detail="Nickname must be alphanumeric, max 18 characters",
                )
            student.nickname = nick
        else:
            student.nickname = None

    if "student_id" in payload and payload["student_id"]:
        new_sid = str(payload["student_id"]).strip()
        if new_sid != student.student_id:
            existing = await db.execute(
                select(Student).where(
                    Student.student_id == new_sid, Student.id != student_pk
                )
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=400, detail="Student ID already in use"
                )
            student.student_id = new_sid

    if "role" in payload:
        if payload["role"] not in ("student", "teacher", "admin"):
            raise HTTPException(status_code=400, detail="Invalid role")
        student.role = payload["role"]

    if "tokens" in payload:
        from app.models.token_transaction import TokenTransaction
        old_tokens = student.tokens or 0
        new_tokens = int(payload["tokens"])
        student.tokens = new_tokens
        if old_tokens != new_tokens:
            db.add(TokenTransaction(
                student_id=student.id,
                amount=new_tokens - old_tokens,
                reason=f"點數手動調整（{old_tokens} → {new_tokens}）",
                created_at=datetime.now(timezone.utc),
            ))

    student.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(student)

    return {
        "id": student.id,
        "email": student.email,
        "name": student.name,
        "nickname": student.nickname,
        "student_id": student.student_id,
        "role": student.role,
        "tokens": student.tokens,
    }


@router.put("/api/admin/students/{student_pk}/records/{unit_id}")
async def api_admin_update_record(
    student_pk: int,
    unit_id: int,
    payload: dict = Body(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Update or create a learning record for a student+unit."""
    # Verify student exists
    student = (
        await db.execute(select(Student).where(Student.id == student_pk))
    ).scalar_one_or_none()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")

    # Verify unit exists
    unit = (
        await db.execute(select(Unit).where(Unit.id == unit_id))
    ).scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Find or create record
    existing = (
        await db.execute(
            select(LearningRecord).where(
                LearningRecord.student_id == student_pk,
                LearningRecord.unit_id == unit_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = LearningRecord(student_id=student_pk, unit_id=unit_id)
        db.add(existing)

    for field in ("preview_score", "completion_rate", "quiz_score"):
        if field in payload:
            val = payload[field]
            setattr(existing, field, float(val) if val is not None and val != "" else None)

    existing.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(existing)

    return {
        "id": existing.id,
        "student_id": existing.student_id,
        "unit_id": existing.unit_id,
        "preview_score": existing.preview_score,
        "completion_rate": existing.completion_rate,
        "quiz_score": existing.quiz_score,
    }


@router.post("/api/admin/students/{student_pk}/unbind")
async def api_admin_unbind_student(
    student_pk: int,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Unbind (delete) a student and all related records."""
    student = (
        await db.execute(select(Student).where(Student.id == student_pk))
    ).scalar_one_or_none()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")

    student_name = student.name

    # Reset email to placeholder (preserves roster entry)
    student.email = f"__unbound__{student.student_id}@placeholder"
    student.nickname = None
    student.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok", "message": f"已解除 {student_name} 的 Email 綁定"}


@router.post("/api/admin/students/batch-tokens")
async def api_admin_batch_tokens(
    payload: dict = Body(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Grant tokens to multiple students at once.

    Payload: { student_ids: [int, ...], amount: int, note: str | None,
               achievement_key: str | None }
    When achievement_key is provided, students who already have the achievement
    are skipped (idempotent per-student).
    """
    from app.models.token_transaction import TokenTransaction
    from app.models.achievement import StudentAchievement, ACHIEVEMENT_TYPES

    student_ids = payload.get("student_ids", [])
    amount = payload.get("amount", 0)
    note = payload.get("note", "").strip() or None
    achievement_key = payload.get("achievement_key", "").strip() or None

    if not student_ids:
        raise HTTPException(status_code=400, detail="未選擇任何學生")
    if not isinstance(amount, int) or amount <= 0:
        raise HTTPException(status_code=400, detail="發放點數必須為正整數")
    if amount > 9999:
        raise HTTPException(status_code=400, detail="單次發放上限為 9999 點")
    if achievement_key and achievement_key not in ACHIEVEMENT_TYPES:
        raise HTTPException(status_code=400, detail=f"無效的成就代碼：{achievement_key}")

    result = await db.execute(
        select(Student).where(Student.id.in_(student_ids))
    )
    students = result.scalars().all()

    if not students:
        raise HTTPException(status_code=404, detail="找不到指定學生")

    # If an achievement key is provided, find students who already have it
    already_earned_ids: set[int] = set()
    if achievement_key:
        earned_result = await db.execute(
            select(StudentAchievement.student_id).where(
                StudentAchievement.achievement_key == achievement_key,
                StudentAchievement.student_id.in_(student_ids),
            )
        )
        already_earned_ids = {row[0] for row in earned_result.all()}

    now = datetime.now(timezone.utc)
    updated_ids = []
    skipped_names = []

    for s in students:
        if achievement_key and s.id in already_earned_ids:
            skipped_names.append(s.name)
            continue

        s.tokens = (s.tokens or 0) + amount
        s.updated_at = now
        tx_reason = note or (ACHIEVEMENT_TYPES[achievement_key]["label"] if achievement_key else None)
        tx = TokenTransaction(
            student_id=s.id,
            amount=amount,
            reason=tx_reason,
            created_at=now,
        )
        db.add(tx)

        if achievement_key:
            await db.flush()  # get tx.id
            db.add(StudentAchievement(
                student_id=s.id,
                achievement_key=achievement_key,
                token_transaction_id=tx.id,
                awarded_at=now,
            ))

        updated_ids.append(s.id)

    await db.commit()
    return {
        "updated": len(updated_ids),
        "skipped": len(skipped_names),
        "skipped_names": skipped_names,
        "amount": amount,
        "student_ids": updated_ids,
    }


@router.post("/api/admin/import")
async def api_admin_import(
    file: UploadFile = File(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Import learning records from CSV.

    Expected CSV columns:
    student_id, unit_code, preview_score, completion_rate, quiz_score
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file")

    content = await file.read()
    text = content.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(text))

    created = 0
    updated = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):  # row 1 = header
        try:
            sid = row.get("student_id", "").strip()
            unit_code = row.get("unit_code", "").strip()

            if not sid or not unit_code:
                errors.append(f"Row {row_num}: missing student_id or unit_code")
                continue

            # Find student by student_id field (not PK)
            student_result = await db.execute(
                select(Student).where(Student.student_id == sid)
            )
            student = student_result.scalar_one_or_none()
            if student is None:
                errors.append(f"Row {row_num}: student '{sid}' not found")
                continue

            # Find unit
            unit_result = await db.execute(
                select(Unit).where(Unit.code == unit_code)
            )
            unit = unit_result.scalar_one_or_none()
            if unit is None:
                errors.append(f"Row {row_num}: unit '{unit_code}' not found")
                continue

            # Parse scores
            preview = _parse_float(row.get("preview_score"))
            completion = _parse_float(row.get("completion_rate"))
            quiz = _parse_float(row.get("quiz_score"))

            # Upsert learning record
            existing_result = await db.execute(
                select(LearningRecord).where(
                    LearningRecord.student_id == student.id,
                    LearningRecord.unit_id == unit.id,
                )
            )
            existing = existing_result.scalar_one_or_none()

            if existing:
                if preview is not None:
                    existing.preview_score = preview
                if completion is not None:
                    existing.completion_rate = completion
                if quiz is not None:
                    existing.quiz_score = quiz
                existing.updated_at = datetime.now(timezone.utc)
                updated += 1
            else:
                lr = LearningRecord(
                    student_id=student.id,
                    unit_id=unit.id,
                    preview_score=preview,
                    completion_rate=completion,
                    quiz_score=quiz,
                )
                db.add(lr)
                created += 1

        except Exception as e:
            errors.append(f"Row {row_num}: {e}")

    await db.commit()

    return {
        "created": created,
        "updated": updated,
        "total": created + updated,
        "errors": errors[:20],  # cap error list
    }


@router.post("/api/admin/roster")
async def api_admin_roster(
    file: UploadFile = File(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Import student roster from CSV.

    Expected CSV columns: id (學號), name (姓名)
    Creates unbound roster students with placeholder emails.
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file")

    content = await file.read()
    text = content.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(text))

    created = 0
    updated = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):  # row 1 = header
        try:
            sid = row.get("id", "").strip()
            name = row.get("name", "").strip()

            if not sid or not name:
                errors.append(f"Row {row_num}: missing id or name")
                continue

            # Check if student already exists
            result = await db.execute(
                select(Student).where(Student.student_id == sid)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.name = name
                existing.updated_at = datetime.now(timezone.utc)
                updated += 1
            else:
                placeholder_email = f"__unbound__{sid}@placeholder"
                student = Student(
                    student_id=sid,
                    name=name,
                    email=placeholder_email,
                    role="student",
                    tokens=0,
                )
                db.add(student)
                created += 1

        except Exception as e:
            errors.append(f"Row {row_num}: {e}")

    await db.commit()

    return {
        "created": created,
        "updated": updated,
        "total": created + updated,
        "errors": errors[:20],
    }


def _parse_float(val: str | None) -> float | None:
    """Parse a CSV cell to float, returning None for empty/invalid."""
    if val is None:
        return None
    val = val.strip()
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


# ─── Attribute Rules Management ──────────────────────────────────────

UNIT_NAMES = {
    "unit_1": "先備知識",
    "unit_2": "MLP",
    "unit_3": "CNN",
    "unit_4": "RNN",
    "unit_5": "進階技術",
    "unit_6": "自主學習",
}


@router.get("/admin/rules")
async def admin_rules(
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Attribute rules management page."""
    result = await db.execute(
        select(AttributeRule).order_by(
            AttributeRule.unit_code, AttributeRule.sort_order, AttributeRule.tier
        )
    )
    rules = result.scalars().all()

    # Group by unit_code → attribute_type
    from collections import OrderedDict
    grouped: dict[str, dict[str, list]] = OrderedDict()
    for rule in rules:
        if rule.unit_code not in grouped:
            grouped[rule.unit_code] = OrderedDict()
        if rule.attribute_type not in grouped[rule.unit_code]:
            grouped[rule.unit_code][rule.attribute_type] = []
        grouped[rule.unit_code][rule.attribute_type].append({
            "id": rule.id,
            "tier": rule.tier,
            "options": json.loads(rule.options),
            "labels": json.loads(rule.labels),
            "sort_order": rule.sort_order,
        })

    return templates.TemplateResponse(
        request,
        "admin/rules.html",
        {
            "user": user,
            "grouped": grouped,
            "unit_names": UNIT_NAMES,
        },
    )


@router.put("/api/admin/rules/{rule_id}")
async def api_admin_update_rule(
    rule_id: int,
    payload: dict = Body(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Update a single attribute rule's options and labels."""
    result = await db.execute(
        select(AttributeRule).where(AttributeRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    if "options" in payload:
        if not isinstance(payload["options"], list):
            raise HTTPException(status_code=400, detail="options must be a JSON array")
        rule.options = json.dumps(payload["options"], ensure_ascii=False)

    if "labels" in payload:
        if not isinstance(payload["labels"], dict):
            raise HTTPException(status_code=400, detail="labels must be a JSON object")
        rule.labels = json.dumps(payload["labels"], ensure_ascii=False)

    rule.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(rule)

    return {
        "id": rule.id,
        "unit_code": rule.unit_code,
        "attribute_type": rule.attribute_type,
        "tier": rule.tier,
        "options": json.loads(rule.options),
        "labels": json.loads(rule.labels),
    }


@router.post("/api/admin/rules")
async def api_admin_create_rule(
    payload: dict = Body(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Create a new attribute rule."""
    required = ["unit_code", "attribute_type", "tier", "options", "labels"]
    for field in required:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"Missing field: {field}")

    if not isinstance(payload["options"], list):
        raise HTTPException(status_code=400, detail="options must be a JSON array")
    if not isinstance(payload["labels"], dict):
        raise HTTPException(status_code=400, detail="labels must be a JSON object")

    # Check duplicates
    existing = await db.execute(
        select(AttributeRule).where(
            AttributeRule.unit_code == payload["unit_code"],
            AttributeRule.attribute_type == payload["attribute_type"],
            AttributeRule.tier == payload["tier"],
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Rule already exists for this unit_code + attribute_type + tier",
        )

    rule = AttributeRule(
        unit_code=payload["unit_code"],
        attribute_type=payload["attribute_type"],
        tier=payload["tier"],
        options=json.dumps(payload["options"], ensure_ascii=False),
        labels=json.dumps(payload["labels"], ensure_ascii=False),
        sort_order=payload.get("sort_order", 0),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    return {
        "id": rule.id,
        "unit_code": rule.unit_code,
        "attribute_type": rule.attribute_type,
        "tier": rule.tier,
        "options": json.loads(rule.options),
        "labels": json.loads(rule.labels),
    }


@router.delete("/api/admin/rules/{rule_id}")
async def api_admin_delete_rule(
    rule_id: int,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Delete an attribute rule."""
    result = await db.execute(
        select(AttributeRule).where(AttributeRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    await db.delete(rule)
    await db.commit()
    return {"status": "ok"}
