from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScheduleVariantRequest(BaseModel):
    """Payload to schedule an approved variant for publication."""

    scheduled_at: datetime = Field(
        ...,
        description="Timezone-aware scheduled timestamp (e.g. 2026-10-10T15:30:00Z).",
    )

    @field_validator("scheduled_at")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError(
                "Timestamp must be timezone-aware (e.g. UTC ISO 8601 string '2026-10-10T15:30:00Z')."
            )
        # Normalize to UTC
        return v.astimezone(timezone.utc)


class ScheduleSlotResponse(BaseModel):
    """Serialized schedule slot data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    variant_id: int
    scheduled_at: datetime
    status: str
    idempotency_key: str
    processing_started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PublishHistoryItemResponse(BaseModel):
    """Audit log item representing a single publish execution attempt."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    schedule_slot_id: int
    variant_id: int
    platform: str
    attempt_number: int
    status: str
    external_post_id: Optional[str] = None
    error_message: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None


class PublishHistoryListResponse(BaseModel):
    """Paginated publish history listing."""

    items: List[PublishHistoryItemResponse]
    total: int
    limit: int
    offset: int


class SchedulerStatusResponse(BaseModel):
    """Operational observability metrics for the scheduler and slots."""

    pending_slots: int
    due_slots: int
    processing_slots: int
    completed_slots: int
    failed_slots: int
    reconciliation_required_slots: int
    current_time: datetime
