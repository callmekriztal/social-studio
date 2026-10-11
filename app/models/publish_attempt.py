from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base
from app.models.enums import PublishAttemptStatus

if TYPE_CHECKING:
    from app.models.schedule_slot import ScheduleSlot


class PublishAttempt(Base):
    """Audit log of each individual publishing execution attempt for a scheduled slot."""

    __tablename__ = "publish_attempts"
    __table_args__ = (
        UniqueConstraint(
            "schedule_slot_id",
            "attempt_number",
            name="uq_slot_attempt_number",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    schedule_slot_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_slots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[PublishAttemptStatus] = mapped_column(
        Enum(PublishAttemptStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=PublishAttemptStatus.STARTED,
        nullable=False,
        index=True,
    )
    external_post_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    schedule_slot: Mapped["ScheduleSlot"] = relationship(
        "ScheduleSlot",
        back_populates="publish_attempts",
    )

    @validates("status")
    def validate_status(self, key: str, value: Any) -> PublishAttemptStatus:
        if isinstance(value, PublishAttemptStatus):
            return value
        if isinstance(value, str):
            try:
                return PublishAttemptStatus(value)
            except ValueError:
                raise ValueError(f"Invalid PublishAttemptStatus: {value!r}")
        raise ValueError(f"Invalid PublishAttemptStatus type: {type(value)}")

    def __repr__(self) -> str:
        return f"<PublishAttempt id={self.id} slot={self.schedule_slot_id} attempt={self.attempt_number} status={self.status.value!r}>"
