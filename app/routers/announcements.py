"""Announcements router — admin CRUD + student browsing."""

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user, require_teacher
from app.models.announcement import Announcement, AnnouncementRead
from app.models.student import Student
from app.templating import templates

router = APIRouter()


# ---------------------------------------------------------------------------
# Student routes
# ---------------------------------------------------------------------------


@router.get("/announcements")
async def announcements_list(
    request: Request,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Show published announcements with read status for the current user."""
    result = await db.execute(
        select(Announcement)
        .where(Announcement.is_published == True)  # noqa: E712
        .order_by(Announcement.created_at.desc())
        .options(selectinload(Announcement.reads))
    )
    announcements = result.scalars().all()

    # Build a set of announcement IDs the user has read
    read_ids: set[int] = set()
    if announcements:
        read_result = await db.execute(
            select(AnnouncementRead.announcement_id).where(
                AnnouncementRead.student_id == user.id
            )
        )
        read_ids = {row[0] for row in read_result.fetchall()}

    items = [
        {"announcement": a, "is_read": a.id in read_ids}
        for a in announcements
    ]

    return templates.TemplateResponse(
        request,
        "announcements/list.html",
        {"user": user, "items": items},
    )


@router.post("/api/announcements/{announcement_id}/read")
async def mark_announcement_read(
    announcement_id: int,
    user: Student = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark an announcement as read for the current user (idempotent)."""
    # Verify announcement exists and is published
    result = await db.execute(
        select(Announcement).where(
            Announcement.id == announcement_id,
            Announcement.is_published == True,  # noqa: E712
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Announcement not found")

    # Insert if not exists (ignore duplicates)
    existing = await db.execute(
        select(AnnouncementRead).where(
            AnnouncementRead.announcement_id == announcement_id,
            AnnouncementRead.student_id == user.id,
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(AnnouncementRead(announcement_id=announcement_id, student_id=user.id))
        await db.commit()

    return {"success": True}


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------


@router.get("/admin/announcements")
async def admin_announcements(
    request: Request,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Admin page: list all announcements with read counts."""
    result = await db.execute(
        select(
            Announcement,
            func.count(AnnouncementRead.id).label("read_count"),
        )
        .outerjoin(AnnouncementRead, AnnouncementRead.announcement_id == Announcement.id)
        .group_by(Announcement.id)
        .order_by(Announcement.created_at.desc())
        .options(selectinload(Announcement.created_by))
    )
    rows = result.all()
    items = [{"announcement": row[0], "read_count": row[1]} for row in rows]

    return templates.TemplateResponse(
        request,
        "admin/announcements.html",
        {"user": user, "items": items},
    )


@router.post("/api/admin/announcements")
async def api_create_announcement(
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
    payload: dict = Body(...),
):
    """Create a new announcement."""
    title = (payload.get("title") or "").strip()
    content = (payload.get("content") or "").strip()
    is_published = bool(payload.get("is_published", True))

    if not title:
        raise HTTPException(status_code=422, detail="標題不能為空")
    if not content:
        raise HTTPException(status_code=422, detail="內容不能為空")

    announcement = Announcement(
        title=title,
        content=content,
        is_published=is_published,
        created_by_id=user.id,
    )
    db.add(announcement)
    await db.commit()
    await db.refresh(announcement)

    return {"success": True, "id": announcement.id}


@router.put("/api/admin/announcements/{announcement_id}")
async def api_update_announcement(
    announcement_id: int,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
    payload: dict = Body(...),
):
    """Update an existing announcement."""
    result = await db.execute(
        select(Announcement).where(Announcement.id == announcement_id)
    )
    announcement = result.scalar_one_or_none()
    if announcement is None:
        raise HTTPException(status_code=404, detail="Announcement not found")

    title = (payload.get("title") or "").strip()
    content = (payload.get("content") or "").strip()

    if not title:
        raise HTTPException(status_code=422, detail="標題不能為空")
    if not content:
        raise HTTPException(status_code=422, detail="內容不能為空")

    announcement.title = title
    announcement.content = content
    announcement.is_published = bool(payload.get("is_published", announcement.is_published))
    await db.commit()

    return {"success": True}


@router.delete("/api/admin/announcements/{announcement_id}")
async def api_delete_announcement(
    announcement_id: int,
    user: Student = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
):
    """Delete an announcement and all its read records."""
    result = await db.execute(
        select(Announcement).where(Announcement.id == announcement_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Announcement not found")

    await db.execute(delete(Announcement).where(Announcement.id == announcement_id))
    await db.commit()

    return {"success": True}
