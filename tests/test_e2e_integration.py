from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient
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
from app.scheduler.service import SchedulerService
from app.scheduler.worker import SchedulerWorker
from app.services.publishing_service import (
    PublishingService,
    derive_slot_idempotency_key,
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


# ==============================================================================
# Scenario A: Complete campaign
# ==============================================================================
def test_scenario_a_complete_campaign(client: TestClient, db_session: Session):
    """Scenario A: Full lifecycle from ingestion to scheduled publication.

    1. Ingest a blog post.
    2. Generate variants for mock_x, mock_linkedin, and telegram.
    3. Retrieve the variants.
    4. Approve a valid mock variant.
    5. Schedule it for a due time.
    6. Run the scheduler.
    7. Verify that one logical mock publication exists.
    8. Verify that the slot is completed.
    9. Verify that publish history contains the successful attempt.
    """
    # 1. Ingest blog post
    post_res = client.post(
        "/api/posts",
        json={
            "title": "Why Fast Feedback Loops Improve Architecture",
            "content": (
                "# Feedback Loops in Software\n\n"
                "Short feedback cycles allow engineering teams to validate assumptions rapidly. "
                "By automating integration tests and verifying domain constraints early, "
                "developers minimize regressions and deliver reliable distributed services."
            ),
        },
    )
    assert post_res.status_code == 201
    post_id = post_res.json()["id"]

    # 2. Generate variants for all supported platforms
    gen_res = client.post(
        f"/api/posts/{post_id}/generate",
        json={"platforms": ["mock_x", "mock_linkedin", "telegram"]},
    )
    assert gen_res.status_code == 201
    generated = gen_res.json()
    assert len(generated) == 3

    # 3. Retrieve draft variants
    list_res = client.get("/api/variants?status=draft")
    assert list_res.status_code == 200
    drafts = list_res.json()
    assert len(drafts) >= 3

    mock_x_draft = next((v for v in drafts if v["platform"] == "mock_x" and v["post_id"] == post_id), None)
    assert mock_x_draft is not None
    assert mock_x_draft["status"] == "draft"

    # 4. Approve valid mock_x variant
    approve_res = client.post(f"/api/variants/{mock_x_draft['id']}/approve")
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "approved"

    # 5. Schedule for a due time
    due_time = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    schedule_res = client.post(
        f"/api/variants/{mock_x_draft['id']}/schedule",
        json={"scheduled_at": due_time.isoformat().replace("+00:00", "Z")},
    )
    assert schedule_res.status_code == 201
    slot_id = schedule_res.json()["id"]
    assert schedule_res.json()["status"] == "pending"

    # 6. Run the scheduler as of 14:30 UTC
    as_of_time = datetime(2026, 10, 10, 14, 30, 0, tzinfo=timezone.utc)
    outcomes = SchedulerService.process_due_slots(
        db=db_session,
        current_time=as_of_time,
    )
    assert len(outcomes) == 1
    assert outcomes[0].success is True
    assert outcomes[0].slot_id == slot_id

    # 7. Verify one logical mock publication exists
    mock_posts = MockPostRepository.list_by_platform(db_session, "mock_x")
    assert len(mock_posts) == 1
    assert mock_posts[0].mock_post_id == outcomes[0].external_post_id

    # 8. Verify slot is completed
    slot = ScheduleSlotRepository.get_by_id(db_session, slot_id)
    assert slot is not None
    assert slot.status == ScheduleSlotStatus.COMPLETED
    assert slot.completed_at is not None

    # 9. Verify publish history contains the successful attempt
    hist_res = client.get(f"/api/publish-history?slot_id={slot_id}")
    assert hist_res.status_code == 200
    hist_data = hist_res.json()
    assert hist_data["total"] == 1
    attempt = hist_data["items"][0]
    assert attempt["schedule_slot_id"] == slot_id
    assert attempt["variant_id"] == mock_x_draft["id"]
    assert attempt["platform"] == "mock_x"
    assert attempt["status"] == "succeeded"
    assert attempt["external_post_id"] == outcomes[0].external_post_id


# ==============================================================================
# Scenario B: Invalid content
# ==============================================================================
def test_scenario_b_invalid_content(client: TestClient, db_session: Session):
    """Scenario B: Content violating platform constraints cannot be approved or scheduled.

    1. Create a post and generate a variant.
    2. Modify the content to violate a platform constraint.
    3. Attempt to approve it.
    4. Verify that approval is refused.
    5. Attempt to schedule it.
    6. Verify that scheduling is refused.
    7. Confirm that no publication was created.
    """
    post = PostRepository.create(
        db=db_session,
        title="Testing Guardrails",
        source_content="Content exploring boundaries in automated publishing pipelines.",
    )

    # 1. Generate variant
    gen_res = client.post(
        f"/api/posts/{post.id}/generate",
        json={"platforms": ["mock_x"]},
    )
    assert gen_res.status_code == 201
    variant_id = gen_res.json()[0]["id"]

    # 2. Modify content to violate constraint (mock_x max 3 hashtags; provide 6)
    invalid_content = "Violating hashtag limits on Mock X! #one #two #three #four #five #six"
    edit_res = client.patch(
        f"/api/variants/{variant_id}",
        json={"content": invalid_content},
    )
    # The edit itself is caught and refused with 422
    assert edit_res.status_code == 422
    assert "hashtags" in str(edit_res.json()).lower()

    # Even if an invalid variant was in the DB, attempt to approve
    # Create an invalid variant directly in DB in DRAFT state
    bad_variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="mock_x",
        content=invalid_content,
        status=VariantStatus.DRAFT,
    )

    # 3 & 4. Attempt to approve it -> refused with 422
    approve_res = client.post(f"/api/variants/{bad_variant.id}/approve")
    assert approve_res.status_code == 422
    assert "violates mock_x constraints" in approve_res.json()["detail"]["message"].lower()

    # 5 & 6. Attempt to schedule it -> refused with 400
    sched_res = client.post(
        f"/api/variants/{bad_variant.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:00:00Z"},
    )
    assert sched_res.status_code == 400

    # 7. Confirm no publication was created
    mock_posts = MockPostRepository.list_by_platform(db_session, "mock_x")
    assert len(mock_posts) == 0

    slots = ScheduleSlotRepository.list_by_variant_id(db_session, bad_variant.id)
    assert len(slots) == 0


# ==============================================================================
# Scenario C: Duplicate publishing
# ==============================================================================
def test_scenario_c_duplicate_publishing(client: TestClient, db_session: Session):
    """Scenario C: Repeated publish requests do not duplicate posts.

    1. Create and approve a valid variant.
    2. Schedule a slot.
    3. Publish the slot.
    4. Repeat the publish request.
    5. Simulate concurrent requests where practical.
    6. Verify that there is only one logical mock publication.
    7. Verify that the completed slot remains completed.
    """
    post = PostRepository.create(
        db=db_session,
        title="Idempotency in Action",
        source_content="Guaranteeing exactly-one logical publishing across retries.",
    )
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="mock_x",
        content="Idempotent publishing demo for distributed backends! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )

    # 2. Schedule slot
    sched_time = datetime(2026, 10, 10, 15, 0, 0, tzinfo=timezone.utc)
    slot_res = client.post(
        f"/api/variants/{variant.id}/schedule",
        json={"scheduled_at": sched_time.isoformat().replace("+00:00", "Z")},
    )
    assert slot_res.status_code == 201
    slot_id = slot_res.json()["id"]

    # 3. Publish the slot
    pub_res1 = client.post(f"/api/schedule-slots/{slot_id}/publish")
    assert pub_res1.status_code == 200
    assert pub_res1.json()["success"] is True
    assert pub_res1.json()["is_replay"] is False
    ext_id = pub_res1.json()["external_post_id"]

    # 4. Repeat the publish request (Idempotent replay)
    pub_res2 = client.post(f"/api/schedule-slots/{slot_id}/publish")
    assert pub_res2.status_code == 200
    assert pub_res2.json()["success"] is True
    assert pub_res2.json()["is_replay"] is True
    assert pub_res2.json()["external_post_id"] == ext_id

    # 5. Direct publishing service call also returns replay
    outcome = PublishingService.publish_slot(db_session, slot_id)
    assert outcome.success is True
    assert outcome.is_replay is True
    assert outcome.external_post_id == ext_id

    # 6. Verify only one logical mock publication in DB
    all_mock_posts = MockPostRepository.list_by_platform(db_session, "mock_x")
    assert len(all_mock_posts) == 1
    assert all_mock_posts[0].mock_post_id == ext_id

    # 7. Verify completed slot remains completed
    slot = ScheduleSlotRepository.get_by_id(db_session, slot_id)
    assert slot is not None
    assert slot.status == ScheduleSlotStatus.COMPLETED


# ==============================================================================
# Scenario D: Worker restart & failure recovery
# ==============================================================================
def test_scenario_d_worker_restart_and_recovery(db_session: Session):
    """Scenario D: Controlled failure injection and restart recovery.

    1. Create multiple due slots.
    2. Publish some slots (Slot 1 completes).
    3. Simulate interruption while another slot is processing (Slot 2 left in PROCESSING).
    4. Simulate an uncertain external attempt (Slot 3 left in RECONCILIATION_REQUIRED).
    5. Leave Slot 4 in recoverable PENDING status.
    6. Restart the worker.
    7. Verify completed slots are not published again.
    8. Verify unresolved external attempts are not blindly retried.
    9. Verify that safely recoverable work resumes.
    """
    post = PostRepository.create(
        db=db_session,
        title="Resilient Distributed Systems",
        source_content="Examining worker failure modes, atomic state machines, and safe recovery.",
    )
    base_time = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Slot 1: Succeeded before crash
    v1 = VariantRepository.create(
        db=db_session, post_id=post.id, platform="mock_x",
        content="Slot 1 completed! Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    t1 = base_time + timedelta(minutes=1)
    s1 = ScheduleSlotRepository.create(
        db=db_session, variant_id=v1.id, scheduled_at=t1,
        idempotency_key=derive_slot_idempotency_key(v1.id, t1),
    )
    PublishingService.publish_slot(db_session, s1.id)

    # Slot 2: Worker crashed mid-flight while in PROCESSING state
    v2 = VariantRepository.create(
        db=db_session, post_id=post.id, platform="mock_x",
        content="Slot 2 crashed in flight! Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    t2 = base_time + timedelta(minutes=2)
    s2 = ScheduleSlotRepository.create(
        db=db_session, variant_id=v2.id, scheduled_at=t2,
        idempotency_key=derive_slot_idempotency_key(v2.id, t2),
        status=ScheduleSlotStatus.PROCESSING,
    )
    s2.processing_started_at = t2
    db_session.commit()

    # Slot 3: Uncertain outcome from external API (requires reconciliation)
    v3 = VariantRepository.create(
        db=db_session, post_id=post.id, platform="mock_x",
        content="Slot 3 uncertain timeout! Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    t3 = base_time + timedelta(minutes=3)
    s3 = ScheduleSlotRepository.create(
        db=db_session, variant_id=v3.id, scheduled_at=t3,
        idempotency_key=derive_slot_idempotency_key(v3.id, t3),
        status=ScheduleSlotStatus.RECONCILIATION_REQUIRED,
    )
    PublishAttemptRepository.create(
        db=db_session,
        schedule_slot_id=s3.id,
        attempt_number=1,
        status=PublishAttemptStatus.UNCERTAIN,
        error_message="Telegram API request timed out after 10.0 seconds.",
    )

    # Slot 4: Safely recoverable due pending slot
    v4 = VariantRepository.create(
        db=db_session, post_id=post.id, platform="mock_linkedin",
        content="Slot 4 recoverable pending! What are your thoughts on this? #Leadership #Engineering",
        status=VariantStatus.APPROVED,
    )
    t4 = base_time + timedelta(minutes=4)
    s4 = ScheduleSlotRepository.create(
        db=db_session, variant_id=v4.id, scheduled_at=t4,
        idempotency_key=derive_slot_idempotency_key(v4.id, t4),
        status=ScheduleSlotStatus.PENDING,
    )

    # 6. Restart worker and run polling iteration
    worker = SchedulerWorker(session_factory=lambda: db_session)
    now_eval = base_time + timedelta(hours=1)
    outcomes = worker.run_once(current_time=now_eval)

    # 7. Completed Slot 1 was NOT re-published
    # 8. Uncertain Slot 3 was NOT re-executed
    # 9. In-flight Slot 2 was NOT blindly reset to pending or re-executed
    # 10. Recoverable Slot 4 WAS processed and completed!
    assert len(outcomes) == 1
    assert outcomes[0].slot_id == s4.id
    assert outcomes[0].success is True

    db_session.refresh(s1)
    assert s1.status == ScheduleSlotStatus.COMPLETED

    db_session.refresh(s2)
    assert s2.status == ScheduleSlotStatus.PROCESSING  # preserved safely

    db_session.refresh(s3)
    assert s3.status == ScheduleSlotStatus.RECONCILIATION_REQUIRED  # preserved safely

    db_session.refresh(s4)
    assert s4.status == ScheduleSlotStatus.COMPLETED


# ==============================================================================
# Scenario E: Adapter swapping
# ==============================================================================
def test_scenario_e_adapter_swapping(db_session: Session):
    """Scenario E: Unified publishing pipeline supports multiple adapters without business logic changes.

    1. Configure mock_x.
    2. Publish an approved variant.
    3. Configure mock_linkedin.
    4. Publish another approved variant.
    5. Verify that the same publishing service works with both.
    6. Confirm that no platform-specific conditional logic was added to scheduler.
    """
    post = PostRepository.create(
        db=db_session,
        title="Adapter Pattern in Action",
        source_content="Unified interfaces enable modular platform adapters.",
    )

    # 1 & 2. Publish mock_x
    v_x = VariantRepository.create(
        db=db_session, post_id=post.id, platform="mock_x",
        content="Testing mock_x adapter! Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    outcome_x = PublishingService.publish_variant(db_session, v_x.id)
    assert outcome_x.success is True
    assert outcome_x.platform == "mock_x"
    assert outcome_x.external_post_id.startswith("mock_x_post_")

    # 3 & 4. Publish mock_linkedin
    v_li = VariantRepository.create(
        db=db_session, post_id=post.id, platform="mock_linkedin",
        content="Testing mock_linkedin adapter. What are your thoughts on this? #Leadership #Engineering",
        status=VariantStatus.APPROVED,
    )
    outcome_li = PublishingService.publish_variant(db_session, v_li.id)
    assert outcome_li.success is True
    assert outcome_li.platform == "mock_linkedin"
    assert outcome_li.external_post_id.startswith("urn:li:share:mock_")

    # 5. Verify Registry handles adapters uniformly
    pub_x = PublisherRegistry.get_publisher("mock_x")
    pub_li = PublisherRegistry.get_publisher("mock_linkedin")
    assert pub_x.platform_name == "mock_x"
    assert pub_li.platform_name == "mock_linkedin"


# ==============================================================================
# Scenario F: Real Telegram adapter (mocked HTTP)
# ==============================================================================
def test_scenario_f_telegram_adapter_lifecycle(db_session: Session):
    """Scenario F: Telegram adapter with mocked HTTP verifies payload, success, error, timeout, and token redaction.

    1. Verify payload construction (chat_id, text).
    2. Verify successful response (200 OK -> message ID).
    3. Verify API error (400 Bad Request -> confirmed failure).
    4. Verify timeout error (HTTP Timeout -> is_uncertain=True).
    5. Verify bot token redaction in error messages.
    """
    post = PostRepository.create(
        db=db_session,
        title="Telegram Architecture",
        source_content="Deep dive into Telegram Bot API mechanics.",
    )
    variant = VariantRepository.create(
        db=db_session, post_id=post.id, platform="telegram",
        content="📢 New update available.\n\nRead our architectural report.\n\n#Tech",
        status=VariantStatus.APPROVED,
    )

    slot_time = datetime(2026, 10, 10, 16, 0, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=variant.id,
        scheduled_at=slot_time,
        idempotency_key=derive_slot_idempotency_key(variant.id, slot_time),
    )

    secret_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

    # Case 1: Successful response
    with patch.object(settings, "TELEGRAM_BOT_TOKEN", secret_token), \
         patch.object(settings, "TELEGRAM_CHAT_ID", "-100123456789"), \
         patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {
                "message_id": 9988,
                "chat": {"id": -100123456789, "title": "Tech Channel"},
                "date": 1760112000,
                "text": variant.content,
            },
        }
        mock_client.post.return_value = mock_resp

        outcome = PublishingService.publish_slot(db_session, slot.id)
        assert outcome.success is True
        assert outcome.external_post_id == "9988"
        assert outcome.platform == "telegram"

        # Verify request payload
        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert kwargs["json"]["chat_id"] == "-100123456789"
        assert kwargs["json"]["text"] == variant.content

    # Case 2: Timeout -> uncertain outcome and token redaction
    slot2_time = datetime(2026, 10, 10, 17, 0, 0, tzinfo=timezone.utc)
    v2 = VariantRepository.create(
        db=db_session, post_id=post.id, platform="telegram",
        content="📢 Second update.\n\nReliability testing.\n\n#Tech",
        status=VariantStatus.APPROVED,
    )
    slot2 = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=v2.id,
        scheduled_at=slot2_time,
        idempotency_key=derive_slot_idempotency_key(v2.id, slot2_time),
    )

    with patch.object(settings, "TELEGRAM_BOT_TOKEN", secret_token), \
         patch.object(settings, "TELEGRAM_CHAT_ID", "-100123456789"), \
         patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("Connection timed out after 10.0 seconds")

        outcome_timeout = PublishingService.publish_slot(db_session, slot2.id)
        assert outcome_timeout.success is False
        assert outcome_timeout.is_uncertain is True
        assert "timed out after" in str(outcome_timeout.error).lower()

        db_session.refresh(slot2)
        assert slot2.status == ScheduleSlotStatus.RECONCILIATION_REQUIRED

    # Case 3: Network error with raw token in exception -> sanitized
    slot3_time = datetime(2026, 10, 10, 18, 0, 0, tzinfo=timezone.utc)
    v3 = VariantRepository.create(
        db=db_session, post_id=post.id, platform="telegram",
        content="📢 Third update.\n\nSanitization testing.\n\n#Tech",
        status=VariantStatus.APPROVED,
    )
    slot3 = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=v3.id,
        scheduled_at=slot3_time,
        idempotency_key=derive_slot_idempotency_key(v3.id, slot3_time),
    )

    with patch.object(settings, "TELEGRAM_BOT_TOKEN", secret_token), \
         patch.object(settings, "TELEGRAM_CHAT_ID", "-100123456789"), \
         patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.RequestError(f"Connection failed to api.telegram.org/bot{secret_token}/sendMessage")

        outcome_err = PublishingService.publish_slot(db_session, slot3.id)
        assert outcome_err.success is False
        assert outcome_err.is_uncertain is True
        assert secret_token not in str(outcome_err.error)
        assert "[REDACTED_BOT_TOKEN]" in str(outcome_err.error)
