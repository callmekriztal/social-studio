from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.enums import VariantStatus
from app.models.variant import Variant
from app.repositories.variant_repository import VariantRepository
from app.validators.validator import VariantValidator


class ReviewError(Exception):
    """Base exception for review workflow failures."""
    pass


class VariantNotFoundError(ReviewError):
    """Raised when the requested variant ID does not exist."""
    pass


class InvalidTransitionError(ReviewError):
    """Raised when an illegal state transition is attempted."""
    pass


class ValidationErrorException(ReviewError):
    """Raised when variant content fails platform constraint validation."""

    def __init__(self, message: str, errors: List[str]):
        super().__init__(message)
        self.errors = errors


class ReviewService:
    """Service orchestrating human review, editing, and state machine transitions."""

    @staticmethod
    def get_variant_or_404(db: Session, variant_id: int) -> Variant:
        """Fetch variant by ID or raise VariantNotFoundError."""
        variant = VariantRepository.get_by_id(db, variant_id)
        if not variant:
            raise VariantNotFoundError(f"Variant with ID {variant_id} does not exist.")
        return variant

    @classmethod
    def edit_variant(cls, db: Session, variant_id: int, new_content: str) -> Variant:
        """Edit variant text content with revalidation and automatic status adjustment.

        Rules:
        - Published variants cannot be edited.
        - Rejected variants must be revised first.
        - Content is strictly revalidated against the platform profile.
        - If invalid: raises ValidationErrorException (422), variant remains unchanged.
        - If approved: automatically reset to draft.
        - If draft: stays draft.
        """
        variant = cls.get_variant_or_404(db, variant_id)

        if variant.status == VariantStatus.PUBLISHED:
            raise InvalidTransitionError("Published variants cannot be edited.")

        if variant.status == VariantStatus.REJECTED:
            raise InvalidTransitionError(
                "Rejected variants cannot be edited directly. Return to draft first via /revise."
            )

        # Revalidate content against platform profile
        validation = VariantValidator.validate(variant.platform, new_content)
        if not validation.is_valid:
            raise ValidationErrorException(
                f"Edited content fails {variant.platform} constraints.",
                errors=validation.errors,
            )

        # Valid edits reset approved variants back to draft
        target_status = VariantStatus.DRAFT

        updated = VariantRepository.update_review(
            db=db,
            variant_id=variant.id,
            status=target_status,
            content=new_content,
            rejection_reason=None,
            validation_errors=None,
        )
        return updated  # type: ignore[return-value]

    @classmethod
    def approve_variant(cls, db: Session, variant_id: int) -> Variant:
        """Transition a variant to APPROVED status after verifying constraints.

        Rules:
        - Only draft variants may be approved.
        - Content must pass validation immediately prior to approval.
        - Published or rejected variants cannot be approved directly.
        """
        variant = cls.get_variant_or_404(db, variant_id)

        if variant.status == VariantStatus.PUBLISHED:
            raise InvalidTransitionError("Cannot approve a variant that is already published.")

        if variant.status == VariantStatus.REJECTED:
            raise InvalidTransitionError(
                "Rejected variants cannot be approved directly. Revise to draft first."
            )

        if variant.status == VariantStatus.APPROVED:
            return variant

        # Revalidate immediately before approving
        validation = VariantValidator.validate(variant.platform, variant.content)
        if not validation.is_valid:
            raise ValidationErrorException(
                f"Cannot approve variant: content violates {variant.platform} constraints.",
                errors=validation.errors,
            )

        updated = VariantRepository.update_review(
            db=db,
            variant_id=variant.id,
            status=VariantStatus.APPROVED,
            rejection_reason=None,
            validation_errors=None,
        )
        return updated  # type: ignore[return-value]

    @classmethod
    def reject_variant(
        cls,
        db: Session,
        variant_id: int,
        reason: Optional[str] = None,
    ) -> Variant:
        """Transition a draft or approved variant to REJECTED with an optional reason.

        Rules:
        - Published variants cannot be rejected.
        """
        variant = cls.get_variant_or_404(db, variant_id)

        if variant.status == VariantStatus.PUBLISHED:
            raise InvalidTransitionError("Published variants cannot be rejected.")

        updated = VariantRepository.update_review(
            db=db,
            variant_id=variant.id,
            status=VariantStatus.REJECTED,
            rejection_reason=reason,
        )
        return updated  # type: ignore[return-value]

    @classmethod
    def revise_variant(cls, db: Session, variant_id: int) -> Variant:
        """Return a REJECTED variant to DRAFT for revision.

        Rules:
        - Only rejected variants can be revised.
        - Does NOT automatically approve the variant.
        """
        variant = cls.get_variant_or_404(db, variant_id)

        if variant.status != VariantStatus.REJECTED:
            raise InvalidTransitionError(
                f"Only rejected variants can be returned to draft (current status: {variant.status.value})."
            )

        # Revalidate current content
        validation = VariantValidator.validate(variant.platform, variant.content)
        validation_errors = None if validation.is_valid else validation.to_dict()

        updated = VariantRepository.update_review(
            db=db,
            variant_id=variant.id,
            status=VariantStatus.DRAFT,
            rejection_reason=None,
            validation_errors=validation_errors,
        )
        return updated  # type: ignore[return-value]

    @staticmethod
    def list_variants(
        db: Session,
        status: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> List[Variant]:
        """List variants filtered by status or platform."""
        status_enum = None
        if status:
            try:
                status_enum = VariantStatus(status)
            except ValueError:
                status_enum = None

        return VariantRepository.list_filtered(
            db=db,
            status=status_enum,
            platform=platform,
        )
