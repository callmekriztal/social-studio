import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.adapters.publisher_base import PublishResult, SocialPublisher
from app.adapters.registry import PublisherRegistry
from app.models.enums import PublishAttemptStatus, ScheduleSlotStatus, VariantStatus
from app.models.schedule_slot import ScheduleSlot
from app.models.variant import Variant
from app.repositories.mock_post_repository import MockPostRepository
from app.repositories.publish_attempt_repository import PublishAttemptRepository
from app.repositories.schedule_slot_repository import ScheduleSlotRepository
from app.repositories.variant_repository import VariantRepository
from app.services.review_service import ReviewService, VariantNotFoundError
from app.services.scheduling_guard import (
    SchedulingEligibilityError,
    SchedulingEligibilityGuard,
)


class PublishingError(Exception):
    """Base exception for publishing pipeline errors."""
    pass


class SlotNotFoundError(PublishingError):
    """Raised when the requested schedule slot does not exist."""
    pass


class SlotLockedError(PublishingError):
    """Raised when a schedule slot is already claimed or locked by another worker."""
    pass


class SlotReconciliationError(PublishingError):
    """Raised when a schedule slot requires manual reconciliation due to an uncertain previous attempt."""
    pass


def derive_slot_idempotency_key(variant_id: int, scheduled_at: datetime) -> str:
    """Derive a stable, deterministic idempotency key for a schedule slot.

    Guarantees that the same variant scheduled for the same time slot produces
    the identical key across all attempts, retries, and worker restarts.
    """
    ts_str = scheduled_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"slot:var-{variant_id}:{ts_str}"


@dataclass
class PublishOutcome:
    """Consolidated outcome of executing a controlled publish."""

    success: bool
    variant_id: int
    platform: str
    slot_id: Optional[int] = None
    external_post_id: Optional[str] = None
    preview: Optional[str] = None
    error: Optional[str] = None
    attempt_id: Optional[int] = None
    published_at: Optional[datetime] = None
    is_replay: bool = False
    is_uncertain: bool = False


class PublishingService:
    """Idempotent publishing service.

    Enforces approval and validation barriers, atomic slot claiming, stable
    idempotency keys, audit attempt persistence, and explicit handling
    for confirmed success, confirmed failure, and uncertain external outcomes.
    """

    @classmethod
    def _build_completed_replay_outcome(
        cls,
        db: Session,
        slot: ScheduleSlot,
        variant: Variant,
        platform_override: Optional[str] = None,
    ) -> PublishOutcome:
        """Construct an outcome for an already completed slot without re-invoking the publisher."""
        successful_attempt = PublishAttemptRepository.get_successful_attempt(db, slot.id)
        target_platform = platform_override or variant.platform

        preview: Optional[str] = None
        if successful_attempt and successful_attempt.external_post_id:
            mock_post = MockPostRepository.get_by_mock_id(db, successful_attempt.external_post_id)
            if mock_post:
                if variant.platform == "mock_x":
                    preview = (
                        f"--- [Mock X Post Preview] ---\n"
                        f"{mock_post.content}\n"
                        f"--- (Simulated post ID: {mock_post.mock_post_id}) ---"
                    )
                elif variant.platform == "mock_linkedin":
                    preview = (
                        f"=== [Mock LinkedIn Feed Preview] ===\n"
                        f"{mock_post.content}\n"
                        f"=== (Simulated share URN: {mock_post.mock_post_id}) ==="
                    )

        return PublishOutcome(
            success=True,
            variant_id=variant.id,
            platform=target_platform,
            slot_id=slot.id,
            external_post_id=successful_attempt.external_post_id if successful_attempt else None,
            preview=preview,
            attempt_id=successful_attempt.id if successful_attempt else None,
            published_at=slot.completed_at or (successful_attempt.finished_at if successful_attempt else None),
            is_replay=True,
        )

    @classmethod
    def publish_slot(
        cls,
        db: Session,
        slot_id: int,
        platform_override: Optional[str] = None,
    ) -> PublishOutcome:
        """Publish a specific ScheduleSlot with strict idempotency and concurrency protection.

        Workflow:
        1. Load and verify slot existence.
        2. Verify associated variant exists.
        3. If already COMPLETED: return existing successful outcome (idempotent replay).
        4. If RECONCILIATION_REQUIRED: reject automatic execution to avoid blind resends.
        5. Enforce SchedulingEligibilityGuard (variant must be approved and valid).
        6. Atomically claim slot (conditional UPDATE status=PROCESSING).
        7. Persist attempt record with STARTED status BEFORE calling publisher.
        8. Invoke publisher adapter with stable slot idempotency key.
        9. On confirmed success: mark attempt SUCCEEDED, slot COMPLETED, variant PUBLISHED.
        10. On uncertain outcome: mark attempt UNCERTAIN, slot RECONCILIATION_REQUIRED (no blind retry).
        11. On confirmed failure: mark attempt FAILED, slot FAILED.
        """
        # 1 & 2. Load slot and variant
        slot = ScheduleSlotRepository.get_by_id(db, slot_id)
        if not slot:
            raise SlotNotFoundError(f"Schedule slot with ID {slot_id} does not exist.")

        variant = ReviewService.get_variant_or_404(db, slot.variant_id)

        # 3. Check if already completed (Idempotent response)
        if slot.status == ScheduleSlotStatus.COMPLETED:
            return cls._build_completed_replay_outcome(db, slot, variant, platform_override)

        # 4. Check if slot requires manual reconciliation
        if slot.status == ScheduleSlotStatus.RECONCILIATION_REQUIRED:
            raise SlotReconciliationError(
                f"Schedule slot {slot.id} has an uncertain previous attempt and requires "
                f"manual reconciliation before retry."
            )

        # 5. Enforce approval and validation boundary
        SchedulingEligibilityGuard.assert_eligible_for_scheduling(variant)

        # 6. Atomically claim slot for processing
        claimed = ScheduleSlotRepository.claim_slot_for_processing(db, slot.id)
        if not claimed:
            # Refresh to inspect why claim failed
            refreshed = ScheduleSlotRepository.get_by_id(db, slot.id)
            if not refreshed:
                raise SlotNotFoundError(f"Schedule slot with ID {slot_id} does not exist.")
            if refreshed.status == ScheduleSlotStatus.COMPLETED:
                return cls._build_completed_replay_outcome(db, refreshed, variant, platform_override)
            if refreshed.status == ScheduleSlotStatus.PROCESSING:
                raise SlotLockedError(f"Schedule slot {slot_id} is currently being processed by another worker.")
            if refreshed.status == ScheduleSlotStatus.RECONCILIATION_REQUIRED:
                raise SlotReconciliationError(
                    f"Schedule slot {slot_id} requires manual reconciliation due to an uncertain previous attempt."
                )
            raise SlotLockedError(f"Schedule slot {slot_id} could not be claimed (current status: {refreshed.status.value}).")

        # 7. Persist attempt record BEFORE making external call
        existing_attempts = PublishAttemptRepository.list_by_slot_id(db, slot.id)
        attempt_number = len(existing_attempts) + 1
        started_at = datetime.now(timezone.utc)

        attempt = PublishAttemptRepository.create(
            db=db,
            schedule_slot_id=slot.id,
            attempt_number=attempt_number,
            status=PublishAttemptStatus.STARTED,
            started_at=started_at,
        )

        # 8. Select publisher and execute call
        target_platform = platform_override or variant.platform
        publisher: SocialPublisher = PublisherRegistry.get_publisher(target_platform)

        try:
            result: PublishResult = publisher.publish(
                content=variant.content,
                idempotency_key=slot.idempotency_key,
                db=db,
            )
        except Exception as exc:
            # Unhandled crash during external invocation: record as uncertain outcome
            error_msg = f"Crash/exception during publish call: {str(exc)}"
            PublishAttemptRepository.mark_uncertain(
                db=db,
                attempt_id=attempt.id,
                error_message=error_msg,
                finished_at=datetime.now(timezone.utc),
            )
            ScheduleSlotRepository.mark_reconciliation_required(
                db=db,
                slot_id=slot.id,
                error_message=error_msg,
            )
            raise

        # 9. Process result
        if result.success:
            PublishAttemptRepository.mark_success(
                db=db,
                attempt_id=attempt.id,
                external_post_id=result.external_post_id or "",
                finished_at=result.published_at or datetime.now(timezone.utc),
            )
            ScheduleSlotRepository.mark_completed(
                db=db,
                slot_id=slot.id,
                completed_at=result.published_at or datetime.now(timezone.utc),
            )
            VariantRepository.update_status(db, variant.id, VariantStatus.PUBLISHED)

            return PublishOutcome(
                success=True,
                variant_id=variant.id,
                platform=result.platform,
                slot_id=slot.id,
                external_post_id=result.external_post_id,
                preview=result.preview,
                error=None,
                attempt_id=attempt.id,
                published_at=result.published_at,
                is_replay=False,
                is_uncertain=False,
            )

        elif result.is_uncertain:
            PublishAttemptRepository.mark_uncertain(
                db=db,
                attempt_id=attempt.id,
                error_message=result.error or "Uncertain external delivery outcome",
                finished_at=datetime.now(timezone.utc),
            )
            ScheduleSlotRepository.mark_reconciliation_required(
                db=db,
                slot_id=slot.id,
                error_message=result.error or "Uncertain external delivery outcome",
            )

            return PublishOutcome(
                success=False,
                variant_id=variant.id,
                platform=result.platform,
                slot_id=slot.id,
                external_post_id=None,
                preview=None,
                error=result.error,
                attempt_id=attempt.id,
                published_at=None,
                is_replay=False,
                is_uncertain=True,
            )

        else:
            PublishAttemptRepository.mark_failure(
                db=db,
                attempt_id=attempt.id,
                error_message=result.error or "Publishing failed",
                finished_at=datetime.now(timezone.utc),
            )
            ScheduleSlotRepository.mark_failed(
                db=db,
                slot_id=slot.id,
                error_message=result.error or "Publishing failed",
            )

            return PublishOutcome(
                success=False,
                variant_id=variant.id,
                platform=result.platform,
                slot_id=slot.id,
                external_post_id=None,
                preview=None,
                error=result.error,
                attempt_id=attempt.id,
                published_at=None,
                is_replay=False,
                is_uncertain=False,
            )

    @classmethod
    def publish_variant(
        cls,
        db: Session,
        variant_id: int,
        platform_override: Optional[str] = None,
    ) -> PublishOutcome:
        """Publish an approved variant on demand via its schedule slot.

        Guarantees:
        - Must pass SchedulingEligibilityGuard (only valid, approved variants allowed).
        - Reuses existing schedule slot or creates one with deterministic idempotency key.
        - Delegates execution to publish_slot.
        """
        variant = ReviewService.get_variant_or_404(db, variant_id)
        SchedulingEligibilityGuard.assert_eligible_for_scheduling(variant)

        # Retrieve existing non-completed slot, or create deterministic slot
        slots = ScheduleSlotRepository.list_by_variant_id(db, variant_id)
        slot: Optional[ScheduleSlot] = None
        for s in reversed(slots):
            if s.status != ScheduleSlotStatus.COMPLETED:
                slot = s
                break

        if not slot:
            now = datetime.now(timezone.utc)
            idempotency_key = derive_slot_idempotency_key(variant_id, now)
            slot = ScheduleSlotRepository.create(
                db=db,
                variant_id=variant.id,
                scheduled_at=now,
                idempotency_key=idempotency_key,
                status=ScheduleSlotStatus.PENDING,
            )

        return cls.publish_slot(
            db=db,
            slot_id=slot.id,
            platform_override=platform_override,
        )
