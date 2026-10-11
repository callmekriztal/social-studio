from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.mock_linkedin import MockLinkedInPublisher
from app.adapters.mock_x import MockXPublisher
from app.adapters.telegram import TelegramPublisher
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
from app.services.scheduling_guard import SchedulingEligibilityError


@pytest.fixture
def sample_post(db_session: Session) -> Post:
    """Fixture providing a persisted Post."""
    return PostRepository.create(
        db=db_session,
        title="Idempotency Deep Dive",
        source_content="Comprehensive testing of idempotent publishing and concurrency protection.",
    )


@pytest.fixture
def approved_x_variant(db_session: Session, sample_post: Post) -> Variant:
    """Fixture providing an approved valid Mock X variant."""
    return VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Deep dive into idempotency in distributed backends! Thoughts? #Tech #Architecture",
        status=VariantStatus.APPROVED,
    )


@pytest.fixture
def approved_telegram_variant(db_session: Session, sample_post: Post) -> Variant:
    """Fixture providing an approved valid Telegram variant."""
    return VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="telegram",
        content="📢 Idempotency update.\n\nEnsuring safe execution across networks.\n\n#Tech",
        status=VariantStatus.APPROVED,
    )


# 1. Deterministic Mock-Publishing Test (Prompt 6 Section 6 Scenario)
def test_1_deterministic_mock_publishing_scenario(
    client: TestClient, db_session: Session, sample_post: Post, approved_x_variant: Variant
):
    now = datetime(2026, 10, 10, 16, 0, 0, tzinfo=timezone.utc)
    idempotency_key = derive_slot_idempotency_key(approved_x_variant.id, now)

    # 4. Create a schedule slot
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_x_variant.id,
        scheduled_at=now,
        idempotency_key=idempotency_key,
        status=ScheduleSlotStatus.PENDING,
    )

    # 5. Publish the slot once via endpoint
    res1 = client.post(f"/api/schedule-slots/{slot.id}/publish")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["success"] is True
    assert data1["is_replay"] is False
    external_post_id_1 = data1["external_post_id"]
    attempt_id_1 = data1["attempt_id"]

    # 6. Call the publishing service/endpoint again for the same slot
    res2 = client.post(f"/api/schedule-slots/{slot.id}/publish")
    assert res2.status_code == 200
    data2 = res2.json()

    # 7. Verify that only one logical mock post exists
    saved_posts = MockPostRepository.list_by_platform(db_session, "mock_x")
    matching = [p for p in saved_posts if p.mock_post_id == external_post_id_1]
    assert len(matching) == 1

    # 8. Verify that the second call returns the existing successful result
    assert data2["success"] is True
    assert data2["external_post_id"] == external_post_id_1
    assert data2["attempt_id"] == attempt_id_1
    assert data2["is_replay"] is True

    # 9. Verify that no second successful publication attempt was created
    attempts = PublishAttemptRepository.list_by_slot_id(db_session, slot.id)
    assert len(attempts) == 1


# 2. Missing slot returns 404
def test_2_missing_slot_returns_404(client: TestClient):
    res = client.post("/api/schedule-slots/999999/publish")
    assert res.status_code == 404
    assert "does not exist" in res.json()["detail"]


# 3. Unapproved variants cannot publish
def test_3_unapproved_variants_cannot_publish(
    client: TestClient, db_session: Session, sample_post: Post
):
    # Draft variant in slot
    draft_var = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Draft variant that should never publish? #Tech",
        status=VariantStatus.DRAFT,
    )
    now = datetime(2026, 10, 10, 16, 5, 0, tzinfo=timezone.utc)
    slot_draft = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=draft_var.id,
        scheduled_at=now,
        idempotency_key=derive_slot_idempotency_key(draft_var.id, now),
        status=ScheduleSlotStatus.PENDING,
    )
    res_draft = client.post(f"/api/schedule-slots/{slot_draft.id}/publish")
    assert res_draft.status_code == 400
    assert "Variant is in DRAFT status" in res_draft.json()["detail"]

    # Rejected variant in slot
    rejected_var = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Rejected variant? #Tech",
        status=VariantStatus.REJECTED,
        rejection_reason="Editorial review failed",
    )
    now2 = datetime(2026, 10, 10, 16, 10, 0, tzinfo=timezone.utc)
    slot_rejected = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=rejected_var.id,
        scheduled_at=now2,
        idempotency_key=derive_slot_idempotency_key(rejected_var.id, now2),
        status=ScheduleSlotStatus.PENDING,
    )
    res_rejected = client.post(f"/api/schedule-slots/{slot_rejected.id}/publish")
    assert res_rejected.status_code == 400
    assert "Variant is REJECTED" in res_rejected.json()["detail"]


# 4. Invalid variants cannot publish
def test_4_invalid_variants_cannot_publish(
    client: TestClient, db_session: Session, sample_post: Post
):
    invalid_var = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Invalid content without conversational tone markers and shouting SHOUTING SHOUTING",
        status=VariantStatus.APPROVED,
    )
    now = datetime(2026, 10, 10, 16, 15, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=invalid_var.id,
        scheduled_at=now,
        idempotency_key=derive_slot_idempotency_key(invalid_var.id, now),
        status=ScheduleSlotStatus.PENDING,
    )
    res = client.post(f"/api/schedule-slots/{slot.id}/publish")
    assert res.status_code == 400
    assert "violates platform constraints" in res.json()["detail"]


# 5. Completed slots cannot publish again (returns existing success without publisher call)
def test_5_completed_slots_return_existing_success(
    client: TestClient, db_session: Session, approved_x_variant: Variant
):
    now = datetime(2026, 10, 10, 16, 20, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_x_variant.id,
        scheduled_at=now,
        idempotency_key=derive_slot_idempotency_key(approved_x_variant.id, now),
        status=ScheduleSlotStatus.PENDING,
    )

    # First execution succeeds
    res1 = client.post(f"/api/schedule-slots/{slot.id}/publish")
    assert res1.status_code == 200
    assert res1.json()["is_replay"] is False

    # Second execution should return existing result without calling publisher adapter
    with patch("app.adapters.mock_x.MockXPublisher.publish") as mock_publish:
        res2 = client.post(f"/api/schedule-slots/{slot.id}/publish")
        assert res2.status_code == 200
        assert res2.json()["is_replay"] is True
        assert not mock_publish.called


# 6. Duplicate idempotency keys are rejected at database level
def test_6_duplicate_idempotency_keys_rejected_at_db_level(
    db_session: Session, approved_x_variant: Variant
):
    now = datetime(2026, 10, 10, 16, 25, 0, tzinfo=timezone.utc)
    key = "fixed-idempotency-key-12345"

    # ScheduleSlot uniqueness
    ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_x_variant.id,
        scheduled_at=now,
        idempotency_key=key,
        status=ScheduleSlotStatus.PENDING,
    )
    with pytest.raises(IntegrityError):
        ScheduleSlotRepository.create(
            db=db_session,
            variant_id=approved_x_variant.id,
            scheduled_at=datetime(2026, 10, 10, 17, 0, 0, tzinfo=timezone.utc),
            idempotency_key=key,
            status=ScheduleSlotStatus.PENDING,
        )
    db_session.rollback()

    # MockPlatformPost uniqueness
    MockPostRepository.create(
        db=db_session,
        platform="mock_x",
        content="Post 1 #Tech",
        mock_post_id="id_1",
        idempotency_key=key,
    )
    with pytest.raises(IntegrityError):
        MockPostRepository.create(
            db=db_session,
            platform="mock_x",
            content="Post 2 #Tech",
            mock_post_id="id_2",
            idempotency_key=key,
        )
    db_session.rollback()


# 7. Concurrent workers cannot both claim the same slot
def test_7_concurrent_workers_claim_protection(
    client: TestClient, db_session: Session, approved_x_variant: Variant
):
    now = datetime(2026, 10, 10, 16, 30, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_x_variant.id,
        scheduled_at=now,
        idempotency_key=derive_slot_idempotency_key(approved_x_variant.id, now),
        status=ScheduleSlotStatus.PENDING,
    )

    # Worker 1 claims the slot
    claimed = ScheduleSlotRepository.claim_slot_for_processing(db_session, slot.id)
    assert claimed is True

    # Worker 2 attempts to claim the slot: must return False
    claimed_again = ScheduleSlotRepository.claim_slot_for_processing(db_session, slot.id)
    assert claimed_again is False

    # Calling publish endpoint while slot is in PROCESSING status yields HTTP 409 Conflict
    res = client.post(f"/api/schedule-slots/{slot.id}/publish")
    assert res.status_code == 409
    assert "currently being processed" in res.json()["detail"]


# 8. Confirmed failures are recorded cleanly
def test_8_confirmed_failures_recorded(
    client: TestClient, db_session: Session, approved_telegram_variant: Variant
):
    now = datetime(2026, 10, 10, 16, 35, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_telegram_variant.id,
        scheduled_at=now,
        idempotency_key=derive_slot_idempotency_key(approved_telegram_variant.id, now),
        status=ScheduleSlotStatus.PENDING,
    )

    # Simulate confirmed 403 Forbidden error from Telegram API
    with patch.object(settings, "TELEGRAM_BOT_TOKEN", "mock_token"), \
         patch.object(settings, "TELEGRAM_CHAT_ID", "-100123"), \
         patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {
            "ok": False,
            "error_code": 403,
            "description": "Forbidden: bot was blocked by the user",
        }
        mock_client.post.return_value = mock_resp

        res = client.post(f"/api/schedule-slots/{slot.id}/publish")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["is_uncertain"] is False
        assert "403" in data["error"]

        # Check DB state
        db_session.refresh(slot)
        assert slot.status == ScheduleSlotStatus.FAILED
        assert "403" in slot.last_error

        attempt = PublishAttemptRepository.get_by_id(db_session, data["attempt_id"])
        assert attempt.status == PublishAttemptStatus.FAILED
        assert "403" in attempt.error_message


# 9. Uncertain outcomes are NOT blindly retried
def test_9_uncertain_outcomes_not_blindly_retried(
    client: TestClient, db_session: Session, approved_telegram_variant: Variant
):
    now = datetime(2026, 10, 10, 16, 40, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_telegram_variant.id,
        scheduled_at=now,
        idempotency_key=derive_slot_idempotency_key(approved_telegram_variant.id, now),
        status=ScheduleSlotStatus.PENDING,
    )

    # Simulate network timeout mid-flight
    with patch.object(settings, "TELEGRAM_BOT_TOKEN", "mock_token"), \
         patch.object(settings, "TELEGRAM_CHAT_ID", "-100123"), \
         patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("Read timeout after request dispatched")

        res = client.post(f"/api/schedule-slots/{slot.id}/publish")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["is_uncertain"] is True
        assert "timed out" in data["error"]

        # Verify slot is marked RECONCILIATION_REQUIRED
        db_session.refresh(slot)
        assert slot.status == ScheduleSlotStatus.RECONCILIATION_REQUIRED

        # Verify attempt is marked UNCERTAIN
        attempt = PublishAttemptRepository.get_by_id(db_session, data["attempt_id"])
        assert attempt.status == PublishAttemptStatus.UNCERTAIN

        # Subsequent call must be REJECTED with 409 to prevent duplicate send
        res_retry = client.post(f"/api/schedule-slots/{slot.id}/publish")
        assert res_retry.status_code == 409
        assert "requires manual reconciliation" in res_retry.json()["detail"]


# 10. Worker crash simulation blocks unsafe automatic resend
def test_10_worker_crash_simulation_blocks_unsafe_resend(
    client: TestClient, db_session: Session, approved_x_variant: Variant
):
    now = datetime(2026, 10, 10, 16, 45, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_x_variant.id,
        scheduled_at=now,
        idempotency_key=derive_slot_idempotency_key(approved_x_variant.id, now),
        status=ScheduleSlotStatus.PROCESSING,
    )
    # Simulate attempt recorded as STARTED prior to crash
    PublishAttemptRepository.create(
        db=db_session,
        schedule_slot_id=slot.id,
        attempt_number=1,
        status=PublishAttemptStatus.STARTED,
    )

    # Next request arrives while slot is still in PROCESSING status
    res = client.post(f"/api/schedule-slots/{slot.id}/publish")
    assert res.status_code == 409
    assert "currently being processed" in res.json()["detail"]


# 11. Mock adapters remain idempotent on direct calls
def test_11_mock_adapters_direct_idempotency(db_session: Session):
    key = "direct-mock-idempotency-key-xyz"
    content = "Direct test of MockX idempotency! Thoughts? #Tech"

    publisher = MockXPublisher()

    # Call 1
    res1 = publisher.publish(content=content, idempotency_key=key, db=db_session)
    assert res1.success is True

    # Call 2 with identical key
    res2 = publisher.publish(content=content, idempotency_key=key, db=db_session)
    assert res2.success is True
    assert res2.external_post_id == res1.external_post_id
    assert res2.raw_response.get("is_replay") is True

    # Confirm only 1 post was saved in DB
    posts = MockPostRepository.list_by_platform(db_session, "mock_x")
    matching = [p for p in posts if p.idempotency_key == key]
    assert len(matching) == 1


# 12. Telegram errors and timeouts do not create false success records
def test_12_telegram_errors_do_not_create_false_success(
    client: TestClient, db_session: Session, approved_telegram_variant: Variant
):
    now = datetime(2026, 10, 10, 16, 50, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_telegram_variant.id,
        scheduled_at=now,
        idempotency_key=derive_slot_idempotency_key(approved_telegram_variant.id, now),
        status=ScheduleSlotStatus.PENDING,
    )

    with patch.object(settings, "TELEGRAM_BOT_TOKEN", "mock_token"), \
         patch.object(settings, "TELEGRAM_CHAT_ID", "-100123"), \
         patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: chat not found",
        }
        mock_client.post.return_value = mock_resp

        res = client.post(f"/api/schedule-slots/{slot.id}/publish")
        assert res.status_code == 200
        assert res.json()["success"] is False

        # Ensure no success attempts exist for this slot
        succeeded = PublishAttemptRepository.get_successful_attempt(db_session, slot.id)
        assert succeeded is None

        # Ensure variant is not marked PUBLISHED
        db_session.refresh(approved_telegram_variant)
        assert approved_telegram_variant.status == VariantStatus.APPROVED
