"""Admin dashboard routes — HTML pages and API endpoints."""

import csv
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import case, delete, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_teacher
from app.models.achievement import ACHIEVEMENT_TYPES, StudentAchievement
from app.models.attribute_rule import AttributeRule
from app.models.card import Card
from app.models.system_setting import SystemSetting
from app.models.card_config import CardConfig
from app.models.learning_record import LearningRecord
from app.models.student import Student
from app.models.token_transaction import TokenTransaction
from app.models.unit import Unit
from app.services.excel_import import (
    ExcelParseResult,
    StudentRecord,
    parse_completion_excel,
    parse_score_excel,
)
from app.config import settings
from app.services.storage import get_storage_service
from app.services.system_settings import (
    OLLAMA_MODEL_SUGGESTIONS,
    SYSTEM_SETTING_LABELS,
    get_system_setting,
    get_system_settings_map,
    set_system_setting,
)
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])
_PREVIEW_RATES_FILENAME = "preview_rates.csv"
_TIER_ORDER = ["S", "A", "B", "C", "D"]


def _parse_card_snapshot(card: Card) -> dict:
    raw = card.config_snapshot
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _inclusive_tiers_for_admin(tier: str) -> list[str]:
    try:
        start = _TIER_ORDER.index(tier)
    except ValueError:
        return [tier]
    return _TIER_ORDER[start:]


def _merge_rule_dicts(rule_dicts: list[dict]) -> tuple[list[str], dict[str, str]]:
    merged_options: list[str] = []
    merged_labels: dict[str, str] = {}
    seen: set[str] = set()

    for rule in rule_dicts:
        for option in rule["options"]:
            if option in seen:
                continue
            seen.add(option)
            merged_options.append(option)
            label = rule["labels"].get(option)
            if label is not None:
                merged_labels[option] = label

    return merged_options, merged_labels


def _build_simulation_reuse_url(card: Card, student: Student | None = None) -> str:
    snapshot = _parse_card_snapshot(card)
    meta = snapshot.pop("__meta", {}) if isinstance(snapshot.get("__meta"), dict) else {}
    params: dict[str, str | int] = {}

    for key in [
        "race", "gender", "class", "body", "equipment", "weapon_quality",
        "weapon_type", "background", "expression", "pose"
    ]:
        value = snapshot.get(key)
        if value not in (None, ""):
            params[key] = value

    level = snapshot.get("level") or card.level_number
    rarity = snapshot.get("rarity") or card.rarity
    nickname = meta.get("nickname") or getattr(student, "nickname", None) or ""
    seed = meta.get("seed")
    if seed in (None, "", -1, "-1"):
        seed = card.seed

    if level is not None:
        params["level"] = int(level)
    if rarity:
        params["rarity"] = str(rarity)
    if nickname:
        params["nickname"] = str(nickname)
    if seed not in (None, ""):
        params["seed"] = int(seed)

    query = urlencode(params)
    return f"/admin/simulation?{query}" if query else "/admin/simulation"


def _student_unit_exp(unit: Unit, lr: LearningRecord | None) -> float | None:
    if lr is None:
        return None
    if unit.code == "unit_6":
        return lr.completion_rate
    pv = (lr.preview_score or 0.0) * 0.2
    cv = (lr.completion_rate or 0.0) * 0.4
    qv = (lr.quiz_score or 0.0) * 0.4
    return round(pv + cv + qv, 1)


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

    student_ids = [s.id for s in students]

    # Units ordered for EXP columns
    units_result = await db.execute(select(Unit).order_by(Unit.sort_order))
    units = units_result.scalars().all()

    # Card counts — one query
    card_counts: dict[int, int] = {}
    if student_ids:
        cc_result = await db.execute(
            select(Card.student_id, func.count(Card.id))
            .where(Card.student_id.in_(student_ids))
            .group_by(Card.student_id)
        )
        card_counts = {row[0]: row[1] for row in cc_result.all()}

    # Learning records — one query, grouped by student_id → unit_id
    lr_map: dict[int, dict[int, LearningRecord]] = {}
    if student_ids:
        lr_result = await db.execute(
            select(LearningRecord).where(
                LearningRecord.student_id.in_(student_ids)
            )
        )
        for lr in lr_result.scalars().all():
            lr_map.setdefault(lr.student_id, {})[lr.unit_id] = lr

    student_data = []
    for s in students:
        unit_exps = []
        for u in units:
            lr = lr_map.get(s.id, {}).get(u.id)
            unit_exps.append({"unit": u, "exp": _student_unit_exp(u, lr)})
        student_data.append({
            "student": s,
            "card_count": card_counts.get(s.id, 0),
            "unit_exps": unit_exps,
        })

    return templates.TemplateResponse(
        request,
        "admin/students.html",
        {"user": user, "student_data": student_data, "search_query": q, "units": units},
    )


@router.get("/admin/students/export-selected")
async def admin_students_export_selected(
    student_ids: list[int] = Query(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Download selected student summary rows as CSV."""
    if not student_ids:
        raise HTTPException(status_code=400, detail="請先勾選至少一位學生")

    role_order = case(
        (Student.role == "admin", 0),
        (Student.role == "teacher", 1),
        else_=2,
    )
    students = (
        await db.execute(
            select(Student)
            .where(Student.id.in_(student_ids))
            .order_by(role_order, Student.student_id.asc())
        )
    ).scalars().all()

    if not students:
        raise HTTPException(status_code=404, detail="找不到指定學生")

    selected_ids = [s.id for s in students]
    units = (
        await db.execute(select(Unit).order_by(Unit.sort_order))
    ).scalars().all()

    card_counts: dict[int, int] = {}
    cc_result = await db.execute(
        select(Card.student_id, func.count(Card.id))
        .where(Card.student_id.in_(selected_ids))
        .group_by(Card.student_id)
    )
    card_counts = {row[0]: row[1] for row in cc_result.all()}

    lr_map: dict[int, dict[int, LearningRecord]] = {}
    lr_result = await db.execute(
        select(LearningRecord).where(LearningRecord.student_id.in_(selected_ids))
    )
    for lr in lr_result.scalars().all():
        lr_map.setdefault(lr.student_id, {})[lr.unit_id] = lr

    output = io.StringIO()
    writer = csv.writer(output)
    headers = ["學號", "姓名", "Email", "角色", "代幣", "卡牌"]
    headers.extend([f"Ch{i}" for i in range(1, len(units) + 1)])
    writer.writerow(headers)

    for student in students:
        email = "" if (student.email or "").startswith("__unbound__") else (student.email or "")
        row = [
            student.student_id or "",
            student.name or "",
            email,
            student.role or "",
            student.tokens or 0,
            card_counts.get(student.id, 0),
        ]
        for unit in units:
            exp = _student_unit_exp(unit, lr_map.get(student.id, {}).get(unit.id))
            row.append("" if exp is None else exp)
        writer.writerow(row)

    filename = f"selected-students-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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


@router.get("/admin/cards/{card_id}")
async def admin_card_detail(
    card_id: int,
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """管理者卡牌詳情頁面。"""
    result = await db.execute(
        select(Card, Student)
        .join(Student, Card.student_id == Student.id)
        .where(Card.id == card_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到此卡牌。")

    card, student = row
    return templates.TemplateResponse(
        request,
        "admin/simulation_card_detail.html",
        {
            "user": user,
            "card": card,
            "student": student,
            "page_title": f"卡牌詳情 #{card.id}",
            "back_url": f"/admin/students/{student.id}",
            "back_label": f"返回 {student.name} 的資料",
            "show_admin_debug": True,
            "reuse_simulation_url": _build_simulation_reuse_url(card, student),
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

    for field in ("preview_score", "pretest_score", "completion_rate", "quiz_score"):
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
        "pretest_score": existing.pretest_score,
        "completion_rate": existing.completion_rate,
        "quiz_score": existing.quiz_score,
    }


@router.post("/api/admin/students/{source_student_pk}/copy-records-to-admin")
async def api_admin_copy_records_to_admin(
    source_student_pk: int,
    admin: Student = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Copy all learning records from source student to the logged-in admin."""
    source = (
        await db.execute(select(Student).where(Student.id == source_student_pk))
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Student not found")

    if source.id == admin.id:
        return {
            "status": "skipped",
            "copied": 0,
            "source_name": source.name,
            "message": "來源與目標相同，已略過",
        }

    source_records = (
        await db.execute(
            select(LearningRecord).where(LearningRecord.student_id == source.id)
        )
    ).scalars().all()

    await db.execute(
        delete(LearningRecord).where(LearningRecord.student_id == admin.id)
    )

    now = datetime.now(timezone.utc)
    for r in source_records:
        db.add(LearningRecord(
            student_id=admin.id,
            unit_id=r.unit_id,
            preview_score=r.preview_score,
            pretest_score=r.pretest_score,
            completion_rate=r.completion_rate,
            quiz_score=r.quiz_score,
            imported_at=now,
            updated_at=now,
        ))

    await db.commit()
    return {
        "status": "ok",
        "copied": len(source_records),
        "source_name": source.name,
        "message": f"已複製 {source.name} 的 {len(source_records)} 筆學習記錄",
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


# ─── Excel Import Endpoints ───────────────────────────────────────────

_MAX_EXCEL_SIZE = 5 * 1024 * 1024  # 5 MB


def _validate_excel_upload(file: UploadFile) -> None:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="請上傳 .xlsx 格式的 Excel 檔案")


def _build_preview_html(
    parse_result: ExcelParseResult,
    student_map: dict[str, int],
    import_type: str,
    award_preview: dict | None = None,
) -> str:
    """Return an HTMX HTML fragment summarising the parse result.

    The actual "確認匯入" button is rendered by the page-level JavaScript
    (`commit-area`), so this fragment only contains the parse summary.

    `award_preview` is an optional dict with shape::

        {"achievements": int, "tokens": int, "breakdown": {key: count}}

    used by the score-list import to estimate the auto-awards that will be
    granted on commit.
    """
    matched_ids: set[str] = set()
    not_found: list[str] = []

    for rec in parse_result.records:
        if rec.student_id in student_map:
            matched_ids.add(rec.student_id)
        elif rec.student_id not in not_found:
            not_found.append(rec.student_id)

    will_update = len(matched_ids)
    not_found_html = ""
    if not_found:
        items = "".join(f'<li class="font-mono">{sid}</li>' for sid in not_found[:20])
        extra = f'<li>…共 {len(not_found)} 筆</li>' if len(not_found) > 20 else ""
        not_found_html = f"""
        <div class="mt-2">
          <p class="text-[var(--rpg-danger)] font-bold mb-1">找不到以下學號：</p>
          <ul class="text-[var(--rpg-danger)] text-[10px] list-disc list-inside">{items}{extra}</ul>
        </div>"""

    errors_html = ""
    if parse_result.parse_errors:
        items = "".join(f'<li>{e}</li>' for e in parse_result.parse_errors[:10])
        errors_html = f'<div class="mt-2 text-[var(--rpg-danger)] text-[10px]"><ul class="list-disc list-inside">{items}</ul></div>'

    unrecognized_html = ""
    if parse_result.unrecognized_headers:
        cols = ", ".join(parse_result.unrecognized_headers[:10])
        unrecognized_html = f'<p class="mt-1 text-[10px] text-[var(--rpg-text-secondary)]">略過未識別欄位：{cols}</p>'

    award_html = ""
    if award_preview is not None:
        ach_count = award_preview.get("achievements", 0)
        tokens_total = award_preview.get("tokens", 0)
        breakdown = award_preview.get("breakdown", {}) or {}
        if ach_count > 0:
            items = "".join(
                f'<li>{ACHIEVEMENT_TYPES[k]["label"]} × <span class="text-[var(--rpg-gold-bright)] font-bold">{n}</span></li>'
                for k, n in breakdown.items()
                if k in ACHIEVEMENT_TYPES
            )
            award_html = f"""
        <div class="mt-3 rounded border border-[var(--rpg-gold-dark)] bg-[var(--rpg-bg-panel)]/40 p-3">
          <p class="font-tc text-xs font-bold text-[var(--rpg-gold)] mb-1">🎁 將自動發放</p>
          <ul class="font-tc text-xs text-[var(--rpg-text-primary)] space-y-1">
            <li>成就：<span class="text-[var(--rpg-gold-bright)] font-bold">{ach_count}</span> 個</li>
            <li>點數：<span class="text-[var(--rpg-gold-bright)] font-bold">{tokens_total}</span> 點</li>
          </ul>
          <ul class="mt-1 font-tc text-[10px] text-[var(--rpg-text-secondary)] list-disc list-inside">{items}</ul>
        </div>"""
        else:
            award_html = """
        <p class="mt-3 font-tc text-[11px] text-[var(--rpg-text-secondary)]">
          🎁 自動發放：本次無新增成就（皆已發放或無對應條件）
        </p>"""

    return f"""
    <div class="rounded bg-[var(--rpg-bg-card)] border border-[var(--rpg-gold-dark)] p-4 mt-3">
      <p class="font-tc text-xs font-bold text-[var(--rpg-gold)] mb-2">預覽摘要</p>
      <ul class="font-tc text-xs text-[var(--rpg-text-primary)] space-y-1">
        <li>比對到：<span class="text-[var(--rpg-gold-bright)] font-bold">{will_update}</span> 位學生</li>
        <li>將更新記錄：<span class="text-[var(--rpg-gold-bright)] font-bold">{len(parse_result.records)}</span> 筆</li>
        <li>找不到：<span class="text-[var(--rpg-danger)] font-bold">{len(not_found)}</span> 位學生</li>
      </ul>
      {not_found_html}
      {unrecognized_html}
      {errors_html}
      {award_html}
    </div>"""


async def _upsert_records(
    db: AsyncSession,
    records: list[StudentRecord],
    student_map: dict[str, int],
    unit_map: dict[str, int],
    update_fields: tuple[str, ...],
) -> tuple[int, int, list[str]]:
    """Upsert learning records, updating only the specified fields.

    Returns (created, updated, warnings).
    """
    created = 0
    updated = 0
    warnings: list[str] = []
    now = datetime.now(timezone.utc)

    for rec in records:
        student_pk = student_map.get(rec.student_id)
        if student_pk is None:
            warnings.append(f"找不到學號 {rec.student_id}，已略過")
            continue

        unit_pk = unit_map.get(rec.unit_code)
        if unit_pk is None:
            warnings.append(f"找不到單元 {rec.unit_code}，已略過")
            continue

        existing = (
            await db.execute(
                select(LearningRecord).where(
                    LearningRecord.student_id == student_pk,
                    LearningRecord.unit_id == unit_pk,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            kwargs: dict = {"student_id": student_pk, "unit_id": unit_pk}
            for f in update_fields:
                v = getattr(rec, f)
                if v is not None:
                    kwargs[f] = v
            lr = LearningRecord(**kwargs)
            db.add(lr)
            created += 1
        else:
            changed = False
            for f in update_fields:
                v = getattr(rec, f)
                if v is not None:
                    setattr(existing, f, v)
                    changed = True
            if changed:
                existing.updated_at = now
            updated += 1

    return created, updated, warnings


@router.post("/api/admin/import-excel/completion/preview", response_class=HTMLResponse)
async def api_excel_completion_preview(
    request: Request,
    file: UploadFile = File(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Parse completion Excel and return an HTMX preview fragment."""
    _validate_excel_upload(file)
    content = await file.read()
    if len(content) > _MAX_EXCEL_SIZE:
        raise HTTPException(status_code=400, detail="檔案超過 5MB 限制")

    parse_result = parse_completion_excel(content)

    students = (await db.execute(select(Student))).scalars().all()
    student_map = {s.student_id: s.id for s in students}

    return _build_preview_html(parse_result, student_map, "completion")


@router.post("/api/admin/import-excel/completion/commit", response_class=HTMLResponse)
async def api_excel_completion_commit(
    request: Request,
    file: UploadFile = File(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Re-parse and commit completion Excel data to the database."""
    _validate_excel_upload(file)
    content = await file.read()
    if len(content) > _MAX_EXCEL_SIZE:
        raise HTTPException(status_code=400, detail="檔案超過 5MB 限制")

    parse_result = parse_completion_excel(content)

    students = (await db.execute(select(Student))).scalars().all()
    student_map = {s.student_id: s.id for s in students}

    units = (await db.execute(select(Unit))).scalars().all()
    unit_map = {u.code: u.id for u in units}

    try:
        created, updated, warnings = await _upsert_records(
            db, parse_result.records, student_map, unit_map,
            update_fields=("completion_rate",),
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Excel completion commit failed")
        raise HTTPException(status_code=500, detail=f"寫入資料庫失敗：{exc}") from exc

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings[:20])
        warn_html = f'<ul class="mt-2 text-[var(--rpg-danger)] text-[10px] list-disc list-inside">{items}</ul>'

    return f"""
    <div class="rounded bg-[var(--rpg-bg-card)] border border-[var(--rpg-gold)] p-4 mt-3">
      <p class="font-tc text-xs font-bold text-[var(--rpg-gold)] mb-2">✓ 匯入完成</p>
      <ul class="font-tc text-xs text-[var(--rpg-text-primary)] space-y-1">
        <li>新增：<span class="text-[var(--rpg-gold-bright)] font-bold">{created}</span> 筆</li>
        <li>更新：<span class="text-[var(--rpg-gold-bright)] font-bold">{updated}</span> 筆</li>
      </ul>
      {warn_html}
    </div>"""


# ─── Score-list auto-award helpers ────────────────────────────────────

# Map LearningRecord field → achievement-key suffix.
_SCORE_FIELD_TO_KEY_SUFFIX: dict[str, str] = {
    "pretest_score": "pretest",
    "quiz_score": "complete",
}

# Chapters eligible for auto-award (unit_6 自主學習 has no pretest/post-test).
_AUTO_AWARD_UNIT_CODES: tuple[str, ...] = (
    "unit_1", "unit_2", "unit_3", "unit_4", "unit_5",
)


def _achievement_key_for(unit_code: str, field: str) -> str | None:
    """Return achievement_key for (unit_code, field) or None if not eligible."""
    if unit_code not in _AUTO_AWARD_UNIT_CODES:
        return None
    suffix = _SCORE_FIELD_TO_KEY_SUFFIX.get(field)
    if suffix is None:
        return None
    chapter_num = unit_code.removeprefix("unit_")
    return f"chapter_{chapter_num}_{suffix}"


async def _compute_score_achievement_grants(
    db: AsyncSession,
    affected_student_pks: set[int],
    overlay_records: list[StudentRecord] | None = None,
    student_id_to_pk: dict[str, int] | None = None,
) -> list[tuple[int, str]]:
    """Return list of (student_pk, achievement_key) to grant.

    Reads current LearningRecord state from DB for `affected_student_pks`,
    then applies optional `overlay_records` (used by preview to simulate
    the post-commit state without writing). Already-earned achievements
    are filtered out via the StudentAchievement table.
    """
    if not affected_student_pks:
        return []

    # state[(student_pk, unit_code)] = (pretest_score, quiz_score)
    state: dict[tuple[int, str], tuple[float | None, float | None]] = {}

    rows = (await db.execute(
        select(
            LearningRecord.student_id,
            Unit.code,
            LearningRecord.pretest_score,
            LearningRecord.quiz_score,
        )
        .join(Unit, LearningRecord.unit_id == Unit.id)
        .where(
            LearningRecord.student_id.in_(affected_student_pks),
            Unit.code.in_(_AUTO_AWARD_UNIT_CODES),
        )
    )).all()
    for sid, code, pretest, quiz in rows:
        state[(sid, code)] = (pretest, quiz)

    if overlay_records:
        sid_to_pk = student_id_to_pk or {}
        for rec in overlay_records:
            if rec.unit_code not in _AUTO_AWARD_UNIT_CODES:
                continue
            sid = sid_to_pk.get(rec.student_id)
            if sid is None:
                continue
            existing_pretest, existing_quiz = state.get((sid, rec.unit_code), (None, None))
            new_pretest = rec.pretest_score if rec.pretest_score is not None else existing_pretest
            new_quiz = rec.quiz_score if rec.quiz_score is not None else existing_quiz
            state[(sid, rec.unit_code)] = (new_pretest, new_quiz)

    candidates: list[tuple[int, str]] = []
    for (sid, code), (pretest, quiz) in state.items():
        pretest_key = _achievement_key_for(code, "pretest_score")
        complete_key = _achievement_key_for(code, "quiz_score")
        if pretest is not None and pretest_key:
            candidates.append((sid, pretest_key))
        if quiz is not None and complete_key:
            candidates.append((sid, complete_key))

    if not candidates:
        return []

    student_ids = {sid for sid, _ in candidates}
    keys = {k for _, k in candidates}
    already_rows = (await db.execute(
        select(StudentAchievement.student_id, StudentAchievement.achievement_key)
        .where(
            StudentAchievement.student_id.in_(student_ids),
            StudentAchievement.achievement_key.in_(keys),
        )
    )).all()
    already = {(sid, key) for sid, key in already_rows}

    return [(sid, key) for sid, key in candidates if (sid, key) not in already]


def _summarize_grants(grants: list[tuple[int, str]]) -> dict:
    """Aggregate grants → {achievements, tokens, breakdown}."""
    breakdown: dict[str, int] = {}
    tokens_total = 0
    for _, key in grants:
        spec = ACHIEVEMENT_TYPES.get(key)
        if spec is None:
            continue
        breakdown[key] = breakdown.get(key, 0) + 1
        tokens_total += int(spec.get("tokens", 0))
    return {
        "achievements": len(grants),
        "tokens": tokens_total,
        "breakdown": breakdown,
    }


async def _apply_score_achievement_grants(
    db: AsyncSession,
    grants: list[tuple[int, str]],
) -> dict:
    """Persist grants: bump student.tokens, write TokenTransaction +
    StudentAchievement rows. Returns the same shape as `_summarize_grants`.
    """
    if not grants:
        return _summarize_grants(grants)

    now = datetime.now(timezone.utc)
    student_ids = {sid for sid, _ in grants}
    students = (await db.execute(
        select(Student).where(Student.id.in_(student_ids))
    )).scalars().all()
    student_by_id = {s.id: s for s in students}

    for sid, key in grants:
        spec = ACHIEVEMENT_TYPES.get(key)
        student = student_by_id.get(sid)
        if spec is None or student is None:
            continue
        amount = int(spec.get("tokens", 0))
        student.tokens = (student.tokens or 0) + amount
        student.updated_at = now

        tx = TokenTransaction(
            student_id=sid,
            amount=amount,
            reason=spec.get("label"),
            created_at=now,
        )
        db.add(tx)
        await db.flush()  # populate tx.id

        db.add(StudentAchievement(
            student_id=sid,
            achievement_key=key,
            token_transaction_id=tx.id,
            awarded_at=now,
        ))

    return _summarize_grants(grants)


@router.post("/api/admin/import-excel/scores/preview", response_class=HTMLResponse)
async def api_excel_scores_preview(
    request: Request,
    file: UploadFile = File(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Parse score-list Excel and return an HTMX preview fragment.

    Also estimates the auto-award (achievements + tokens) that will be
    granted if commit runs, so the admin sees the impact upfront.
    """
    _validate_excel_upload(file)
    content = await file.read()
    if len(content) > _MAX_EXCEL_SIZE:
        raise HTTPException(status_code=400, detail="檔案超過 5MB 限制")

    parse_result = parse_score_excel(content)

    students = (await db.execute(select(Student))).scalars().all()
    student_map = {s.student_id: s.id for s in students}

    affected_pks = {
        student_map[r.student_id]
        for r in parse_result.records
        if r.student_id in student_map
    }
    grants = await _compute_score_achievement_grants(
        db,
        affected_student_pks=affected_pks,
        overlay_records=parse_result.records,
        student_id_to_pk=student_map,
    )
    award_preview = _summarize_grants(grants)

    return _build_preview_html(parse_result, student_map, "scores", award_preview=award_preview)


@router.post("/api/admin/import-excel/scores/commit", response_class=HTMLResponse)
async def api_excel_scores_commit(
    request: Request,
    file: UploadFile = File(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Re-parse and commit score-list Excel data to the database, then
    auto-award chapter pretest / completion achievements (idempotent)."""
    _validate_excel_upload(file)
    content = await file.read()
    if len(content) > _MAX_EXCEL_SIZE:
        raise HTTPException(status_code=400, detail="檔案超過 5MB 限制")

    parse_result = parse_score_excel(content)

    students = (await db.execute(select(Student))).scalars().all()
    student_map = {s.student_id: s.id for s in students}

    units = (await db.execute(select(Unit))).scalars().all()
    unit_map = {u.code: u.id for u in units}

    try:
        created, updated, warnings = await _upsert_records(
            db, parse_result.records, student_map, unit_map,
            update_fields=("pretest_score", "quiz_score"),
        )
        await db.flush()  # make upserts visible to the auto-award query

        affected_pks = {
            student_map[r.student_id]
            for r in parse_result.records
            if r.student_id in student_map
        }
        grants = await _compute_score_achievement_grants(
            db, affected_student_pks=affected_pks
        )
        award_summary = await _apply_score_achievement_grants(db, grants)

        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Excel scores commit failed")
        raise HTTPException(status_code=500, detail=f"寫入資料庫失敗：{exc}") from exc

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings[:20])
        warn_html = f'<ul class="mt-2 text-[var(--rpg-danger)] text-[10px] list-disc list-inside">{items}</ul>'

    ach_count = award_summary["achievements"]
    tokens_total = award_summary["tokens"]
    breakdown = award_summary["breakdown"]
    if ach_count > 0:
        bd_items = "".join(
            f'<li>{ACHIEVEMENT_TYPES[k]["label"]} × <span class="text-[var(--rpg-gold-bright)] font-bold">{n}</span></li>'
            for k, n in breakdown.items()
            if k in ACHIEVEMENT_TYPES
        )
        award_html = f"""
      <div class="mt-3 rounded border border-[var(--rpg-gold-dark)] bg-[var(--rpg-bg-panel)]/40 p-3">
        <p class="font-tc text-xs font-bold text-[var(--rpg-gold)] mb-1">🎁 自動發放完成</p>
        <ul class="font-tc text-xs text-[var(--rpg-text-primary)] space-y-1">
          <li>成就：<span class="text-[var(--rpg-gold-bright)] font-bold">{ach_count}</span> 個</li>
          <li>點數：<span class="text-[var(--rpg-gold-bright)] font-bold">{tokens_total}</span> 點</li>
        </ul>
        <ul class="mt-1 font-tc text-[10px] text-[var(--rpg-text-secondary)] list-disc list-inside">{bd_items}</ul>
      </div>"""
    else:
        award_html = """
      <p class="mt-3 font-tc text-[11px] text-[var(--rpg-text-secondary)]">
        🎁 自動發放：本次無新增成就（皆已發放或無對應條件）
      </p>"""

    return f"""
    <div class="rounded bg-[var(--rpg-bg-card)] border border-[var(--rpg-gold)] p-4 mt-3">
      <p class="font-tc text-xs font-bold text-[var(--rpg-gold)] mb-2">✓ 匯入完成</p>
      <ul class="font-tc text-xs text-[var(--rpg-text-primary)] space-y-1">
        <li>新增：<span class="text-[var(--rpg-gold-bright)] font-bold">{created}</span> 筆</li>
        <li>更新：<span class="text-[var(--rpg-gold-bright)] font-bold">{updated}</span> 筆</li>
      </ul>
      {warn_html}
      {award_html}
    </div>"""


@router.post("/api/admin/import-preview-rates/preview", response_class=HTMLResponse)
async def api_preview_rates_preview(
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Preview import results from data/preview_rates.csv."""

    records, parse_errors = _load_preview_rate_records_from_csv()
    students = (await db.execute(select(Student))).scalars().all()
    student_map, ambiguous_suffixes = _build_student_lookup(students)
    return _build_preview_rates_summary_html(
        records, parse_errors, student_map, ambiguous_suffixes
    )


@router.post("/api/admin/import-preview-rates/commit", response_class=HTMLResponse)
async def api_preview_rates_commit(
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Commit preview_score updates from data/preview_rates.csv."""

    records, parse_errors = _load_preview_rate_records_from_csv()
    if parse_errors:
        items = "".join(f"<li>{err}</li>" for err in parse_errors[:20])
        return f"""
        <div class="rounded bg-[var(--rpg-bg-card)] border border-[var(--rpg-danger)] p-4 mt-3">
          <p class="font-tc text-xs font-bold text-[var(--rpg-danger)] mb-2">無法匯入</p>
          <ul class="text-[var(--rpg-danger)] text-[10px] list-disc list-inside">{items}</ul>
        </div>"""

    students = (await db.execute(select(Student))).scalars().all()
    student_map, ambiguous_suffixes = _build_student_lookup(students)
    if ambiguous_suffixes:
        blocking_ids = sorted({rec.student_id for rec in records if rec.student_id in ambiguous_suffixes})
        if blocking_ids:
            items = "".join(
                f"<li>{suffix}（{', '.join(ambiguous_suffixes[suffix][:3])}）</li>"
                for suffix in blocking_ids[:20]
            )
            return f"""
            <div class="rounded bg-[var(--rpg-bg-card)] border border-[var(--rpg-danger)] p-4 mt-3">
              <p class="font-tc text-xs font-bold text-[var(--rpg-danger)] mb-2">無法匯入</p>
              <p class="font-tc text-xs text-[var(--rpg-text-primary)]">preview_rates.csv 中有 student_id 對應到多位學生，請先修正 CSV。</p>
              <ul class="mt-2 text-[var(--rpg-danger)] text-[10px] list-disc list-inside">{items}</ul>
            </div>"""

    units = (await db.execute(select(Unit))).scalars().all()
    unit_map = {u.code: u.id for u in units}

    try:
        created, updated, warnings = await _upsert_records(
            db,
            records,
            student_map,
            unit_map,
            update_fields=("preview_score",),
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Preview-rate commit failed")
        raise HTTPException(status_code=500, detail=f"寫入資料庫失敗：{exc}") from exc

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{warning}</li>" for warning in warnings[:20])
        warn_html = (
            f'<ul class="mt-2 text-[var(--rpg-danger)] text-[10px] list-disc list-inside">'
            f"{items}</ul>"
        )

    return f"""
    <div class="rounded bg-[var(--rpg-bg-card)] border border-[var(--rpg-gold)] p-4 mt-3">
      <p class="font-tc text-xs font-bold text-[var(--rpg-gold)] mb-2">✓ 影片預習分數更新完成</p>
      <ul class="font-tc text-xs text-[var(--rpg-text-primary)] space-y-1">
        <li>來源檔案：<span class="font-mono text-[var(--rpg-gold-bright)]">{_preview_rates_path()}</span></li>
        <li>新增：<span class="text-[var(--rpg-gold-bright)] font-bold">{created}</span> 筆</li>
        <li>更新：<span class="text-[var(--rpg-gold-bright)] font-bold">{updated}</span> 筆</li>
      </ul>
      {warn_html}
    </div>"""


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


def _preview_rates_path() -> Path:
    """Return the fixed preview-rate CSV path."""

    return settings.DATA_DIR / _PREVIEW_RATES_FILENAME


def _load_preview_rate_records_from_csv() -> tuple[list[StudentRecord], list[str]]:
    """Load preview-score records from data/preview_rates.csv."""

    csv_path = _preview_rates_path()
    if not csv_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"找不到 {csv_path}，請先執行影片預習率匯出腳本。",
        )

    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"讀取 {csv_path.name} 失敗：{exc}") from exc

    reader = csv.DictReader(io.StringIO(text))
    required_columns = {"student_id", "unit_code", "preview_score"}
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail=f"{csv_path.name} 缺少表頭。")

    missing_columns = required_columns - set(reader.fieldnames)
    if missing_columns:
        cols = ", ".join(sorted(missing_columns))
        raise HTTPException(status_code=400, detail=f"{csv_path.name} 缺少必要欄位：{cols}")

    records: list[StudentRecord] = []
    parse_errors: list[str] = []
    for row_num, row in enumerate(reader, start=2):
        student_id = (row.get("student_id") or "").strip()
        unit_code = (row.get("unit_code") or "").strip()
        preview_score = _parse_float(row.get("preview_score"))

        if not student_id or not unit_code:
            parse_errors.append(f"第 {row_num} 列缺少 student_id 或 unit_code")
            continue
        if preview_score is None:
            parse_errors.append(f"第 {row_num} 列的 preview_score 無法解析")
            continue

        records.append(
            StudentRecord(
                student_id=student_id,
                unit_code=unit_code,
                preview_score=preview_score,
            )
        )

    return records, parse_errors


def _build_student_lookup(
    students: list[Student],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Build exact and suffix-based student lookup maps."""

    exact_map: dict[str, int] = {}
    suffix_candidates: dict[str, list[tuple[str, int]]] = {}

    for student in students:
        if not student.student_id:
            continue
        exact_map[student.student_id] = student.id
        suffix = student.student_id[-4:]
        suffix_candidates.setdefault(suffix, []).append((student.student_id, student.id))

    suffix_map: dict[str, list[str]] = {}
    for suffix, matches in suffix_candidates.items():
        if len(matches) == 1:
            exact_map[suffix] = matches[0][1]
        else:
            suffix_map[suffix] = [match[0] for match in matches]

    return exact_map, suffix_map


def _build_preview_rates_summary_html(
    records: list[StudentRecord],
    parse_errors: list[str],
    student_map: dict[str, int],
    ambiguous_suffixes: dict[str, list[str]],
) -> str:
    """Render the preview/confirm fragment for preview_rates.csv import."""

    csv_path = _preview_rates_path()
    matched_students: set[str] = set()
    matched_records = 0
    not_found: list[str] = []
    ambiguous: list[str] = []
    unit_codes: set[str] = set()

    for rec in records:
        unit_codes.add(rec.unit_code)
        if rec.student_id in student_map:
            matched_students.add(rec.student_id)
            matched_records += 1
            continue
        if rec.student_id in ambiguous_suffixes:
            if rec.student_id not in ambiguous:
                ambiguous.append(rec.student_id)
            continue
        if rec.student_id not in not_found:
            not_found.append(rec.student_id)

    stat = csv_path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")

    def _list_html(items: list[str], color_class: str, title: str) -> str:
        if not items:
            return ""
        body = "".join(f'<li class="font-mono">{item}</li>' for item in items[:20])
        extra = f"<li>…共 {len(items)} 筆</li>" if len(items) > 20 else ""
        return f"""
        <div class="mt-2">
          <p class="{color_class} font-bold mb-1">{title}</p>
          <ul class="{color_class} text-[10px] list-disc list-inside">{body}{extra}</ul>
        </div>"""

    preview_html = _list_html(
        not_found,
        "text-[var(--rpg-danger)]",
        "以下 student_id 未註冊於本系統（例如未參與計畫），已略過：",
    )
    ambiguous_labels = [
        f"{suffix}（{', '.join(ambiguous_suffixes[suffix][:3])}）" for suffix in ambiguous
    ]
    ambiguous_html = _list_html(
        ambiguous_labels,
        "text-[var(--rpg-danger)]",
        "以下 student_id 對到多位學生，請先修正 CSV：",
    )
    parse_errors_html = _list_html(parse_errors, "text-[var(--rpg-danger)]", "CSV 解析錯誤：")

    commit_html = ""
    if matched_records > 0 and not ambiguous and not parse_errors:
        commit_html = """
        <form class="mt-3" id="preview-rates-commit-form">
          <button type="submit"
                  class="flex items-center gap-2 rounded px-4 py-2
                         bg-[var(--rpg-gold-dark)] border-2 border-[var(--rpg-gold)]
                         font-tc text-xs font-bold text-[var(--rpg-gold-bright)]
                         transition-opacity hover:opacity-90">
            確認更新影片預習分數
          </button>
        </form>"""

    return f"""
    <div class="rounded bg-[var(--rpg-bg-card)] border border-[var(--rpg-gold-dark)] p-4 mt-3">
      <p class="font-tc text-xs font-bold text-[var(--rpg-gold)] mb-2">preview_rates.csv 預覽摘要</p>
      <ul class="font-tc text-xs text-[var(--rpg-text-primary)] space-y-1">
        <li>來源檔案：<span class="font-mono text-[var(--rpg-gold-bright)]">{csv_path}</span></li>
        <li>最後更新：<span class="text-[var(--rpg-gold-bright)] font-bold">{modified_at}</span></li>
        <li>CSV 資料列：<span class="text-[var(--rpg-gold-bright)] font-bold">{len(records)}</span> 筆</li>
        <li>可更新記錄：<span class="text-[var(--rpg-gold-bright)] font-bold">{matched_records}</span> 筆</li>
        <li>涉及單元：<span class="text-[var(--rpg-gold-bright)] font-bold">{", ".join(sorted(unit_codes)) or "無"}</span></li>
        <li>比對到學生：<span class="text-[var(--rpg-gold-bright)] font-bold">{len(matched_students)}</span> 位</li>
      </ul>
      {preview_html}
      {ambiguous_html}
      {parse_errors_html}
      {commit_html}
    </div>"""


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
    """System settings page: global parameters + attribute rules."""
    result = await db.execute(
        select(AttributeRule).order_by(
            AttributeRule.unit_code, AttributeRule.sort_order, AttributeRule.tier
        )
    )
    rules = result.scalars().all()

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

    for attr_types in grouped.values():
        for attr_type, rule_rows in attr_types.items():
            rule_rows.sort(key=lambda r: _TIER_ORDER.index(r["tier"]) if r["tier"] in _TIER_ORDER else len(_TIER_ORDER))
            by_tier = {row["tier"]: row for row in rule_rows}
            for row in rule_rows:
                effective_rows = [
                    by_tier[tier]
                    for tier in _inclusive_tiers_for_admin(row["tier"])
                    if tier in by_tier
                ]
                effective_options, effective_labels = _merge_rule_dicts(effective_rows)
                row["effective_options"] = effective_options
                row["effective_labels"] = effective_labels

    system_settings = await get_system_settings_map(db)

    return templates.TemplateResponse(
        request,
        "admin/rules.html",
        {
            "user": user,
            "grouped": grouped,
            "unit_names": UNIT_NAMES,
            "system_settings": system_settings,
            "system_setting_labels": SYSTEM_SETTING_LABELS,
            "ollama_model_suggestions": OLLAMA_MODEL_SUGGESTIONS,
        },
    )


@router.put("/api/admin/system-settings/{setting_key}")
async def api_admin_update_system_setting(
    setting_key: str,
    payload: dict = Body(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Update one persisted global system setting."""
    value = str(payload.get("value", "") or "").strip()
    try:
        row = await set_system_setting(db, setting_key, value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown system setting key")

    return {
        "key": row.key,
        "value": row.value,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


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


# ─── Queue & Generation History ──────────────────────────────────────


@router.get("/api/admin/queue")
async def api_admin_queue(
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """代理查詢 ai-worker 佇列，補上學生姓名後回傳。"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.AI_WORKER_BASE_URL}/api/queue")
            resp.raise_for_status()
            queue_data = resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch ai-worker queue: %s", exc)
        return {"current_job": None, "queued_jobs": [], "queue_size": 0, "error": str(exc)}

    # Collect all student_ids in queue
    student_ids: list[str] = []
    if queue_data.get("current_job"):
        student_ids.append(queue_data["current_job"]["student_id"])
    for item in queue_data.get("queued_jobs", []):
        student_ids.append(item["student_id"])

    # Lookup names in bulk
    name_map: dict[str, str] = {}
    if student_ids:
        result = await db.execute(
            select(Student.student_id, Student.name).where(
                Student.student_id.in_(student_ids)
            )
        )
        name_map = {row[0]: row[1] for row in result.all()}

    def enrich(item: dict) -> dict:
        sid = item.get("student_id", "")
        return {**item, "student_name": name_map.get(sid, "")}

    enriched_current = enrich(queue_data["current_job"]) if queue_data.get("current_job") else None
    enriched_queued = [enrich(j) for j in queue_data.get("queued_jobs", [])]

    return {
        "current_job": enriched_current,
        "queued_jobs": enriched_queued,
        "queue_size": queue_data.get("queue_size", 0),
    }


def _apply_generation_history_filters(query, status_filter: str):
    query = query.where(
        Card.job_id.isnot(None),
        Card.history_visible.is_(True),
    )
    if status_filter in ("generating", "completed", "failed"):
        query = query.where(Card.status == status_filter)
    return query


async def _get_generation_history_card(db: AsyncSession, card_id: int) -> Card:
    card = (
        await db.execute(
            select(Card).where(
                Card.id == card_id,
                Card.job_id.isnot(None),
            )
        )
    ).scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="找不到此生圖記錄。")
    return card


@router.get("/admin/generation-history")
async def admin_generation_history(
    request: Request,
    status_filter: str = "all",
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """生圖歷史紀錄頁面。"""
    query = _apply_generation_history_filters(
        select(Card, Student.student_id, Student.name)
        .join(Student, Card.student_id == Student.id)
        .order_by(Card.created_at.desc()),
        status_filter,
    )

    rows = (await db.execute(query)).all()

    _TZ_TAIPEI = timezone(timedelta(hours=8))

    def _to_local(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_TZ_TAIPEI)

    def _fmt(dt):
        return dt.strftime('%m-%d %H:%M:%S') if dt else None

    def _duration(start, end):
        if start is None or end is None:
            return None
        delta = end - start
        secs = int(delta.total_seconds())
        if secs < 0:
            return None
        m, s = divmod(secs, 60)
        return f"{m}m {s:02d}s" if m else f"{s}s"

    records = []
    for card, sid, name in rows:
        start_local = _to_local(card.created_at)
        end_local   = _to_local(card.generated_at)
        records.append({
            "card_id": card.id,
            "job_id": card.job_id,
            "student_id": sid,
            "student_name": name,
            "status": card.status,
            "thumbnail_url": card.thumbnail_url,
            "image_url": card.image_url,
            "detail_url": f"/admin/cards/{card.id}",
            "created_at_fmt": _fmt(start_local),
            "generated_at_fmt": _fmt(end_local),
            "duration": _duration(start_local, end_local),
            # Cloud generation (Phase 1a)
            "backend_used": card.backend_used or "local",
            "cloud_model": card.cloud_model,
            "fallback_from_cloud": bool(card.fallback_from_cloud),
            "cloud_error": card.cloud_error,
        })

    return templates.TemplateResponse(
        request,
        "admin/generation_history.html",
        {
            "user": user,
            "records": records,
            "status_filter": status_filter,
        },
    )


@router.get("/admin/generation-history/export")
async def admin_generation_history_export(
    status_filter: str = "all",
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Download generation history as CSV."""
    query = _apply_generation_history_filters(
        select(Card, Student.student_id, Student.name)
        .join(Student, Card.student_id == Student.id)
        .order_by(Card.created_at.desc()),
        status_filter,
    )

    rows = (await db.execute(query)).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "card_id",
        "job_id",
        "student_id",
        "student_name",
        "status",
        "created_at_utc",
        "generated_at_utc",
        "thumbnail_url",
        "image_url",
    ])
    for card, sid, name in rows:
        writer.writerow([
            card.id,
            card.job_id or "",
            sid or "",
            name or "",
            card.status or "",
            card.created_at.isoformat() if card.created_at else "",
            card.generated_at.isoformat() if card.generated_at else "",
            card.thumbnail_url or "",
            card.image_url or "",
        ])

    filename = f"generation-history-{status_filter}.csv"
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/api/admin/generation-history")
async def api_admin_generation_history_clear(
    status_filter: str = "all",
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Hide generation-history records from the history page."""
    stmt = update(Card).where(
        Card.job_id.isnot(None),
        Card.history_visible.is_(True),
    )
    if status_filter in ("generating", "completed", "failed"):
        stmt = stmt.where(Card.status == status_filter)

    result = await db.execute(
        stmt.values(history_visible=False)
    )
    await db.commit()
    return {"hidden": result.rowcount or 0}


@router.delete("/api/admin/generation-history/{card_id}/hide")
async def api_admin_generation_history_hide_one(
    card_id: int,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Hide one card from the generation-history page only."""
    card = await _get_generation_history_card(db, card_id)
    if not card.history_visible:
        return {"hidden": False, "card_id": card.id}

    card.history_visible = False
    await db.commit()
    return {"hidden": True, "card_id": card.id}


@router.delete("/api/admin/generation-history/{card_id}/record")
async def api_admin_generation_history_delete_record(
    card_id: int,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Delete one card row from the web database only."""
    card = await _get_generation_history_card(db, card_id)
    await db.delete(card)
    await db.commit()
    return {"deleted": True, "card_id": card_id}


@router.delete("/api/admin/generation-history/{card_id}/full")
async def api_admin_generation_history_delete_full(
    card_id: int,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Delete storage assets for one card, then delete its web DB row."""
    card = await _get_generation_history_card(db, card_id)
    storage = get_storage_service()

    try:
        storage_result = await storage.delete_card_assets(card_id)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"儲存端刪除失敗：{exc}",
        ) from exc

    await db.delete(card)
    await db.commit()

    return {
        "deleted": True,
        "card_id": card_id,
        "storage": storage_result,
    }


# ─── Simulation Generation ────────────────────────────────────────────

_SIMULATION_STUDENT_ID = "SIMULATION"

_UNIT_DISPLAY = {
    "unit_1": ("先備知識", "種族、性別"),
    "unit_2": ("MLP", "職業、體型"),
    "unit_3": ("CNN", "服飾裝備"),
    "unit_4": ("RNN", "武器"),
    "unit_5": ("進階技術", "背景場景"),
    "unit_6": ("自主學習", "表情、姿勢"),
}

_ATTR_DISPLAY = {
    "race": "種族", "gender": "性別", "class": "職業", "body": "體型",
    "equipment": "裝備", "weapon_quality": "武器品質", "weapon_type": "武器類型",
    "background": "背景", "expression": "表情", "pose": "姿勢",
}


def _resolve_anchor_image_url(stored_url: str) -> str:
    """Resolve a stored ``cards.image_url`` (relative proxy URL) to an absolute
    URL that the ai-worker can fetch directly from db-storage.

    ``cards.image_url`` is stored as ``/api/images/proxy/students/.../card_NNN.png?v=...``
    by ``_image_path_to_url``. The ai-worker (192.168.60.110) is whitelisted in
    db-storage's ALLOWED_READ_IPS, so we can convert this proxy path back to a
    direct db-storage URL: ``{DB_STORAGE_BASE_URL}/api/images/students/.../card_NNN.png``
    (cache-busting query string is dropped — db-storage doesn't need it).

    If the stored URL is already absolute, it is returned unchanged.
    """
    parsed = urlparse(stored_url)
    if parsed.scheme:
        # Already absolute (defensive — cards.image_url shouldn't be like this
        # under current logic but keep this branch for forward compatibility).
        return stored_url

    proxy_prefix = "/api/images/proxy/"
    if parsed.path.startswith(proxy_prefix):
        relative_image_path = parsed.path[len(proxy_prefix):]
        db_base = settings.DB_STORAGE_BASE_URL.rstrip("/")
        return f"{db_base}/api/images/{relative_image_path}"

    # Fallback for unexpected shapes (e.g. /static/...): prepend web-server
    # base so ai-worker still has *some* absolute URL to try.
    web_base = settings.WEB_SERVER_BASE_URL.rstrip("/")
    return f"{web_base}{parsed.path}"


async def _get_or_create_simulation_student(db: AsyncSession) -> Student:
    """Get or create the SIMULATION pseudo-student."""
    result = await db.execute(
        select(Student).where(Student.student_id == _SIMULATION_STUDENT_ID)
    )
    sim = result.scalar_one_or_none()
    if sim is None:
        sim = Student(
            student_id=_SIMULATION_STUDENT_ID,
            name="模擬生圖",
            email="simulation@system",
            role="admin",
            tokens=0,
        )
        db.add(sim)
        await db.commit()
        await db.refresh(sim)
    return sim


@router.get("/admin/simulation")
async def admin_simulation(
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """模擬生圖頁面。"""
    await _get_or_create_simulation_student(db)

    rules_result = await db.execute(
        select(AttributeRule).order_by(
            AttributeRule.unit_code, AttributeRule.attribute_type, AttributeRule.sort_order
        )
    )
    all_rules = rules_result.scalars().all()

    units_config: dict = {}
    for rule in all_rules:
        unit = rule.unit_code
        attr = rule.attribute_type
        try:
            opts = json.loads(rule.options)
            lbls = json.loads(rule.labels)
        except Exception:
            continue

        if unit not in units_config:
            units_config[unit] = {}
        if attr not in units_config[unit]:
            units_config[unit][attr] = {
                "label": _ATTR_DISPLAY.get(attr, attr),
                "options": [],
                "seen_keys": set(),
            }
        for key in opts:
            if key not in units_config[unit][attr]["seen_keys"]:
                units_config[unit][attr]["seen_keys"].add(key)
                units_config[unit][attr]["options"].append({
                    "key": key,
                    "label": lbls.get(key, key),
                })

    for unit_data in units_config.values():
        for attr_data in unit_data.values():
            attr_data.pop("seen_keys", None)

    units_ordered = []
    for code in ["unit_1", "unit_2", "unit_3", "unit_4", "unit_5", "unit_6"]:
        if code in units_config:
            unit_name, unit_subtitle = _UNIT_DISPLAY.get(code, (code, ""))
            units_ordered.append({
                "code": code,
                "name": unit_name,
                "subtitle": unit_subtitle,
                "attrs": units_config[code],
            })

    sim_student = await _get_or_create_simulation_student(db)
    cards_result = await db.execute(
        select(Card)
        .where(Card.student_id == sim_student.id)
        .order_by(Card.created_at.desc())
    )
    sim_cards = cards_result.scalars().all()

    return templates.TemplateResponse(
        request,
        "admin/simulation.html",
        {
            "user": user,
            "units": units_ordered,
            "sim_cards": sim_cards,
        },
    )


@router.post("/api/admin/simulation/generate")
async def api_admin_simulation_generate(
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """送出模擬生圖請求。"""
    from app.services.ai_worker import get_ai_worker_service
    from app.services.scoring import determine_border_style, roll_rarity

    body = await request.json()
    card_config: dict = body.get("card_config", {})
    level: int = max(1, min(100, int(body.get("level", 50))))
    rarity_input: str = body.get("rarity", "auto")
    nickname: str = body.get("nickname", "Admin Test") or "Admin Test"
    backend_input: str = (body.get("backend") or "local").strip().lower()
    if backend_input not in ("local", "cloud"):
        raise HTTPException(status_code=400, detail="backend 必須為 local 或 cloud")
    cloud_model_override: str | None = body.get("cloud_model") or None

    # Phase 1b: anchor card for image edit. None or 0 / empty string means "no anchor".
    anchor_raw = body.get("anchor_card_id")
    anchor_card_id: int | None = None
    if anchor_raw not in (None, "", 0, "0"):
        try:
            anchor_card_id = int(anchor_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="anchor_card_id 必須為整數")
        if anchor_card_id <= 0:
            anchor_card_id = None

    if anchor_card_id and backend_input != "cloud":
        raise HTTPException(
            status_code=400,
            detail="image edit 僅支援雲端 backend；本地 backend 請選「不使用錨點」",
        )

    seed_raw = body.get("seed")
    requested_seed: int | None = None
    if seed_raw not in (None, "", -1, "-1"):
        try:
            requested_seed = int(seed_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Seed ?????")
        if requested_seed < 0:
            raise HTTPException(status_code=400, detail="Seed ??????????? -1 / ??????")

    rarity = roll_rarity(level) if rarity_input == "auto" else rarity_input
    border = determine_border_style(rarity)
    card_config["level"] = level
    card_config["rarity"] = rarity
    card_config["border"] = border

    sim_student = await _get_or_create_simulation_student(db)

    # Resolve anchor card → reference_image_url. Must belong to the simulation
    # student and be a completed card with a stored image_url.
    reference_image_url: str | None = None
    if anchor_card_id:
        anchor_result = await db.execute(
            select(Card).where(
                Card.id == anchor_card_id,
                Card.student_id == sim_student.id,
            )
        )
        anchor_card = anchor_result.scalar_one_or_none()
        if anchor_card is None:
            raise HTTPException(
                status_code=404,
                detail=f"找不到模擬卡 #{anchor_card_id}（必須是同一個 SIMULATION 學生的卡）",
            )
        if anchor_card.status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"錨點卡 #{anchor_card_id} 狀態為 {anchor_card.status}，必須是 completed 才能當參考圖",
            )
        if not anchor_card.image_url:
            raise HTTPException(
                status_code=400,
                detail=f"錨點卡 #{anchor_card_id} 沒有 image_url（可能上傳失敗）",
            )
        reference_image_url = _resolve_anchor_image_url(anchor_card.image_url)
        logger.info(
            "Simulation: using anchor card #%d as reference (stored=%s, resolved=%s)",
            anchor_card_id, anchor_card.image_url, reference_image_url,
        )

        # image edit 會強制保留錨點的 face/race/body/gender/hair —— 若管理者
        # 故意選了不一致的 race/gender，前端已先 confirm，這裡把錨點的 race/
        # gender 寫回 card_config，確保 metadata 與最終圖一致（避免 UI 顯示
        # 「亞洲男」但圖實際是「歐洲女」）
        try:
            anchor_cfg = json.loads(anchor_card.config_snapshot or "{}")
        except (TypeError, ValueError):
            anchor_cfg = {}
        if isinstance(anchor_cfg, dict):
            for attr in ("race", "gender"):
                anchor_val = anchor_cfg.get(attr)
                chosen_val = card_config.get(attr)
                if anchor_val and chosen_val != anchor_val:
                    logger.info(
                        "Simulation anchor override: %s %r -> %r (from anchor #%d)",
                        attr, chosen_val, anchor_val, anchor_card_id,
                    )
                    card_config[attr] = anchor_val

    snapshot_for_storage = dict(card_config)
    snapshot_for_storage["__meta"] = {
        "nickname": nickname,
        "seed": requested_seed if requested_seed is not None else -1,
        "anchor_card_id": anchor_card_id,
    }

    new_card = Card(
        student_id=sim_student.id,
        config_snapshot=json.dumps(snapshot_for_storage),
        status="pending",
        border_style=border,
        level_number=level,
        rarity=rarity,
        is_latest=False,
        is_display=False,
        is_hidden=True,
        backend_used=backend_input,  # 預設為提交時選的 backend；若 fallback callback 會覆寫
        reference_card_id=anchor_card_id,
    )
    db.add(new_card)
    await db.commit()
    await db.refresh(new_card)

    learning_data = {
        "unit_scores": {},
        "overall_completion": float(level),
    }

    ai_worker = get_ai_worker_service()
    ollama_model = await get_system_setting(db, "ollama_model")
    job_id = None
    try:
        job_id = await ai_worker.submit_generation(
            card_id=new_card.id,
            student_id="SIMULATION",
            student_nickname=nickname,
            card_config=card_config,
            learning_data=learning_data,
            seed=requested_seed,
            ollama_model_override=ollama_model,
            backend=backend_input,
            cloud_model=cloud_model_override,
            reference_card_id=anchor_card_id,
            reference_image_url=reference_image_url,
        )
        new_card.status = "generating"
        new_card.job_id = job_id
        await db.commit()
    except Exception as e:
        logger.error(f"Simulation generation failed: {e}")
        new_card.status = "failed"
        await db.commit()
        raise HTTPException(status_code=502, detail="AI 生圖服務無法連線")

    return {"card_id": new_card.id, "job_id": job_id, "status": "generating"}


@router.get("/api/admin/simulation/cards")
async def api_admin_simulation_cards(
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """取得所有模擬卡牌列表（用於 JS polling）。"""
    sim_result = await db.execute(
        select(Student).where(Student.student_id == _SIMULATION_STUDENT_ID)
    )
    sim_student = sim_result.scalar_one_or_none()
    if sim_student is None:
        return []

    cards_result = await db.execute(
        select(Card)
        .where(Card.student_id == sim_student.id)
        .order_by(Card.created_at.desc())
    )
    cards = cards_result.scalars().all()

    def _parse_attrs(snapshot: str | None) -> tuple[str | None, str | None]:
        if not snapshot:
            return None, None
        try:
            cfg = json.loads(snapshot)
        except (TypeError, ValueError):
            return None, None
        if not isinstance(cfg, dict):
            return None, None
        return cfg.get("race"), cfg.get("gender")

    out = []
    for c in cards:
        race, gender = _parse_attrs(c.config_snapshot)
        out.append(
            {
                "id": c.id,
                "status": c.status,
                "thumbnail_url": c.thumbnail_url,
                "image_url": c.image_url,
                "level_number": c.level_number,
                "rarity": c.rarity,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "race": race,
                "gender": gender,
            }
        )
    return out


@router.delete("/api/admin/simulation/cards")
async def api_admin_simulation_clear(
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """清空所有模擬卡牌。"""
    sim_result = await db.execute(
        select(Student).where(Student.student_id == _SIMULATION_STUDENT_ID)
    )
    sim_student = sim_result.scalar_one_or_none()
    if sim_student is None:
        return {"deleted": 0}

    result = await db.execute(
        delete(Card).where(Card.student_id == sim_student.id)
    )
    await db.commit()
    return {"deleted": result.rowcount}


@router.get("/admin/simulation/cards/{card_id}")
async def admin_simulation_card_detail(
    card_id: int,
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """模擬卡牌詳情頁面 — 顯示卡牌圖片與完整生成提示詞。"""
    sim_student = await _get_or_create_simulation_student(db)
    result = await db.execute(
        select(Card).where(Card.id == card_id, Card.student_id == sim_student.id)
    )
    card = result.scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="找不到此模擬卡牌。")

    return templates.TemplateResponse(
        request,
        "admin/simulation_card_detail.html",
        {
            "user": user,
            "card": card,
            "student": sim_student,
            "page_title": f"模擬卡牌詳情 #{card.id}",
            "back_url": "/admin/simulation",
            "back_label": "返回模擬生圖",
            "show_admin_debug": True,
            "reuse_simulation_url": _build_simulation_reuse_url(card, sim_student),
        },
    )
