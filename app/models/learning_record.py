"""LearningRecord ORM model."""

from datetime import datetime, timezone

from sqlalchemy import Integer, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LearningRecord(Base):
    __tablename__ = "learning_records"
    __table_args__ = (
        UniqueConstraint("student_id", "unit_id", name="uq_learning_record_student_unit"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id"), nullable=False
    )
    unit_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("units.id"), nullable=False
    )
    preview_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    quiz_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    student: Mapped["Student"] = relationship("Student", back_populates="learning_records")
    unit: Mapped["Unit"] = relationship("Unit", back_populates="learning_records")
