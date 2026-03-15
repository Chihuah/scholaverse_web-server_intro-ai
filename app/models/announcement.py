"""Announcement ORM models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    created_by: Mapped["Student"] = relationship("Student")
    reads: Mapped[list["AnnouncementRead"]] = relationship(
        "AnnouncementRead", back_populates="announcement", cascade="all, delete-orphan"
    )


class AnnouncementRead(Base):
    __tablename__ = "announcement_reads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    announcement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (UniqueConstraint("announcement_id", "student_id"),)

    # Relationships
    announcement: Mapped["Announcement"] = relationship("Announcement", back_populates="reads")
    student: Mapped["Student"] = relationship("Student")
