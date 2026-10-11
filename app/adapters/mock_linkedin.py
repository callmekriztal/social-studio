import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session

from app.adapters.publisher_base import PublishResult, SocialPublisher
from app.database import SessionLocal
from app.repositories.mock_post_repository import MockPostRepository


class MockLinkedInPublisher(SocialPublisher):
    """Local mock publisher simulating publishing to LinkedIn."""

    @property
    def platform_name(self) -> str:
        return "mock_linkedin"

    def publish(
        self,
        content: str,
        **kwargs: Any,
    ) -> PublishResult:
        """Simulate publishing a LinkedIn post and save to local mock database record.

        Does not contact any external API.
        """
        now = datetime.now(timezone.utc)
        mock_id = f"urn:li:share:mock_{uuid.uuid4().hex[:12]}"
        idempotency_key = kwargs.get("idempotency_key")
        db: Optional[Session] = kwargs.get("db")

        preview = (
            f"=== [Mock LinkedIn Feed Preview] ===\n"
            f"{content}\n"
            f"=== (Simulated share URN: {mock_id}) ==="
        )

        should_close_db = False
        if db is None:
            db = SessionLocal()
            should_close_db = True

        try:
            if idempotency_key:
                existing = MockPostRepository.get_by_idempotency_key(db, idempotency_key)
                if existing:
                    replay_preview = (
                        f"=== [Mock LinkedIn Feed Preview] ===\n"
                        f"{existing.content}\n"
                        f"=== (Simulated share URN: {existing.mock_post_id}) ==="
                    )
                    return PublishResult(
                        success=True,
                        platform="mock_linkedin",
                        external_post_id=existing.mock_post_id,
                        published_at=existing.created_at,
                        preview=replay_preview,
                        error=None,
                        idempotency_key=idempotency_key,
                        raw_response={
                            "simulation": True,
                            "platform": "mock_linkedin",
                            "mock_post_id": existing.mock_post_id,
                            "is_replay": True,
                        },
                    )

            MockPostRepository.create(
                db=db,
                platform="mock_linkedin",
                content=content,
                mock_post_id=mock_id,
                idempotency_key=idempotency_key,
            )
        finally:
            if should_close_db:
                db.close()

        return PublishResult(
            success=True,
            platform="mock_linkedin",
            external_post_id=mock_id,
            published_at=now,
            preview=preview,
            error=None,
            idempotency_key=idempotency_key,
            raw_response={
                "simulation": True,
                "platform": "mock_linkedin",
                "mock_post_id": mock_id,
                "disclaimer": "Simulated local publication. No real request was sent to LinkedIn.",
            },
        )
