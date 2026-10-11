from enum import Enum


class Platform(str, Enum):
    """Supported social media platforms."""

    TELEGRAM = "telegram"
    MOCK_X = "mock_x"
    MOCK_LINKEDIN = "mock_linkedin"


class VariantStatus(str, Enum):
    """Lifecycle status of a generated variant."""

    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class ScheduleSlotStatus(str, Enum):
    """Status of a scheduled publishing slot."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class PublishAttemptStatus(str, Enum):
    """Outcome status of an individual publish execution attempt."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILIATION_REQUIRED = "reconciliation_required"
