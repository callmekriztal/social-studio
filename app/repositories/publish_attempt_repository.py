from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import PublishAttemptStatus
from app.models.publish_attempt import PublishAttempt
from app.models.schedule_slot import ScheduleSlot
from app.models.variant import Variant


class PublishAttemptRepository:
    """Repository managing persistence and retrieval for PublishAttempts."""

    @staticmethod
    def create(
        db: Session,
        schedule_slot_id: int,
        attempt_number: int,
        status: PublishAttemptStatus = PublishAttemptStatus.STARTED,
        external_post_id: Optional[str] = None,
        error_message: Optional[str] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
    ) -> PublishAttempt:
        """Create and persist a new PublishAttempt record."""
        attempt = PublishAttempt(
            schedule_slot_id=schedule_slot_id,
            attempt_number=attempt_number,
            status=status,
            external_post_id=external_post_id,
            error_message=error_message,
            started_at=started_at or datetime.now(timezone.utc),
            finished_at=finished_at,
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)
        return attempt

    @staticmethod
    def get_by_id(db: Session, attempt_id: int) -> Optional[PublishAttempt]:
        """Retrieve a PublishAttempt by its ID."""
        return db.get(PublishAttempt, attempt_id)

    @staticmethod
    def list_by_slot_id(db: Session, schedule_slot_id: int) -> List[PublishAttempt]:
        """List all attempts for a given schedule slot, ordered by attempt number."""
        stmt = (
            select(PublishAttempt)
            .where(PublishAttempt.schedule_slot_id == schedule_slot_id)
            .order_by(PublishAttempt.attempt_number.asc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def get_latest_attempt(
        db: Session,
        schedule_slot_id: int,
    ) -> Optional[PublishAttempt]:
        """Get the most recent attempt for a schedule slot."""
        stmt = (
            select(PublishAttempt)
            .where(PublishAttempt.schedule_slot_id == schedule_slot_id)
            .order_by(PublishAttempt.attempt_number.desc())
            .limit(1)
        )
        return db.scalar(stmt)

    @staticmethod
    def mark_success(
        db: Session,
        attempt_id: int,
        external_post_id: str,
        finished_at: Optional[datetime] = None,
    ) -> Optional[PublishAttempt]:
        """Mark an attempt as succeeded."""
        attempt = db.get(PublishAttempt, attempt_id)
        if attempt:
            attempt.status = PublishAttemptStatus.SUCCEEDED
            attempt.external_post_id = external_post_id
            attempt.finished_at = finished_at or datetime.now(timezone.utc)
            db.commit()
            db.refresh(attempt)
        return attempt

    @staticmethod
    def mark_failure(
        db: Session,
        attempt_id: int,
        error_message: str,
        finished_at: Optional[datetime] = None,
    ) -> Optional[PublishAttempt]:
        """Mark an attempt as failed."""
        attempt = db.get(PublishAttempt, attempt_id)
        if attempt:
            attempt.status = PublishAttemptStatus.FAILED
            attempt.error_message = error_message
            attempt.finished_at = finished_at or datetime.now(timezone.utc)
            db.commit()
            db.refresh(attempt)
        return attempt

    @staticmethod
    def mark_uncertain(
        db: Session,
        attempt_id: int,
        error_message: str,
        finished_at: Optional[datetime] = None,
    ) -> Optional[PublishAttempt]:
        """Mark an attempt as uncertain / reconciliation required."""
        attempt = db.get(PublishAttempt, attempt_id)
        if attempt:
            attempt.status = PublishAttemptStatus.UNCERTAIN
            attempt.error_message = error_message
            attempt.finished_at = finished_at or datetime.now(timezone.utc)
            db.commit()
            db.refresh(attempt)
        return attempt

    @staticmethod
    def get_successful_attempt(
        db: Session,
        schedule_slot_id: int,
    ) -> Optional[PublishAttempt]:
        """Get the succeeded attempt for a schedule slot, if any."""
        stmt = (
            select(PublishAttempt)
            .where(
                PublishAttempt.schedule_slot_id == schedule_slot_id,
                PublishAttempt.status == PublishAttemptStatus.SUCCEEDED,
            )
            .limit(1)
        )
        return db.scalar(stmt)

    @staticmethod
    def list_history(
        db: Session,
        variant_id: Optional[int] = None,
        slot_id: Optional[int] = None,
        platform: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Tuple[PublishAttempt, int, str]], int]:
        """List publish attempts with joins to slot and variant for filtering and rich response.

        Returns:
            Tuple of (list of (attempt, variant_id, platform), total_count)
        """
        base_query = (
            select(PublishAttempt, ScheduleSlot.variant_id, Variant.platform)
            .join(ScheduleSlot, PublishAttempt.schedule_slot_id == ScheduleSlot.id)
            .join(Variant, ScheduleSlot.variant_id == Variant.id)
        )
        count_query = (
            select(func.count(PublishAttempt.id))
            .join(ScheduleSlot, PublishAttempt.schedule_slot_id == ScheduleSlot.id)
            .join(Variant, ScheduleSlot.variant_id == Variant.id)
        )

        if variant_id is not None:
            base_query = base_query.where(ScheduleSlot.variant_id == variant_id)
            count_query = count_query.where(ScheduleSlot.variant_id == variant_id)

        if slot_id is not None:
            base_query = base_query.where(PublishAttempt.schedule_slot_id == slot_id)
            count_query = count_query.where(PublishAttempt.schedule_slot_id == slot_id)

        if platform is not None:
            base_query = base_query.where(Variant.platform == platform)
            count_query = count_query.where(Variant.platform == platform)

        if status is not None:
            try:
                status_enum = PublishAttemptStatus(status)
                base_query = base_query.where(PublishAttempt.status == status_enum)
                count_query = count_query.where(PublishAttempt.status == status_enum)
            except ValueError:
                base_query = base_query.where(PublishAttempt.status == status)
                count_query = count_query.where(PublishAttempt.status == status)

        total = db.scalar(count_query) or 0

        paged_query = (
            base_query.order_by(PublishAttempt.started_at.desc(), PublishAttempt.id.desc())
            .offset(offset)
            .limit(limit)
        )
        results = [(row[0], row[1], row[2]) for row in db.execute(paged_query).all()]
        return results, total

    @staticmethod
    def get_attempt_detail(
        db: Session,
        attempt_id: int,
    ) -> Optional[Tuple[PublishAttempt, int, str]]:
        """Retrieve detailed single publish attempt along with variant_id and platform."""
        stmt = (
            select(PublishAttempt, ScheduleSlot.variant_id, Variant.platform)
            .join(ScheduleSlot, PublishAttempt.schedule_slot_id == ScheduleSlot.id)
            .join(Variant, ScheduleSlot.variant_id == Variant.id)
            .where(PublishAttempt.id == attempt_id)
        )
        row = db.execute(stmt).first()
        if not row:
            return None
        return (row[0], row[1], row[2])
