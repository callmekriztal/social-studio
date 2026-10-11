from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MockPlatformPost(Base):
    """Dedicated persistence entity for posts published via mock adapters."""

    __tablename__ = "mock_platform_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    mock_post_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<MockPlatformPost id={self.id} platform={self.platform!r} mock_post_id={self.mock_post_id!r} key={self.idempotency_key!r}>"
