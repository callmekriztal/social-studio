from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional
from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base
from app.models.enums import ScheduleSlotStatus

if TYPE_CHECKING:
    from app.models.publish_attempt import PublishAttempt
    from app.models.variant import Variant


class ScheduleSlot(Base):
    """Represents a scheduled publishing window for an approved variant."""

    __tablename__ = "schedule_slots"
    __table_args__ = (
        # Prevent scheduling the same variant for the exact same timestamp
        UniqueConstraint("variant_id", "scheduled_at", name="uq_variant_scheduled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    status: Mapped[ScheduleSlotStatus] = mapped_column(
        Enum(ScheduleSlotStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=ScheduleSlotStatus.PENDING,
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    processing_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    variant: Mapped["Variant"] = relationship(
        "Variant",
        back_populates="schedule_slots",
    )
    publish_attempts: Mapped[List["PublishAttempt"]] = relationship(
        "PublishAttempt",
        back_populates="schedule_slot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PublishAttempt.attempt_number",
    )

    @validates("status")
    def validate_status(self, key: str, value: Any) -> ScheduleSlotStatus:
        if isinstance(value, ScheduleSlotStatus):
            return value
        if isinstance(value, str):
            try:
                return ScheduleSlotStatus(value)
            except ValueError:
                raise ValueError(f"Invalid ScheduleSlotStatus: {value!r}")
        raise ValueError(f"Invalid ScheduleSlotStatus type: {type(value)}")

    def __repr__(self) -> str:
        return f"<ScheduleSlot id={self.id} variant_id={self.variant_id} status={self.status.value!r}>"
