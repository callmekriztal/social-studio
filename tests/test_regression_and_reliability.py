from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.registry import PublisherRegistry
from app.config import settings
from app.models.enums import (
    PublishAttemptStatus,
    ScheduleSlotStatus,
    VariantStatus,
)
from app.models.mock_post import MockPlatformPost
from app.models.post import Post
from app.models.publish_attempt import PublishAttempt
from app.models.schedule_slot import ScheduleSlot
from app.models.variant import Variant
from app.repositories.mock_post_repository import MockPostRepository
from app.repositories.post_repository import PostRepository
from app.repositories.publish_attempt_repository import PublishAttemptRepository
from app.repositories.schedule_slot_repository import ScheduleSlotRepository
from app.repositories.variant_repository import VariantRepository
from app.services.publishing_service import (
    PublishOutcome,
    PublishingService,
    SlotLockedError,
    SlotNotFoundError,
    SlotReconciliationError,
    derive_slot_idempotency_key,
)
from app.services.scheduling_guard import (
    SchedulingEligibilityError,
    SchedulingEligibilityGuard,
)


@pytest.fixture(autouse=True)
def clean_database(db_session: Session):
    """Ensure a clean database state for each test in this module."""
    db_session.query(PublishAttempt).delete()
    db_session.query(ScheduleSlot).delete()
    db_session.query(MockPlatformPost).delete()
    db_session.query(Variant).delete()
    db_session.query(Post).delete()
    db_session.commit()
    yield


@pytest.fixture
def sample_post(db_session: Session) -> Post:
    """Fixture providing a persisted Post."""
    return PostRepository.create(
        db=db_session,
        title="Reliability Test Post",
        source_content="Content for regression and reliability verification.",
    )


# 1. Missing Records (404 Not Found)
def test_missing_records_return_404(client: TestClient):
    # Non-existent post
    res_post = client.get("/api/posts/999999")
    assert res_post.status_code == 404

    # Non-existent variant
    res_var = client.get("/api/variants/999999")
    assert res_var.status_code == 404

    # Non-existent variant approve
    res_app = client.post("/api/variants/999999/approve")
    assert res_app.status_code == 404

    # Non-existent variant schedule
    res_sched = client.post(
        "/api/variants/999999/schedule",
        json={"scheduled_at": "2026-10-10T15:00:00Z"},
    )
    assert res_sched.status_code == 404

    # Non-existent slot publish
    res_slot = client.post("/api/schedule-slots/999999/publish")
    assert res_slot.status_code == 404

    # Non-existent publish history detail
    res_hist = client.get("/api/publish-history/999999")
    assert res_hist.status_code == 404


# 2. Malformed Requests (422 Unprocessable Content)
def test_malformed_requests_rejected(client: TestClient, sample_post: Post):
    # Empty post title
    res1 = client.post("/api/posts", json={"title": "   ", "content": "Valid body text"})
    assert res1.status_code == 422

    # Empty post content
    res2 = client.post("/api/posts", json={"title": "Valid title", "content": "  "})
    assert res2.status_code == 422

    # Missing scheduled_at field
    res3 = client.post(f"/api/variants/1/schedule", json={})
    assert res3.status_code == 422

    # Naive timestamp
    res4 = client.post(
        f"/api/variants/1/schedule",
        json={"scheduled_at": "2026-10-10T12:00:00"},
    )
    assert res4.status_code == 422


# 3. Invalid State Transitions
def test_invalid_state_transitions_blocked(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    # Create a published variant
    v_pub = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Published variant. Thoughts? #Tech",
        status=VariantStatus.PUBLISHED,
    )

    # Published variant cannot be edited
    res_edit = client.patch(
        f"/api/variants/{v_pub.id}",
        json={"content": "New content. Thoughts? #Tech"},
    )
    assert res_edit.status_code == 400
    assert "published variants cannot be edited" in res_edit.json()["detail"].lower()

    # Published variant cannot be approved
    res_app = client.post(f"/api/variants/{v_pub.id}/approve")
    assert res_app.status_code == 400
    assert "already published" in res_app.json()["detail"].lower()

    # Published variant cannot be rejected
    res_rej = client.post(f"/api/variants/{v_pub.id}/reject")
    assert res_rej.status_code == 400

    # Rejected variant cannot be approved directly without revise
    v_rej = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Rejected content. Thoughts? #Tech",
        status=VariantStatus.REJECTED,
    )
    res_app_rej = client.post(f"/api/variants/{v_rej.id}/approve")
    assert res_app_rej.status_code == 400
    assert "revise to draft first" in res_app_rej.json()["detail"].lower()


# 4. Database-Level Constraint Violations and Rollback
def test_database_level_constraints_and_rollback(
    sample_post: Post,
    db_session: Session,
):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Testing DB constraints! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    t = datetime(2026, 10, 10, 15, 0, 0, tzinfo=timezone.utc)
    key = derive_slot_idempotency_key(v.id, t)

    s1 = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=v.id,
        scheduled_at=t,
        idempotency_key=key,
    )

    # 1. Duplicate idempotency key rejected by DB constraint
    duplicate_slot = ScheduleSlot(
        variant_id=v.id,
        scheduled_at=t + timedelta(hours=1),
        idempotency_key=key,  # same key!
    )
    db_session.add(duplicate_slot)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 2. Duplicate (variant_id, scheduled_at) rejected by DB constraint
    duplicate_time_slot = ScheduleSlot(
        variant_id=v.id,
        scheduled_at=t,  # same timestamp for same variant!
        idempotency_key="distinct-key-12345",
    )
    db_session.add(duplicate_time_slot)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# 5. Concurrent Slot Claims Collision Protection
def test_concurrent_slot_claims_protection(sample_post: Post, db_session: Session):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Claim collision test! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    t = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=v.id,
        scheduled_at=t,
        idempotency_key=derive_slot_idempotency_key(v.id, t),
    )

    # Worker 1 claims slot successfully
    claimed_1 = ScheduleSlotRepository.claim_slot_for_processing(db_session, slot.id)
    assert claimed_1 is True

    # Worker 2 attempts to claim same slot concurrently -> fails atomically
    claimed_2 = ScheduleSlotRepository.claim_slot_for_processing(db_session, slot.id)
    assert claimed_2 is False

    # Calling publish_slot while claimed by another worker raises SlotLockedError
    with pytest.raises(SlotLockedError):
        PublishingService.publish_slot(db_session, slot.id)


# 6. Safe Retry Behavior After Confirmed Failure
def test_safe_retry_behavior_after_confirmed_failure(
    sample_post: Post,
    db_session: Session,
):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Failure and recovery test! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    t = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=v.id,
        scheduled_at=t,
        idempotency_key=derive_slot_idempotency_key(v.id, t),
    )

    # Attempt 1 fails with simulated confirmed adapter failure
    with patch("app.adapters.mock_x.MockXPublisher.publish") as mock_pub:
        from app.adapters.publisher_base import PublishResult
        mock_pub.return_value = PublishResult(
            success=False,
            platform="mock_x",
            error="Simulated temporary API rejection",
            is_uncertain=False,  # confirmed failure!
        )
        outcome1 = PublishingService.publish_slot(db_session, slot.id)
        assert outcome1.success is False
        assert outcome1.is_uncertain is False

    db_session.refresh(slot)
    assert slot.status == ScheduleSlotStatus.FAILED
    assert slot.last_error == "Simulated temporary API rejection"

    # Confirmed failed slot CAN be safely retried
    outcome2 = PublishingService.publish_slot(db_session, slot.id)
    assert outcome2.success is True
    assert outcome2.slot_id == slot.id

    db_session.refresh(slot)
    assert slot.status == ScheduleSlotStatus.COMPLETED

    # Verify 2 attempts logged (attempt 1 failed, attempt 2 succeeded)
    attempts = PublishAttemptRepository.list_by_slot_id(db_session, slot.id)
    assert len(attempts) == 2
    assert attempts[0].attempt_number == 1
    assert attempts[0].status == PublishAttemptStatus.FAILED
    assert attempts[1].attempt_number == 2
    assert attempts[1].status == PublishAttemptStatus.SUCCEEDED


# 7. Recovery After Interrupted Operation (Lockout on Uncertainty)
def test_recovery_blocks_blind_retry_on_uncertainty(
    sample_post: Post,
    db_session: Session,
):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Uncertainty lockout test! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    t = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=v.id,
        scheduled_at=t,
        idempotency_key=derive_slot_idempotency_key(v.id, t),
    )

    # Simulate crash/uncertainty during external call
    with patch("app.adapters.mock_x.MockXPublisher.publish", side_effect=RuntimeError("Process terminated abruptly")):
        with pytest.raises(RuntimeError):
            PublishingService.publish_slot(db_session, slot.id)

    db_session.refresh(slot)
    assert slot.status == ScheduleSlotStatus.RECONCILIATION_REQUIRED

    # Subsequent automatic attempt is rejected to prevent duplicate publishes
    with pytest.raises(SlotReconciliationError):
        PublishingService.publish_slot(db_session, slot.id)


# 8. Publish-History Accuracy, Filtering, and Pagination
def test_publish_history_accuracy_and_pagination(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    # Create 3 variants and publish them
    v1 = VariantRepository.create(
        db=db_session, post_id=sample_post.id, platform="mock_x",
        content="Post 1. Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    v2 = VariantRepository.create(
        db=db_session, post_id=sample_post.id, platform="mock_linkedin",
        content="Post 2. What are your thoughts on this? #Leadership #Engineering", status=VariantStatus.APPROVED,
    )

    PublishingService.publish_variant(db_session, v1.id)
    PublishingService.publish_variant(db_session, v2.id)

    # Query with limit 1, offset 0
    res_p1 = client.get("/api/publish-history?limit=1&offset=0")
    assert res_p1.status_code == 200
    d1 = res_p1.json()
    assert d1["total"] == 2
    assert len(d1["items"]) == 1

    # Query with limit 1, offset 1
    res_p2 = client.get("/api/publish-history?limit=1&offset=1")
    assert res_p2.status_code == 200
    d2 = res_p2.json()
    assert len(d2["items"]) == 1
    assert d2["items"][0]["id"] != d1["items"][0]["id"]

    # Filter by platform mock_x
    res_fx = client.get("/api/publish-history?platform=mock_x")
    assert res_fx.status_code == 200
    assert all(i["platform"] == "mock_x" for i in res_fx.json()["items"])


# 9. Missing Telegram Credentials When Mock Adapter Selected
def test_missing_telegram_credentials_does_not_break_mock_adapters(
    sample_post: Post,
    db_session: Session,
):
    # Telegram credentials completely unset
    with patch.object(settings, "TELEGRAM_BOT_TOKEN", None), \
         patch.object(settings, "TELEGRAM_CHAT_ID", None):
        v = VariantRepository.create(
            db=db_session, post_id=sample_post.id, platform="mock_x",
            content="Mock adapter operates without Telegram creds! Thoughts? #Tech",
            status=VariantStatus.APPROVED,
        )
        outcome = PublishingService.publish_variant(db_session, v.id)
        assert outcome.success is True
        assert outcome.platform == "mock_x"


# 10. SSRF Protection for URL Ingestion
def test_ssrf_protection_for_url_ingestion(client: TestClient):
    # Loopback
    res_loopback = client.post("/api/posts/from-url", json={"url": "http://127.0.0.1:8000/internal"})
    assert res_loopback.status_code == 400
    assert "security violation" in res_loopback.json()["detail"].lower()

    # RFC 1918 Private IP
    res_private = client.post("/api/posts/from-url", json={"url": "http://192.168.1.1/secret"})
    assert res_private.status_code == 400

    # Cloud metadata endpoint
    res_meta = client.post("/api/posts/from-url", json={"url": "http://169.254.169.254/latest/meta-data"})
    assert res_meta.status_code == 400

    # Non-HTTP schema (rejected by Pydantic HttpUrl validation as 422 or SSRF check as 400)
    res_file = client.post("/api/posts/from-url", json={"url": "file:///etc/passwd"})
    assert res_file.status_code in [400, 422]
