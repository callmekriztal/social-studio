from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.registry import UnsupportedPublisherError
from app.database import get_db
from app.models.enums import VariantStatus
from app.repositories.schedule_slot_repository import ScheduleSlotRepository
from app.repositories.variant_repository import VariantRepository
from app.schemas.scheduler import ScheduleSlotResponse, ScheduleVariantRequest
from app.schemas.variant import (
    PublishVariantResponse,
    RejectVariantRequest,
    ValidateVariantResponse,
    VariantResponse,
    VariantUpdateContent,
)
from app.services.publishing_service import (
    PublishingService,
    SlotLockedError,
    SlotNotFoundError,
    SlotReconciliationError,
    derive_slot_idempotency_key,
)
from app.services.review_service import (
    InvalidTransitionError,
    ReviewService,
    ValidationErrorException,
    VariantNotFoundError,
)
from app.services.scheduling_guard import (
    SchedulingEligibilityError,
    SchedulingEligibilityGuard,
)
from app.validators.validator import VariantValidator

router = APIRouter(prefix="/variants", tags=["variants"])


@router.get(
    "",
    response_model=List[VariantResponse],
    summary="List variants awaiting review",
    description="Retrieve variants with optional filtering by status (e.g. status=draft) and platform.",
)
def list_variants(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (e.g. draft, approved, rejected, published)"),
    platform_filter: Optional[str] = Query(None, alias="platform", description="Filter by platform identifier"),
    db: Session = Depends(get_db),
) -> List[VariantResponse]:
    """List variants with optional filtering by status and platform."""
    variants = ReviewService.list_variants(
        db=db,
        status=status_filter,
        platform=platform_filter,
    )
    return [VariantResponse.model_validate(v) for v in variants]


@router.get(
    "/{variant_id}",
    response_model=VariantResponse,
    summary="Get variant by ID",
)
def get_variant(
    variant_id: int,
    db: Session = Depends(get_db),
) -> VariantResponse:
    """Retrieve a single variant by ID."""
    try:
        variant = ReviewService.get_variant_or_404(db, variant_id)
        return VariantResponse.model_validate(variant)
    except VariantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch(
    "/{variant_id}",
    response_model=VariantResponse,
    summary="Edit variant content",
    description="Edits a draft variant or resets an approved variant back to draft upon editing. Revalidates immediately.",
)
def edit_variant_endpoint(
    variant_id: int,
    payload: VariantUpdateContent,
    db: Session = Depends(get_db),
) -> VariantResponse:
    """Edit variant content with strict revalidation.

    Approved variants edited here automatically revert to DRAFT.
    Invalid content is rejected with 422.
    """
    try:
        updated = ReviewService.edit_variant(
            db=db,
            variant_id=variant_id,
            new_content=payload.content,
        )
        return VariantResponse.model_validate(updated)
    except VariantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationErrorException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(e), "errors": e.errors},
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/{variant_id}/approve",
    response_model=VariantResponse,
    summary="Approve a valid draft variant",
    description="Transitions a valid draft variant to APPROVED. Revalidates content immediately.",
)
def approve_variant_endpoint(
    variant_id: int,
    db: Session = Depends(get_db),
) -> VariantResponse:
    """Approve a draft variant. Revalidates immediately; rejects invalid variants with 422."""
    try:
        approved = ReviewService.approve_variant(db=db, variant_id=variant_id)
        return VariantResponse.model_validate(approved)
    except VariantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationErrorException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(e), "errors": e.errors},
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/{variant_id}/reject",
    response_model=VariantResponse,
    summary="Reject a draft or approved variant",
    description="Transitions a draft or approved variant to REJECTED with an optional reason.",
)
def reject_variant_endpoint(
    variant_id: int,
    payload: Optional[RejectVariantRequest] = None,
    db: Session = Depends(get_db),
) -> VariantResponse:
    """Reject a variant with an optional rejection reason."""
    reason = payload.reason if payload else None
    try:
        rejected = ReviewService.reject_variant(
            db=db,
            variant_id=variant_id,
            reason=reason,
        )
        return VariantResponse.model_validate(rejected)
    except VariantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/{variant_id}/revise",
    response_model=VariantResponse,
    summary="Return a rejected variant to draft",
    description="Transitions a REJECTED variant back to DRAFT so it can be edited and reviewed again.",
)
def revise_variant_endpoint(
    variant_id: int,
    db: Session = Depends(get_db),
) -> VariantResponse:
    """Return a rejected variant back to draft for revision."""
    try:
        revised = ReviewService.revise_variant(db=db, variant_id=variant_id)
        return VariantResponse.model_validate(revised)
    except VariantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/{variant_id}/validate",
    response_model=ValidateVariantResponse,
    summary="Revalidate an existing variant against platform constraints",
)
def validate_variant_endpoint(
    variant_id: int,
    db: Session = Depends(get_db),
) -> ValidateVariantResponse:
    """Revalidate an existing variant against its platform constraint profile."""
    variant = VariantRepository.get_by_id(db, variant_id)
    if not variant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Variant with ID {variant_id} does not exist.",
        )

    result = VariantValidator.validate(variant.platform, variant.content)

    if not result.is_valid:
        variant.status = VariantStatus.REJECTED
        variant.validation_errors = result.to_dict()
        db.commit()
        db.refresh(variant)
    else:
        variant.validation_errors = None
        db.commit()
        db.refresh(variant)

    return ValidateVariantResponse(
        variant_id=variant.id,
        platform=variant.platform,
        is_valid=result.is_valid,
        status=variant.status.value,
        character_count=result.character_count,
        hashtag_count=result.hashtag_count,
        errors=result.errors,
        warnings=result.warnings,
    )


@router.post(
    "/{variant_id}/publish",
    response_model=PublishVariantResponse,
    summary="Publish an approved variant",
    description="Publishes an approved variant immediately via its platform publisher adapter or optional override.",
)
def publish_variant_endpoint(
    variant_id: int,
    platform: Optional[str] = Query(None, description="Optional target publisher override (e.g. mock_x, mock_linkedin, telegram)"),
    db: Session = Depends(get_db),
) -> PublishVariantResponse:
    """Publish an approved variant.

    Enforces SchedulingEligibilityGuard (only valid, approved variants can be published).
    Records publishing attempt and transitions variant status to PUBLISHED upon success.
    """
    try:
        outcome = PublishingService.publish_variant(
            db=db,
            variant_id=variant_id,
            platform_override=platform,
        )
        return PublishVariantResponse(
            success=outcome.success,
            variant_id=outcome.variant_id,
            platform=outcome.platform,
            slot_id=outcome.slot_id,
            external_post_id=outcome.external_post_id,
            preview=outcome.preview,
            error=outcome.error,
            attempt_id=outcome.attempt_id,
            published_at=outcome.published_at,
            is_replay=outcome.is_replay,
            is_uncertain=outcome.is_uncertain,
        )
    except VariantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except SlotNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except SlotLockedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except SlotReconciliationError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except SchedulingEligibilityError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except UnsupportedPublisherError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/{variant_id}/schedule",
    response_model=ScheduleSlotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule an approved variant for publication",
    description="Schedules an approved, valid variant for publication at a specified timezone-aware UTC timestamp.",
)
def schedule_variant_endpoint(
    variant_id: int,
    payload: ScheduleVariantRequest,
    db: Session = Depends(get_db),
) -> ScheduleSlotResponse:
    """Schedule an approved variant for publication.

    - Validates that the variant exists (HTTP 404).
    - Enforces SchedulingEligibilityGuard: variant must be approved and valid (HTTP 400).
    - Validates timezone-aware timestamp and normalizes to UTC.
    - Prevents duplicates for the same variant and timestamp slot (HTTP 409 Conflict).
    - Past timestamps are allowed and immediately due for the background worker.
    """
    try:
        variant = ReviewService.get_variant_or_404(db, variant_id)
        SchedulingEligibilityGuard.assert_eligible_for_scheduling(variant)
    except VariantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except SchedulingEligibilityError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Check for duplicate schedule on the same variant and scheduled timestamp
    existing_slot = ScheduleSlotRepository.get_by_variant_and_time(
        db=db,
        variant_id=variant.id,
        scheduled_at=payload.scheduled_at,
    )
    if existing_slot:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Variant {variant_id} is already scheduled for timestamp {payload.scheduled_at.isoformat()}.",
        )

    idempotency_key = derive_slot_idempotency_key(variant.id, payload.scheduled_at)
    existing_by_key = ScheduleSlotRepository.get_by_idempotency_key(db, idempotency_key)
    if existing_by_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Schedule slot with idempotency key '{idempotency_key}' already exists.",
        )

    try:
        slot = ScheduleSlotRepository.create(
            db=db,
            variant_id=variant.id,
            scheduled_at=payload.scheduled_at,
            idempotency_key=idempotency_key,
        )
        return ScheduleSlotResponse.model_validate(slot)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conflict scheduling variant {variant_id}: duplicate slot or key.",
        )
