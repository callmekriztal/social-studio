from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.post import Post


class PostRepository:
    """Repository managing persistence and retrieval for Posts."""

    @staticmethod
    def create(
        db: Session,
        title: str,
        source_content: str,
        source_url: Optional[str] = None,
    ) -> Post:
        """Create and persist a new Post."""
        post = Post(
            title=title,
            source_content=source_content,
            source_url=source_url,
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        return post

    @staticmethod
    def get_by_id(db: Session, post_id: int) -> Optional[Post]:
        """Retrieve a Post by its ID."""
        return db.get(Post, post_id)

    @staticmethod
    def get_with_variants(db: Session, post_id: int) -> Optional[Post]:
        """Retrieve a Post along with eagerly loaded variants."""
        stmt = (
            select(Post)
            .where(Post.id == post_id)
            .options(selectinload(Post.variants))
        )
        return db.scalar(stmt)

    @staticmethod
    def list_all(db: Session, skip: int = 0, limit: int = 100) -> List[Post]:
        """List posts ordered by creation time descending."""
        stmt = select(Post).order_by(Post.created_at.desc()).offset(skip).limit(limit)
        return list(db.scalars(stmt).all())

    @staticmethod
    def delete(db: Session, post_id: int) -> bool:
        """Delete a Post by ID. Cascades to its variants."""
        post = db.get(Post, post_id)
        if post:
            db.delete(post)
            db.commit()
            return True
        return False
