import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.enums import ScheduleSlotStatus
from app.repositories.schedule_slot_repository import ScheduleSlotRepository
from app.services.publishing_service import (
    PublishOutcome,
    PublishingError,
    PublishingService,
)

logger = logging.getLogger(__name__)


class SchedulerService:
    """Database-backed durable scheduler service.

    Queries pending schedule slots that are due (scheduled_at <= now in UTC),
    processes them through PublishingService.publish_slot() in bounded batches,
    and isolates errors so a failure on one slot does not halt remaining slots.
    Supports deterministic time injection for testing.
    """

    @classmethod
    def process_due_slots(
        cls,
        db: Session,
        current_time: Optional[datetime] = None,
        batch_size: int = 10,
    ) -> List[PublishOutcome]:
        """Fetch and process a bounded batch of due pending slots.

        Args:
            db: Database session.
            current_time: Optional test-injected current timestamp. Normalized to UTC.
            batch_size: Maximum number of slots to process in this run.

        Returns:
            List of PublishOutcome objects representing the results for processed slots.
        """
        if current_time is None:
            now_utc = datetime.now(timezone.utc)
        elif current_time.tzinfo is None:
            now_utc = current_time.replace(tzinfo=timezone.utc)
        else:
            now_utc = current_time.astimezone(timezone.utc)

        due_slots = ScheduleSlotRepository.get_due_slots(
            db=db,
            max_time=now_utc,
            limit=batch_size,
        )

        outcomes: List[PublishOutcome] = []

        for slot in due_slots:
            try:
                outcome = PublishingService.publish_slot(db=db, slot_id=slot.id)
                outcomes.append(outcome)
            except Exception as exc:
                logger.error(
                    "Error processing schedule slot %s: %s",
                    slot.id,
                    exc,
                    exc_info=True,
                )
                # Ensure session is in a clean state if a database exception occurred
                try:
                    db.rollback()
                except Exception:
                    pass

                outcomes.append(
                    PublishOutcome(
                        success=False,
                        variant_id=slot.variant_id,
                        platform="unknown",
                        slot_id=slot.id,
                        error=f"Scheduler exception: {str(exc)}",
                    )
                )

        return outcomes

    @classmethod
    def get_status(
        cls,
        db: Session,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Obtain scheduler operational metrics and slot counts.

        Args:
            db: Database session.
            current_time: Optional test-injected current timestamp. Normalized to UTC.

        Returns:
            Dictionary with slot status counts and due slot count.
        """
        if current_time is None:
            now_utc = datetime.now(timezone.utc)
        elif current_time.tzinfo is None:
            now_utc = current_time.replace(tzinfo=timezone.utc)
        else:
            now_utc = current_time.astimezone(timezone.utc)

        status_counts = ScheduleSlotRepository.count_by_status(db)
        due_count = ScheduleSlotRepository.count_due_slots(db, max_time=now_utc)

        return {
            "pending_slots": status_counts.get(ScheduleSlotStatus.PENDING.value, 0),
            "due_slots": due_count,
            "processing_slots": status_counts.get(ScheduleSlotStatus.PROCESSING.value, 0),
            "completed_slots": status_counts.get(ScheduleSlotStatus.COMPLETED.value, 0),
            "failed_slots": status_counts.get(ScheduleSlotStatus.FAILED.value, 0),
            "reconciliation_required_slots": status_counts.get(ScheduleSlotStatus.RECONCILIATION_REQUIRED.value, 0),
            "current_time": now_utc,
        }
