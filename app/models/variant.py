from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base
from app.models.enums import Platform, VariantStatus

if TYPE_CHECKING:
    from app.models.post import Post
    from app.models.schedule_slot import ScheduleSlot


class Variant(Base):
    """Platform-specific content variant generated from a blog post."""

    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[VariantStatus] = mapped_column(
        Enum(VariantStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=VariantStatus.DRAFT,
        nullable=False,
        index=True,
    )
    validation_errors: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(
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
    post: Mapped["Post"] = relationship(
        "Post",
        back_populates="variants",
    )
    schedule_slots: Mapped[List["ScheduleSlot"]] = relationship(
        "ScheduleSlot",
        back_populates="variant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @validates("status")
    def validate_status(self, key: str, value: Any) -> VariantStatus:
        if isinstance(value, VariantStatus):
            return value
        if isinstance(value, str):
            try:
                return VariantStatus(value)
            except ValueError:
                raise ValueError(f"Invalid VariantStatus: {value!r}")
        raise ValueError(f"Invalid VariantStatus type: {type(value)}")

    def __repr__(self) -> str:
        return f"<Variant id={self.id} platform={self.platform!r} status={self.status.value!r}>"
