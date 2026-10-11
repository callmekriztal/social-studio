from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models.enums import ScheduleSlotStatus
from app.models.schedule_slot import ScheduleSlot


class ScheduleSlotRepository:
    """Repository managing persistence and retrieval for ScheduleSlots."""

    @staticmethod
    def create(
        db: Session,
        variant_id: int,
        scheduled_at: datetime,
        idempotency_key: str,
        status: ScheduleSlotStatus = ScheduleSlotStatus.PENDING,
    ) -> ScheduleSlot:
        """Create and persist a new ScheduleSlot."""
        slot = ScheduleSlot(
            variant_id=variant_id,
            scheduled_at=scheduled_at,
            idempotency_key=idempotency_key,
            status=status,
        )
        db.add(slot)
        db.commit()
        db.refresh(slot)
        return slot

    @staticmethod
    def get_by_id(db: Session, slot_id: int) -> Optional[ScheduleSlot]:
        """Retrieve a ScheduleSlot by its ID."""
        return db.get(ScheduleSlot, slot_id)

    @staticmethod
    def get_by_idempotency_key(
        db: Session,
        idempotency_key: str,
    ) -> Optional[ScheduleSlot]:
        """Retrieve a ScheduleSlot by its unique idempotency key."""
        stmt = select(ScheduleSlot).where(
            ScheduleSlot.idempotency_key == idempotency_key
        )
        return db.scalar(stmt)

    @staticmethod
    def get_with_attempts(db: Session, slot_id: int) -> Optional[ScheduleSlot]:
        """Retrieve a ScheduleSlot along with eagerly loaded publish attempts."""
        stmt = (
            select(ScheduleSlot)
            .where(ScheduleSlot.id == slot_id)
            .options(selectinload(ScheduleSlot.publish_attempts))
        )
        return db.scalar(stmt)

    @staticmethod
    def list_by_variant_id(db: Session, variant_id: int) -> List[ScheduleSlot]:
        """List all schedule slots for a given variant."""
        stmt = (
            select(ScheduleSlot)
            .where(ScheduleSlot.variant_id == variant_id)
            .order_by(ScheduleSlot.scheduled_at.asc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def get_by_variant_and_time(
        db: Session,
        variant_id: int,
        scheduled_at: datetime,
    ) -> Optional[ScheduleSlot]:
        """Retrieve a schedule slot for a specific variant and scheduled timestamp."""
        stmt = select(ScheduleSlot).where(
            ScheduleSlot.variant_id == variant_id,
            ScheduleSlot.scheduled_at == scheduled_at,
        )
        return db.scalar(stmt)

    @staticmethod
    def get_due_slots(
        db: Session,
        max_time: datetime,
        limit: int = 10,
    ) -> List[ScheduleSlot]:
        """Retrieve pending schedule slots whose scheduled_at is less than or equal to max_time.

        Orders by scheduled_at asc (FIFO for due slots) and limits results to bounded batch size.
        """
        stmt = (
            select(ScheduleSlot)
            .where(
                ScheduleSlot.status == ScheduleSlotStatus.PENDING,
                ScheduleSlot.scheduled_at <= max_time,
            )
            .order_by(ScheduleSlot.scheduled_at.asc(), ScheduleSlot.id.asc())
            .limit(limit)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def count_by_status(db: Session) -> Dict[str, int]:
        """Return counts of slots grouped by their status."""
        counts = {status.value: 0 for status in ScheduleSlotStatus}
        stmt = select(ScheduleSlot.status, func.count(ScheduleSlot.id)).group_by(ScheduleSlot.status)
        for status_enum, count in db.execute(stmt).all():
            val = status_enum.value if hasattr(status_enum, "value") else str(status_enum)
            counts[val] = count
        return counts

    @staticmethod
    def count_due_slots(db: Session, max_time: datetime) -> int:
        """Count pending slots that are due at or before max_time."""
        stmt = select(func.count(ScheduleSlot.id)).where(
            ScheduleSlot.status == ScheduleSlotStatus.PENDING,
            ScheduleSlot.scheduled_at <= max_time,
        )
        return db.scalar(stmt) or 0

    @staticmethod
    def claim_slot_for_processing(
        db: Session,
        slot_id: int,
        claimed_at: Optional[datetime] = None,
    ) -> bool:
        """Atomically claim a slot for processing via conditional update.

        Only claims if the slot is currently PENDING or FAILED.
        Returns True if claimed (rowcount == 1), False otherwise.
        """
        now = claimed_at or datetime.now(timezone.utc)
        stmt = (
            update(ScheduleSlot)
            .where(
                ScheduleSlot.id == slot_id,
                ScheduleSlot.status.in_([ScheduleSlotStatus.PENDING, ScheduleSlotStatus.FAILED]),
            )
            .values(
                status=ScheduleSlotStatus.PROCESSING,
                processing_started_at=now,
                updated_at=now,
            )
        )
        result = db.execute(stmt)
        db.commit()
        return result.rowcount > 0

    @staticmethod
    def mark_completed(
        db: Session,
        slot_id: int,
        completed_at: Optional[datetime] = None,
    ) -> Optional[ScheduleSlot]:
        """Mark a ScheduleSlot as completed with completion timestamp."""
        now = completed_at or datetime.now(timezone.utc)
        slot = db.get(ScheduleSlot, slot_id)
        if slot:
            slot.status = ScheduleSlotStatus.COMPLETED
            slot.completed_at = now
            slot.last_error = None
            slot.updated_at = now
            db.commit()
            db.refresh(slot)
        return slot

    @staticmethod
    def mark_failed(
        db: Session,
        slot_id: int,
        error_message: str,
    ) -> Optional[ScheduleSlot]:
        """Mark a ScheduleSlot as failed with error message."""
        now = datetime.now(timezone.utc)
        slot = db.get(ScheduleSlot, slot_id)
        if slot:
            slot.status = ScheduleSlotStatus.FAILED
            slot.last_error = error_message
            slot.updated_at = now
            db.commit()
            db.refresh(slot)
        return slot

    @staticmethod
    def mark_reconciliation_required(
        db: Session,
        slot_id: int,
        error_message: str,
    ) -> Optional[ScheduleSlot]:
        """Mark a ScheduleSlot as requiring manual reconciliation due to an uncertain outcome."""
        now = datetime.now(timezone.utc)
        slot = db.get(ScheduleSlot, slot_id)
        if slot:
            slot.status = ScheduleSlotStatus.RECONCILIATION_REQUIRED
            slot.last_error = error_message
            slot.updated_at = now
            db.commit()
            db.refresh(slot)
        return slot

    @staticmethod
    def update_status(
        db: Session,
        slot_id: int,
        status: ScheduleSlotStatus,
    ) -> Optional[ScheduleSlot]:
        """Update the status of a ScheduleSlot."""
        slot = db.get(ScheduleSlot, slot_id)
        if slot:
            slot.status = status
            db.commit()
            db.refresh(slot)
        return slot

    @staticmethod
    def delete(db: Session, slot_id: int) -> bool:
        """Delete a ScheduleSlot by ID."""
        slot = db.get(ScheduleSlot, slot_id)
        if slot:
            db.delete(slot)
            db.commit()
            return True
        return False
