from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.scheduler import SchedulerStatusResponse
from app.scheduler.service import SchedulerService

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.get(
    "/status",
    response_model=SchedulerStatusResponse,
    summary="Get scheduler operational status and slot metrics",
    description="Returns observability metrics including counts of pending, due, processing, completed, failed, and reconciliation-required slots.",
)
def get_scheduler_status(
    as_of: Optional[datetime] = Query(
        None,
        description="Optional reference timestamp for calculating due slots (defaults to current UTC time).",
    ),
    db: Session = Depends(get_db),
) -> SchedulerStatusResponse:
    """Retrieve scheduler operational metrics."""
    status_data = SchedulerService.get_status(db=db, current_time=as_of)
    return SchedulerStatusResponse(**status_data)
