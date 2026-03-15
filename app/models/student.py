"""Student ORM model."""

from datetime import date, datetime, timezone

from sqlalchemy import Date, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    student_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(18), nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="student")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    last_login_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    learning_records: Mapped[list["LearningRecord"]] = relationship(
        "LearningRecord", back_populates="student"
    )
    card_configs: Mapped[list["CardConfig"]] = relationship(
        "CardConfig", back_populates="student"
    )
    cards: Mapped[list["Card"]] = relationship("Card", back_populates="student")
    token_transactions: Mapped[list["TokenTransaction"]] = relationship(
        "TokenTransaction", back_populates="student"
    )
    achievements: Mapped[list["StudentAchievement"]] = relationship(
        "StudentAchievement", back_populates="student", cascade="all, delete-orphan"
    )
