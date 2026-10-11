from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import VariantStatus
from app.models.variant import Variant


class VariantRepository:
    """Repository managing persistence and retrieval for content Variants."""

    @staticmethod
    def create(
        db: Session,
        post_id: int,
        platform: str,
        content: str,
        status: VariantStatus = VariantStatus.DRAFT,
        validation_errors: Optional[Dict[str, Any]] = None,
        rejection_reason: Optional[str] = None,
        commit: bool = True,
    ) -> Variant:
        """Create and persist a new Variant."""
        variant = Variant(
            post_id=post_id,
            platform=platform,
            content=content,
            status=status,
            validation_errors=validation_errors,
            rejection_reason=rejection_reason,
        )
        db.add(variant)
        if commit:
            db.commit()
            db.refresh(variant)
        else:
            db.flush()
        return variant

    @staticmethod
    def get_by_id(db: Session, variant_id: int) -> Optional[Variant]:
        """Retrieve a Variant by its ID."""
        return db.get(Variant, variant_id)

    @staticmethod
    def get_with_slots(db: Session, variant_id: int) -> Optional[Variant]:
        """Retrieve a Variant along with eagerly loaded schedule slots."""
        stmt = (
            select(Variant)
            .where(Variant.id == variant_id)
            .options(selectinload(Variant.schedule_slots))
        )
        return db.scalar(stmt)

    @staticmethod
    def list_by_post_id(db: Session, post_id: int) -> List[Variant]:
        """List all variants associated with a given Post ID."""
        stmt = (
            select(Variant)
            .where(Variant.post_id == post_id)
            .order_by(Variant.created_at.asc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def list_filtered(
        db: Session,
        status: Optional[VariantStatus] = None,
        platform: Optional[str] = None,
        post_id: Optional[int] = None,
    ) -> List[Variant]:
        """List variants filtered by status, platform, or post ID."""
        stmt = select(Variant)
        if status is not None:
            stmt = stmt.where(Variant.status == status)
        if platform is not None:
            stmt = stmt.where(Variant.platform == platform)
        if post_id is not None:
            stmt = stmt.where(Variant.post_id == post_id)
        stmt = stmt.order_by(Variant.created_at.desc())
        return list(db.scalars(stmt).all())

    @staticmethod
    def update_review(
        db: Session,
        variant_id: int,
        status: VariantStatus,
        content: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        validation_errors: Optional[Dict[str, Any]] = None,
    ) -> Optional[Variant]:
        """Update review state, content, rejection reason, and validation errors."""
        variant = db.get(Variant, variant_id)
        if variant:
            variant.status = status
            if content is not None:
                variant.content = content
            variant.rejection_reason = rejection_reason
            variant.validation_errors = validation_errors
            db.commit()
            db.refresh(variant)
        return variant

    @staticmethod
    def update_status(
        db: Session,
        variant_id: int,
        status: VariantStatus,
    ) -> Optional[Variant]:
        """Update the status of a Variant."""
        variant = db.get(Variant, variant_id)
        if variant:
            variant.status = status
            db.commit()
            db.refresh(variant)
        return variant

    @staticmethod
    def update_content(
        db: Session,
        variant_id: int,
        content: str,
        status: Optional[VariantStatus] = None,
        validation_errors: Optional[Dict[str, Any]] = None,
    ) -> Optional[Variant]:
        """Update content and optionally status/validation errors for a Variant."""
        variant = db.get(Variant, variant_id)
        if variant:
            variant.content = content
            if status is not None:
                variant.status = status
            if validation_errors is not None:
                variant.validation_errors = validation_errors
            db.commit()
            db.refresh(variant)
        return variant

    @staticmethod
    def delete(db: Session, variant_id: int) -> bool:
        """Delete a Variant by ID."""
        variant = db.get(Variant, variant_id)
        if variant:
            db.delete(variant)
            db.commit()
            return True
        return False
