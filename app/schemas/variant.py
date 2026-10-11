from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class GenerateVariantsRequest(BaseModel):
    """Request payload for generating platform-specific variants."""

    platforms: Optional[List[str]] = Field(
        default=None,
        description="Target platform identifiers (e.g. ['mock_x', 'mock_linkedin', 'telegram']). Defaults to all.",
    )


class VariantUpdateContent(BaseModel):
    """Request payload for editing a variant's content."""

    content: str = Field(..., min_length=1, description="Updated social media content")

    @field_validator("content")
    @classmethod
    def validate_non_whitespace(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Content cannot be empty or purely whitespace.")
        return stripped


class RejectVariantRequest(BaseModel):
    """Optional payload when rejecting a variant."""

    reason: Optional[str] = Field(
        default=None,
        description="Optional reason or feedback for rejecting the variant.",
    )


class VariantResponse(BaseModel):
    """Schema for serialized variant data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int
    platform: str
    content: str
    status: str
    validation_errors: Optional[Dict[str, Any]] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ValidateVariantResponse(BaseModel):
    """Response schema from validating an existing variant."""

    variant_id: int
    platform: str
    is_valid: bool
    status: str
    character_count: int
    hashtag_count: int
    errors: List[str]
    warnings: List[str]


class PublishVariantResponse(BaseModel):
    """Response schema from a controlled publish execution."""

    success: bool
    variant_id: int
    platform: str
    slot_id: Optional[int] = None
    external_post_id: Optional[str] = None
    preview: Optional[str] = None
    error: Optional[str] = None
    attempt_id: Optional[int] = None
    published_at: Optional[datetime] = None
    is_replay: Optional[bool] = False
    is_uncertain: Optional[bool] = False
