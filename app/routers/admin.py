"""Admin dashboard routes — HTML pages and API endpoints."""

import csv
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
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
from app.services.excel_import import (
    ExcelParseResult,
    StudentRecord,
    parse_completion_excel,
    parse_score_excel,
)
from app.config import settings
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

    def _exp(unit: Unit, lr: LearningRecord | None) -> float | None:
        if lr is None:
            return None
        if unit.code == "unit_6":
            return lr.completion_rate  # may be None
        pv = (lr.preview_score or 0.0) * 0.2
        cv = (lr.completion_rate or 0.0) * 0.4
        qv = (lr.quiz_score or 0.0) * 0.4
        return round(pv + cv + qv, 1)

    student_data = []
    for s in students:
        unit_exps = []
        for u in units:
            lr = lr_map.get(s.id, {}).get(u.id)
            unit_exps.append({"unit": u, "exp": _exp(u, lr)})
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
) -> str:
    """Return an HTMX HTML fragment summarising the parse result."""
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

    commit_endpoint = (
        "/api/admin/import-excel/completion/commit"
        if import_type == "completion"
        else "/api/admin/import-excel/scores/commit"
    )

    confirm_btn = ""
    if will_update > 0:
        confirm_btn = f"""
        <form class="mt-3" enctype="multipart/form-data"
              hx-post="{commit_endpoint}"
              hx-encoding="multipart/form-data"
              hx-target="this"
              hx-swap="outerHTML">
          <input type="hidden" name="_preview_file_data" value="">
          <button type="submit"
                  class="flex items-center gap-2 rounded px-4 py-2
                         bg-[var(--rpg-gold-dark)] border-2 border-[var(--rpg-gold)]
                         font-tc text-xs font-bold text-[var(--rpg-gold-bright)]
                         transition-opacity hover:opacity-90"
                  onclick="this.closest('form').querySelector('[name=_preview_file_data]').value=window.__excelFile_{import_type} || ''; return true;"
                  hx-include="closest form">
            確認匯入
          </button>
        </form>"""

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
      {confirm_btn}
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
            update_fields=("completion_rate", "quiz_score"),
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


@router.post("/api/admin/import-excel/scores/preview", response_class=HTMLResponse)
async def api_excel_scores_preview(
    request: Request,
    file: UploadFile = File(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Parse score-list Excel and return an HTMX preview fragment."""
    _validate_excel_upload(file)
    content = await file.read()
    if len(content) > _MAX_EXCEL_SIZE:
        raise HTTPException(status_code=400, detail="檔案超過 5MB 限制")

    parse_result = parse_score_excel(content)

    students = (await db.execute(select(Student))).scalars().all()
    student_map = {s.student_id: s.id for s in students}

    return _build_preview_html(parse_result, student_map, "scores")


@router.post("/api/admin/import-excel/scores/commit", response_class=HTMLResponse)
async def api_excel_scores_commit(
    request: Request,
    file: UploadFile = File(...),
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Re-parse and commit score-list Excel data to the database."""
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
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Excel scores commit failed")
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


@router.get("/admin/generation-history")
async def admin_generation_history(
    request: Request,
    status_filter: str = "all",
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """生圖歷史紀錄頁面。"""
    query = (
        select(Card, Student.student_id, Student.name)
        .join(Student, Card.student_id == Student.id)
        .where(Card.job_id.isnot(None))
        .order_by(Card.created_at.desc())
    )
    if status_filter in ("generating", "completed", "failed"):
        query = query.where(Card.status == status_filter)

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
            "created_at_fmt": _fmt(start_local),
            "generated_at_fmt": _fmt(end_local),
            "duration": _duration(start_local, end_local),
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
    from app.services.scoring import roll_rarity

    body = await request.json()
    card_config: dict = body.get("card_config", {})
    level: int = max(1, min(100, int(body.get("level", 50))))
    rarity_input: str = body.get("rarity", "auto")
    nickname: str = body.get("nickname", "Admin Test") or "Admin Test"

    rarity = roll_rarity(level) if rarity_input == "auto" else rarity_input
    card_config["level"] = level
    card_config["rarity"] = rarity
    card_config["border"] = "copper"

    sim_student = await _get_or_create_simulation_student(db)

    new_card = Card(
        student_id=sim_student.id,
        config_snapshot=json.dumps(card_config),
        status="pending",
        border_style="copper",
        level_number=level,
        rarity=rarity,
        is_latest=False,
        is_display=False,
        is_hidden=True,
    )
    db.add(new_card)
    await db.commit()
    await db.refresh(new_card)

    learning_data = {
        "unit_scores": {},
        "overall_completion": float(level),
    }

    ai_worker = get_ai_worker_service()
    job_id = None
    try:
        job_id = await ai_worker.submit_generation(
            card_id=new_card.id,
            student_id="SIMULATION",
            student_nickname=nickname,
            card_config=card_config,
            learning_data=learning_data,
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

    return [
        {
            "id": c.id,
            "status": c.status,
            "thumbnail_url": c.thumbnail_url,
            "image_url": c.image_url,
            "level_number": c.level_number,
            "rarity": c.rarity,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in cards
    ]


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
