"""StudentAchievement ORM model and achievement type constants."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


ACHIEVEMENT_TYPES: dict[str, dict] = {
    "early_bird": {"label": "早鳥註冊", "tokens": 30, "icon": "bird"},
    "survey_initial": {"label": "完成期初問卷", "tokens": 20, "icon": "clipboard-list"},
    "survey_mid": {"label": "完成期中問卷", "tokens": 20, "icon": "clipboard-check"},
    "survey_final": {"label": "完成期末問卷", "tokens": 20, "icon": "file-check"},
    "chapter_1_complete": {"label": "完成第1章學習", "tokens": 10, "icon": "book-open"},
    "chapter_2_complete": {"label": "完成第2章學習", "tokens": 10, "icon": "book-open"},
    "chapter_3_complete": {"label": "完成第3章學習", "tokens": 10, "icon": "book-open"},
    "chapter_4_complete": {"label": "完成第4章學習", "tokens": 10, "icon": "book-open"},
    "chapter_5_complete": {"label": "完成第5章學習", "tokens": 10, "icon": "book-open"},
    "chapter_1_pretest": {"label": "完成第1章前測", "tokens": 10, "icon": "pen-line"},
    "chapter_2_pretest": {"label": "完成第2章前測", "tokens": 10, "icon": "pen-line"},
    "chapter_3_pretest": {"label": "完成第3章前測", "tokens": 10, "icon": "pen-line"},
    "chapter_4_pretest": {"label": "完成第4章前測", "tokens": 10, "icon": "pen-line"},
    "chapter_5_pretest": {"label": "完成第5章前測", "tokens": 10, "icon": "pen-line"},
}


class StudentAchievement(Base):
    __tablename__ = "student_achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    achievement_key: Mapped[str] = mapped_column(String(50), nullable=False)
    token_transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("token_transactions.id"), nullable=True
    )
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("student_id", "achievement_key", name="uq_student_achievement"),
    )

    # Relationships
    student: Mapped["Student"] = relationship("Student", back_populates="achievements")
    token_transaction: Mapped["TokenTransaction | None"] = relationship(
        "TokenTransaction"
    )
