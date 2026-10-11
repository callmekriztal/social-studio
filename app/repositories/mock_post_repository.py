from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mock_post import MockPlatformPost


class MockPostRepository:
    """Repository for persisting and querying mock published posts."""

    @staticmethod
    def create(
        db: Session,
        platform: str,
        content: str,
        mock_post_id: str,
        idempotency_key: Optional[str] = None,
    ) -> MockPlatformPost:
        """Create and persist a new mock platform post."""
        post = MockPlatformPost(
            platform=platform,
            content=content,
            mock_post_id=mock_post_id,
            idempotency_key=idempotency_key,
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        return post

    @staticmethod
    def get_by_mock_id(
        db: Session,
        mock_post_id: str,
    ) -> Optional[MockPlatformPost]:
        """Retrieve a mock post by its generated mock post ID."""
        stmt = select(MockPlatformPost).where(MockPlatformPost.mock_post_id == mock_post_id)
        return db.scalar(stmt)

    @staticmethod
    def get_by_idempotency_key(
        db: Session,
        idempotency_key: str,
    ) -> Optional[MockPlatformPost]:
        """Retrieve a mock post by its idempotency key."""
        stmt = select(MockPlatformPost).where(
            MockPlatformPost.idempotency_key == idempotency_key
        )
        return db.scalar(stmt)

    @staticmethod
    def list_by_platform(
        db: Session,
        platform: str,
    ) -> List[MockPlatformPost]:
        """List all mock posts for a specific platform."""
        stmt = (
            select(MockPlatformPost)
            .where(MockPlatformPost.platform == platform)
            .order_by(MockPlatformPost.created_at.desc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def list_all(db: Session) -> List[MockPlatformPost]:
        """List all mock posts."""
        stmt = select(MockPlatformPost).order_by(MockPlatformPost.created_at.desc())
        return list(db.scalars(stmt).all())
