from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.publish_attempt_repository import PublishAttemptRepository
from app.schemas.scheduler import (
    PublishHistoryItemResponse,
    PublishHistoryListResponse,
)

router = APIRouter(prefix="/publish-history", tags=["publish-history"])


@router.get(
    "",
    response_model=PublishHistoryListResponse,
    summary="List publish attempt history",
    description="Retrieve paginated publish attempts in reverse-chronological order with optional filtering.",
)
def list_publish_history(
    variant_id: Optional[int] = Query(None, description="Filter by variant ID"),
    slot_id: Optional[int] = Query(None, description="Filter by schedule slot ID"),
    platform: Optional[str] = Query(None, description="Filter by platform identifier"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by attempt status (started, succeeded, failed, uncertain, reconciliation_required)"),
    limit: int = Query(50, ge=1, le=100, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
) -> PublishHistoryListResponse:
    """Retrieve publish attempt audit log records."""
    records, total = PublishAttemptRepository.list_history(
        db=db,
        variant_id=variant_id,
        slot_id=slot_id,
        platform=platform,
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    items = [
        PublishHistoryItemResponse(
            id=attempt.id,
            schedule_slot_id=attempt.schedule_slot_id,
            variant_id=var_id,
            platform=plat,
            attempt_number=attempt.attempt_number,
            status=attempt.status.value if hasattr(attempt.status, "value") else str(attempt.status),
            external_post_id=attempt.external_post_id,
            error_message=attempt.error_message,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
        )
        for attempt, var_id, plat in records
    ]

    return PublishHistoryListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{attempt_id}",
    response_model=PublishHistoryItemResponse,
    summary="Get single publish attempt detail",
    description="Retrieve full details for an individual publish attempt by ID.",
)
def get_publish_attempt_detail(
    attempt_id: int,
    db: Session = Depends(get_db),
) -> PublishHistoryItemResponse:
    """Retrieve an individual publish attempt by ID."""
    detail = PublishAttemptRepository.get_attempt_detail(db, attempt_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Publish attempt with ID {attempt_id} was not found.",
        )

    attempt, variant_id, platform = detail
    return PublishHistoryItemResponse(
        id=attempt.id,
        schedule_slot_id=attempt.schedule_slot_id,
        variant_id=variant_id,
        platform=platform,
        attempt_number=attempt.attempt_number,
        status=attempt.status.value if hasattr(attempt.status, "value") else str(attempt.status),
        external_post_id=attempt.external_post_id,
        error_message=attempt.error_message,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
    )
