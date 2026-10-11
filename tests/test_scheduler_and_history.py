from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import (
    PublishAttemptStatus,
    ScheduleSlotStatus,
    VariantStatus,
)
from app.models.post import Post
from app.models.publish_attempt import PublishAttempt
from app.models.schedule_slot import ScheduleSlot
from app.models.variant import Variant
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
    db_session.query(Variant).delete()
    db_session.query(Post).delete()
    db_session.commit()
    yield


@pytest.fixture
def sample_post(db_session: Session) -> Post:
    """Fixture providing a persisted Post."""
    return PostRepository.create(
        db=db_session,
        title="Durable Scheduling Mastery",
        source_content="Comprehensive testing of durable scheduler, background worker, and publish history.",
    )


@pytest.fixture
def approved_x_variant(db_session: Session, sample_post: Post) -> Variant:
    """Fixture providing a valid, approved X variant."""
    return VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Testing scheduler and publish history with full idempotency! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )


@pytest.fixture
def approved_linkedin_variant(db_session: Session, sample_post: Post) -> Variant:
    """Fixture providing a valid, approved LinkedIn variant."""
    return VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_linkedin",
        content="Professional insights into durable scheduling and background workers. What are your thoughts on this? #Leadership #Engineering",
        status=VariantStatus.APPROVED,
    )


# Scenario 1: Valid approved variant scheduled successfully with UTC normalization
def test_1_valid_approved_variant_scheduled_successfully(
    client: TestClient,
    approved_x_variant: Variant,
    db_session: Session,
):
    # Pass an offset-aware timestamp (+02:00)
    scheduled_time_str = "2026-10-10T17:30:00+02:00"
    response = client.post(
        f"/api/variants/{approved_x_variant.id}/schedule",
        json={"scheduled_at": scheduled_time_str},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["variant_id"] == approved_x_variant.id
    assert data["status"] == "pending"
    # Should be normalized to 15:30:00 UTC
    assert data["scheduled_at"].startswith("2026-10-10T15:30:00")
    assert "slot:var-" in data["idempotency_key"]

    # Verify database persistence
    slot = ScheduleSlotRepository.get_by_id(db_session, data["id"])
    assert slot is not None
    assert slot.status == ScheduleSlotStatus.PENDING


# Scenario 2: Naive or invalid timestamps rejected
def test_2_naive_or_invalid_timestamps_rejected(
    client: TestClient,
    approved_x_variant: Variant,
):
    # Naive timestamp (no tzinfo)
    response_naive = client.post(
        f"/api/variants/{approved_x_variant.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:30:00"},
    )
    assert response_naive.status_code == 422
    assert "timezone-aware" in response_naive.text

    # Completely invalid format
    response_invalid = client.post(
        f"/api/variants/{approved_x_variant.id}/schedule",
        json={"scheduled_at": "not-a-valid-timestamp"},
    )
    assert response_invalid.status_code == 422


# Scenario 3: Draft variant cannot be scheduled
def test_3_draft_variant_cannot_be_scheduled(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    draft_variant = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Draft content awaiting review. Thoughts? #Tech",
        status=VariantStatus.DRAFT,
    )
    response = client.post(
        f"/api/variants/{draft_variant.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:30:00Z"},
    )
    assert response.status_code == 400
    assert "Only APPROVED variants may be scheduled" in response.json()["detail"]


# Scenario 4: Rejected variant cannot be scheduled
def test_4_rejected_variant_cannot_be_scheduled(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    rejected_variant = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Rejected content. Thoughts? #Tech",
        status=VariantStatus.REJECTED,
    )
    response = client.post(
        f"/api/variants/{rejected_variant.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:30:00Z"},
    )
    assert response.status_code == 400
    assert "Variant is REJECTED" in response.json()["detail"]


# Scenario 5: Published variant cannot be scheduled
def test_5_published_variant_cannot_be_scheduled(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    published_variant = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Already published content. Thoughts? #Tech",
        status=VariantStatus.PUBLISHED,
    )
    response = client.post(
        f"/api/variants/{published_variant.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:30:00Z"},
    )
    assert response.status_code == 400
    assert "already been PUBLISHED" in response.json()["detail"]


# Scenario 6: Invalid variant cannot be scheduled even if marked approved
def test_6_invalid_variant_cannot_be_scheduled_even_if_marked_approved(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    # mock_x allows max 4 hashtags and 280 chars; here we put 6 hashtags
    invalid_content = "This content has too many hashtags! #one #two #three #four #five #six"
    variant = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content=invalid_content,
        status=VariantStatus.APPROVED,
    )
    response = client.post(
        f"/api/variants/{variant.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:30:00Z"},
    )
    assert response.status_code == 400
    assert "violates platform constraints" in response.json()["detail"]


# Scenario 7: Duplicate schedule for same variant and timestamp returns 409 Conflict
def test_7_duplicate_schedule_same_timestamp_returns_409(
    client: TestClient,
    approved_x_variant: Variant,
):
    timestamp_str = "2026-10-10T15:30:00Z"
    res1 = client.post(
        f"/api/variants/{approved_x_variant.id}/schedule",
        json={"scheduled_at": timestamp_str},
    )
    assert res1.status_code == 201

    res2 = client.post(
        f"/api/variants/{approved_x_variant.id}/schedule",
        json={"scheduled_at": timestamp_str},
    )
    assert res2.status_code == 409
    assert "already scheduled" in res2.json()["detail"]


# Scenario 8: Stable idempotency key generated deterministically
def test_8_stable_idempotency_key_generated_deterministically(
    approved_x_variant: Variant,
):
    dt1 = datetime(2026, 10, 10, 15, 30, 0, tzinfo=timezone.utc)
    # Same moment in different timezone offset
    dt2 = datetime(2026, 10, 10, 17, 30, 0, tzinfo=timezone(timedelta(hours=2)))

    key1 = derive_slot_idempotency_key(approved_x_variant.id, dt1)
    key2 = derive_slot_idempotency_key(approved_x_variant.id, dt2)

    assert key1 == key2
    assert key1 == f"slot:var-{approved_x_variant.id}:20261010153000"


# Scenario 9: Due slots identified and processed by scheduler
def test_9_due_slots_identified_and_processed_by_scheduler(
    approved_x_variant: Variant,
    db_session: Session,
):
    slot_time = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    idempotency_key = derive_slot_idempotency_key(approved_x_variant.id, slot_time)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_x_variant.id,
        scheduled_at=slot_time,
        idempotency_key=idempotency_key,
    )

    # Process due slots as of 10:30 UTC (slot_time <= current_time)
    current_time = datetime(2026, 10, 10, 10, 30, 0, tzinfo=timezone.utc)
    outcomes = SchedulerService.process_due_slots(
        db=db_session,
        current_time=current_time,
    )

    assert len(outcomes) == 1
    assert outcomes[0].success is True
    assert outcomes[0].slot_id == slot.id

    # Verify slot is COMPLETED and variant is PUBLISHED
    db_session.refresh(slot)
    assert slot.status == ScheduleSlotStatus.COMPLETED
    assert slot.completed_at is not None

    db_session.refresh(approved_x_variant)
    assert approved_x_variant.status == VariantStatus.PUBLISHED

    # Verify attempt record
    attempts = PublishAttemptRepository.list_by_slot_id(db_session, slot.id)
    assert len(attempts) == 1
    assert attempts[0].status == PublishAttemptStatus.SUCCEEDED


# Scenario 10: Future slots remain pending and are not published before their time
def test_10_future_slots_remain_pending_not_published_early(
    approved_x_variant: Variant,
    db_session: Session,
):
    future_time = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    idempotency_key = derive_slot_idempotency_key(approved_x_variant.id, future_time)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_x_variant.id,
        scheduled_at=future_time,
        idempotency_key=idempotency_key,
    )

    # Scheduler runs before slot time (at 11:00 UTC)
    current_time = datetime(2026, 10, 10, 11, 0, 0, tzinfo=timezone.utc)
    outcomes = SchedulerService.process_due_slots(
        db=db_session,
        current_time=current_time,
    )

    assert len(outcomes) == 0

    db_session.refresh(slot)
    assert slot.status == ScheduleSlotStatus.PENDING

    attempts = PublishAttemptRepository.list_by_slot_id(db_session, slot.id)
    assert len(attempts) == 0


# Scenario 11: Bounded batch size respected
def test_11_bounded_batch_size_respected(
    sample_post: Post,
    db_session: Session,
):
    # Create 5 approved variants and 5 due slots
    slots = []
    base_time = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        var = VariantRepository.create(
            db=db_session,
            post_id=sample_post.id,
            platform="mock_x",
            content=f"Batch test variant #{i} for scheduler limit verification! Thoughts? #Tech",
            status=VariantStatus.APPROVED,
        )
        slot_dt = base_time + timedelta(minutes=i)
        key = derive_slot_idempotency_key(var.id, slot_dt)
        s = ScheduleSlotRepository.create(
            db=db_session,
            variant_id=var.id,
            scheduled_at=slot_dt,
            idempotency_key=key,
        )
        slots.append(s)

    current_time = base_time + timedelta(hours=1)
    # Batch size = 2
    outcomes = SchedulerService.process_due_slots(
        db=db_session,
        current_time=current_time,
        batch_size=2,
    )

    assert len(outcomes) == 2
    for out in outcomes:
        assert out.success is True

    # Check remaining status
    due_count = ScheduleSlotRepository.count_due_slots(db_session, max_time=current_time)
    assert due_count == 3


# Scenario 12: Error in one slot does not stop processing of remaining batch
def test_12_error_in_one_slot_does_not_halt_remaining_batch(
    sample_post: Post,
    db_session: Session,
):
    # Variant 1: fails when publisher throws an error
    var1 = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Variant 1 destined to fail during publish! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    t1 = datetime(2026, 10, 10, 9, 0, 0, tzinfo=timezone.utc)
    s1 = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=var1.id,
        scheduled_at=t1,
        idempotency_key=derive_slot_idempotency_key(var1.id, t1),
    )

    # Variant 2: succeeds normally
    var2 = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_linkedin",
        content="Variant 2 destined to succeed smoothly. What are your thoughts on this? #Leadership #Engineering",
        status=VariantStatus.APPROVED,
    )
    t2 = datetime(2026, 10, 10, 9, 5, 0, tzinfo=timezone.utc)
    s2 = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=var2.id,
        scheduled_at=t2,
        idempotency_key=derive_slot_idempotency_key(var2.id, t2),
    )

    current_time = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)

    # Patch MockXPublisher.publish to raise an unexpected runtime error
    with patch("app.adapters.mock_x.MockXPublisher.publish", side_effect=RuntimeError("Simulated publisher crash")):
        outcomes = SchedulerService.process_due_slots(
            db=db_session,
            current_time=current_time,
            batch_size=10,
        )

    assert len(outcomes) == 2
    # First slot failed or was caught
    assert outcomes[0].success is False
    assert outcomes[0].slot_id == s1.id

    # Second slot succeeded despite first slot failing!
    assert outcomes[1].success is True
    assert outcomes[1].slot_id == s2.id


# Scenario 13: Worker processes due slots and updates slot/attempt statuses
def test_13_worker_processes_due_slots_and_updates_status(
    approved_x_variant: Variant,
    db_session: Session,
):
    slot_time = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=approved_x_variant.id,
        scheduled_at=slot_time,
        idempotency_key=derive_slot_idempotency_key(approved_x_variant.id, slot_time),
    )

    # Run worker iteration with session factory returning our test db_session
    worker = SchedulerWorker(
        session_factory=lambda: db_session,
        poll_interval=1.0,
        batch_size=5,
    )
    current_time = datetime(2026, 10, 10, 10, 15, 0, tzinfo=timezone.utc)
    outcomes = worker.run_once(current_time=current_time)

    assert len(outcomes) == 1
    assert outcomes[0].success is True

    db_session.refresh(slot)
    assert slot.status == ScheduleSlotStatus.COMPLETED


# Scenario 14: Restart safety: worker does not blindly reset processing or re-execute uncertain slots
def test_14_restart_safety_no_blind_reset_and_uncertain_reconciliation(
    sample_post: Post,
    db_session: Session,
):
    # Slot 1 stuck in PROCESSING (e.g. previous crash)
    var1 = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Processing variant! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    t1 = datetime(2026, 10, 10, 8, 0, 0, tzinfo=timezone.utc)
    s1 = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=var1.id,
        scheduled_at=t1,
        idempotency_key=derive_slot_idempotency_key(var1.id, t1),
        status=ScheduleSlotStatus.PROCESSING,
    )

    # Slot 2 in RECONCILIATION_REQUIRED
    var2 = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Uncertain variant! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    t2 = datetime(2026, 10, 10, 8, 5, 0, tzinfo=timezone.utc)
    s2 = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=var2.id,
        scheduled_at=t2,
        idempotency_key=derive_slot_idempotency_key(var2.id, t2),
        status=ScheduleSlotStatus.RECONCILIATION_REQUIRED,
    )

    # Worker starts fresh and runs
    worker = SchedulerWorker(session_factory=lambda: db_session)
    now = datetime(2026, 10, 10, 9, 0, 0, tzinfo=timezone.utc)
    outcomes = worker.run_once(current_time=now)

    # Neither slot is picked up by process_due_slots
    assert len(outcomes) == 0

    db_session.refresh(s1)
    assert s1.status == ScheduleSlotStatus.PROCESSING  # not blindly reset to PENDING

    db_session.refresh(s2)
    assert s2.status == ScheduleSlotStatus.RECONCILIATION_REQUIRED  # not re-executed


# Scenario 15: Publish history list endpoint supports filtering and pagination
def test_15_publish_history_list_filtering_and_pagination(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    # Create 2 variants and publish them
    var_x = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="History test X post! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    var_li = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_linkedin",
        content="History test LinkedIn post. What are your thoughts on this? #Leadership #Engineering",
        status=VariantStatus.APPROVED,
    )

    PublishingService.publish_variant(db_session, var_x.id)
    PublishingService.publish_variant(db_session, var_li.id)

    # 1. Unfiltered query
    res = client.get("/api/publish-history")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 2
    assert len(data["items"]) >= 2
    # Verify fields on item
    first_item = data["items"][0]
    assert "attempt_number" in first_item
    assert "status" in first_item
    assert "started_at" in first_item
    assert "platform" in first_item

    # 2. Filter by platform
    res_x = client.get("/api/publish-history?platform=mock_x")
    assert res_x.status_code == 200
    data_x = res_x.json()
    assert all(item["platform"] == "mock_x" for item in data_x["items"])

    # 3. Filter by variant_id
    res_li = client.get(f"/api/publish-history?variant_id={var_li.id}")
    assert res_li.status_code == 200
    data_li = res_li.json()
    assert all(item["variant_id"] == var_li.id for item in data_li["items"])

    # 4. Filter by status
    res_succ = client.get("/api/publish-history?status=succeeded")
    assert res_succ.status_code == 200
    assert all(item["status"] == "succeeded" for item in res_succ.json()["items"])

    # 5. Pagination limit and offset
    res_page = client.get("/api/publish-history?limit=1&offset=0")
    assert res_page.status_code == 200
    assert len(res_page.json()["items"]) == 1


# Scenario 16: Publish history detail endpoint returns full attempt record, 404 for invalid ID
def test_16_publish_history_detail_endpoint(
    client: TestClient,
    approved_x_variant: Variant,
    db_session: Session,
):
    outcome = PublishingService.publish_variant(db_session, approved_x_variant.id)
    attempt_id = outcome.attempt_id
    assert attempt_id is not None

    # Retrieve by valid ID
    res = client.get(f"/api/publish-history/{attempt_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == attempt_id
    assert data["variant_id"] == approved_x_variant.id
    assert data["platform"] == "mock_x"
    assert data["status"] == "succeeded"
    assert data["external_post_id"] is not None

    # Retrieve non-existent attempt
    res_404 = client.get("/api/publish-history/999999")
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["detail"].lower()


# Scenario 17: Scheduler status endpoint returns accurate slot counts by status and due count
def test_17_scheduler_status_endpoint(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    # Setup slots with specific statuses:
    now_ref = datetime(2026, 10, 10, 15, 0, 0, tzinfo=timezone.utc)

    # Due pending slot (past)
    v1 = VariantRepository.create(
        db=db_session, post_id=sample_post.id, platform="mock_x",
        content="Slot due! Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    s1 = ScheduleSlotRepository.create(
        db=db_session, variant_id=v1.id,
        scheduled_at=now_ref - timedelta(hours=1),
        idempotency_key=derive_slot_idempotency_key(v1.id, now_ref - timedelta(hours=1)),
    )

    # Future pending slot (not due)
    v2 = VariantRepository.create(
        db=db_session, post_id=sample_post.id, platform="mock_x",
        content="Slot future! Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    s2 = ScheduleSlotRepository.create(
        db=db_session, variant_id=v2.id,
        scheduled_at=now_ref + timedelta(hours=2),
        idempotency_key=derive_slot_idempotency_key(v2.id, now_ref + timedelta(hours=2)),
    )

    # Completed slot
    v3 = VariantRepository.create(
        db=db_session, post_id=sample_post.id, platform="mock_x",
        content="Slot completed! Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    s3 = ScheduleSlotRepository.create(
        db=db_session, variant_id=v3.id,
        scheduled_at=now_ref - timedelta(hours=3),
        idempotency_key=derive_slot_idempotency_key(v3.id, now_ref - timedelta(hours=3)),
        status=ScheduleSlotStatus.COMPLETED,
    )

    # Reconciliation required slot
    v4 = VariantRepository.create(
        db=db_session, post_id=sample_post.id, platform="mock_x",
        content="Slot uncertain! Thoughts? #Tech", status=VariantStatus.APPROVED,
    )
    s4 = ScheduleSlotRepository.create(
        db=db_session, variant_id=v4.id,
        scheduled_at=now_ref - timedelta(hours=4),
        idempotency_key=derive_slot_idempotency_key(v4.id, now_ref - timedelta(hours=4)),
        status=ScheduleSlotStatus.RECONCILIATION_REQUIRED,
    )

    # Query status endpoint passing as_of timestamp matching now_ref
    as_of_param = now_ref.isoformat().replace("+00:00", "Z")
    res = client.get(f"/api/scheduler/status?as_of={as_of_param}")
    assert res.status_code == 200
    metrics = res.json()

    assert metrics["pending_slots"] >= 2
    assert metrics["due_slots"] >= 1
    assert metrics["completed_slots"] >= 1
    assert metrics["reconciliation_required_slots"] >= 1
    assert "current_time" in metrics

    # Also check /api/schedule-slots/status alias
    res_alias = client.get(f"/api/schedule-slots/status?as_of={as_of_param}")
    assert res_alias.status_code == 200
    assert res_alias.json()["due_slots"] >= 1
