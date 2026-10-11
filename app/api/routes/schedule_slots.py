from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.adapters.registry import UnsupportedPublisherError
from app.database import get_db
from datetime import datetime
from app.schemas.scheduler import SchedulerStatusResponse
from app.schemas.variant import PublishVariantResponse
from app.scheduler.service import SchedulerService
from app.services.publishing_service import (
    PublishingService,
    SlotLockedError,
    SlotNotFoundError,
    SlotReconciliationError,
)
from app.services.review_service import VariantNotFoundError
from app.services.scheduling_guard import SchedulingEligibilityError

router = APIRouter(prefix="/schedule-slots", tags=["schedule-slots"])


@router.get(
    "/status",
    response_model=SchedulerStatusResponse,
    summary="Get scheduler operational status and slot metrics",
    description="Returns observability metrics including counts of pending, due, processing, completed, failed, and reconciliation-required slots.",
)
def get_schedule_slots_status(
    as_of: Optional[datetime] = Query(
        None,
        description="Optional reference timestamp for calculating due slots (defaults to current UTC time).",
    ),
    db: Session = Depends(get_db),
) -> SchedulerStatusResponse:
    """Retrieve operational metrics for schedule slots."""
    status_data = SchedulerService.get_status(db=db, current_time=as_of)
    return SchedulerStatusResponse(**status_data)


@router.post(
    "/{slot_id}/publish",
    response_model=PublishVariantResponse,
    summary="Publish a specific schedule slot idempotently",
    description="Internal/development testing endpoint to publish a scheduled slot idempotently with concurrency protection.",
)
def publish_slot_endpoint(
    slot_id: int,
    platform: Optional[str] = Query(None, description="Optional target publisher override (e.g. mock_x, mock_linkedin, telegram)"),
    db: Session = Depends(get_db),
) -> PublishVariantResponse:
    """Publish a specific schedule slot with strict idempotency and duplicate prevention.

    - Repeating a successful request returns the existing successful result without re-publishing.
    - Concurrent requests return HTTP 409 Conflict if another worker currently owns the slot.
    - Slots requiring reconciliation return HTTP 409 Conflict.
    - Unapproved or invalid variants return HTTP 400 Bad Request.
    - Missing slots return HTTP 404 Not Found.
    """
    try:
        outcome = PublishingService.publish_slot(
            db=db,
            slot_id=slot_id,
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
    except SlotNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except VariantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except SlotLockedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except SlotReconciliationError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except SchedulingEligibilityError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except UnsupportedPublisherError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
