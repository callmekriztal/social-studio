from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.registry import PublisherRegistry
from app.config import settings
from app.models.enums import (
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
from app.validators.validator import VariantValidator


@pytest.fixture(autouse=True)
def clean_database(db_session: Session):
    """Ensure a clean database state for each acceptance probe."""
    db_session.query(PublishAttempt).delete()
    db_session.query(ScheduleSlot).delete()
    db_session.query(MockPlatformPost).delete()
    db_session.query(Variant).delete()
    db_session.query(Post).delete()
    db_session.commit()
    yield


@pytest.fixture
def sample_post(db_session: Session) -> Post:
    """Fixture providing a stored blog post."""
    return PostRepository.create(
        db=db_session,
        title="Modern Cloud Architecture Patterns",
        source_content=(
            "# Architecture Patterns\n\n"
            "Decoupled event-driven backends provide durability and horizontal scalability. "
            "By enforcing strict boundary validation and deterministic idempotency keys, "
            "services eliminate duplicate side effects in asynchronous distributed workloads."
        ),
    )


# ==============================================================================
# Probe 1: A stored post produces valid variants for the configured platforms.
# ==============================================================================
def test_acceptance_probe_1_stored_post_produces_valid_variants(
    client: TestClient,
    sample_post: Post,
):
    """Probe 1: A stored post produces valid variants for the configured platforms."""
    res = client.post(
        f"/api/posts/{sample_post.id}/generate",
        json={"platforms": ["mock_x", "mock_linkedin", "telegram"]},
    )
    assert res.status_code == 201
    variants = res.json()
    assert len(variants) == 3

    platforms_found = set()
    for v in variants:
        assert v["post_id"] == sample_post.id
        assert v["status"] == "draft"  # valid variants enter draft review
        assert v["validation_errors"] is None
        assert len(v["content"]) > 0

        # Verify against platform constraint profile
        val = VariantValidator.validate(v["platform"], v["content"])
        assert val.is_valid is True, f"Variant for {v['platform']} failed constraints: {val.errors}"
        platforms_found.add(v["platform"])

    assert platforms_found == {"mock_x", "mock_linkedin", "telegram"}


# ==============================================================================
# Probe 2: A variant violating a platform rule is rejected naming broken rule.
# ==============================================================================
def test_acceptance_probe_2_violating_variant_rejected_naming_broken_rule(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    """Probe 2: A variant violating a platform rule is rejected with an error naming the broken rule."""
    # mock_x allows max 3 hashtags; provide 5 hashtags
    violating_content = "Violating hashtag rule! Thoughts? #one #two #three #four #five"
    variant = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content=violating_content,
        status=VariantStatus.DRAFT,
    )

    res = client.post(f"/api/variants/{variant.id}/approve")
    assert res.status_code == 422
    err_detail = res.json()["detail"]
    assert "message" in err_detail
    assert "errors" in err_detail

    # Must explicitly name the broken rule
    errors_joined = " ".join(err_detail["errors"])
    assert "maximum of 3 hashtags" in errors_joined
    assert "#four" in errors_joined or "found 5" in errors_joined


# ==============================================================================
# Probe 3: An unapproved variant cannot be scheduled and receives a 4xx response.
# ==============================================================================
def test_acceptance_probe_3_unapproved_variant_cannot_be_scheduled_receives_4xx(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    """Probe 3: An unapproved variant cannot be scheduled and receives a 4xx response."""
    # Case A: Draft variant
    v_draft = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Draft content awaiting review. Thoughts? #Tech",
        status=VariantStatus.DRAFT,
    )
    res_draft = client.post(
        f"/api/variants/{v_draft.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:00:00Z"},
    )
    assert res_draft.status_code == 400
    assert "draft" in res_draft.json()["detail"].lower()

    # Case B: Rejected variant
    v_rej = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Rejected content. Thoughts? #Tech",
        status=VariantStatus.REJECTED,
    )
    res_rej = client.post(
        f"/api/variants/{v_rej.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:00:00Z"},
    )
    assert res_rej.status_code == 400
    assert "rejected" in res_rej.json()["detail"].lower()

    # Case C: Published variant
    v_pub = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Already published content. Thoughts? #Tech",
        status=VariantStatus.PUBLISHED,
    )
    res_pub = client.post(
        f"/api/variants/{v_pub.id}/schedule",
        json={"scheduled_at": "2026-10-10T15:00:00Z"},
    )
    assert res_pub.status_code == 400
    assert "published" in res_pub.json()["detail"].lower()


# ==============================================================================
# Probe 4: Approved variant scheduled for future time and published when due.
# ==============================================================================
def test_acceptance_probe_4_approved_variant_scheduled_and_published_when_due(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    """Probe 4: An approved variant can be scheduled for a future time and published when due."""
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Future scheduled post test! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )

    future_time = datetime(2026, 10, 10, 16, 0, 0, tzinfo=timezone.utc)
    res_sched = client.post(
        f"/api/variants/{v.id}/schedule",
        json={"scheduled_at": future_time.isoformat().replace("+00:00", "Z")},
    )
    assert res_sched.status_code == 201
    slot_id = res_sched.json()["id"]

    # Step 1: Run scheduler before due time (at 15:30 UTC)
    time_before = datetime(2026, 10, 10, 15, 30, 0, tzinfo=timezone.utc)
    outcomes_early = SchedulerService.process_due_slots(db_session, current_time=time_before)
    assert len(outcomes_early) == 0

    slot = ScheduleSlotRepository.get_by_id(db_session, slot_id)
    assert slot.status == ScheduleSlotStatus.PENDING

    # Step 2: Run scheduler when due (at 16:05 UTC)
    time_due = datetime(2026, 10, 10, 16, 5, 0, tzinfo=timezone.utc)
    outcomes_due = SchedulerService.process_due_slots(db_session, current_time=time_due)
    assert len(outcomes_due) == 1
    assert outcomes_due[0].success is True
    assert outcomes_due[0].slot_id == slot_id

    # Verify slot completed and mock publication created
    db_session.refresh(slot)
    assert slot.status == ScheduleSlotStatus.COMPLETED

    mock_posts = MockPostRepository.list_by_platform(db_session, "mock_x")
    assert len(mock_posts) == 1
    assert mock_posts[0].mock_post_id == outcomes_due[0].external_post_id


# ==============================================================================
# Probe 5: Repeated publication & simulated recovery do not create duplicate posts.
# ==============================================================================
def test_acceptance_probe_5_repeated_publication_and_recovery_no_duplicates(
    client: TestClient,
    sample_post: Post,
    db_session: Session,
):
    """Probe 5: Repeated publication and simulated worker recovery do not create duplicate mock posts."""
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Testing duplicate prevention! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )
    due_time = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    res_sched = client.post(
        f"/api/variants/{v.id}/schedule",
        json={"scheduled_at": due_time.isoformat().replace("+00:00", "Z")},
    )
    assert res_sched.status_code == 201
    slot_id = res_sched.json()["id"]

    # 1. Publish slot through worker
    worker = SchedulerWorker(session_factory=lambda: db_session)
    now = datetime(2026, 10, 10, 10, 15, 0, tzinfo=timezone.utc)
    outcomes1 = worker.run_once(current_time=now)
    assert len(outcomes1) == 1
    assert outcomes1[0].success is True

    # 1 mock post created
    mock_posts_1 = MockPostRepository.list_by_platform(db_session, "mock_x")
    assert len(mock_posts_1) == 1
    orig_post_id = mock_posts_1[0].mock_post_id

    # 2. Repeated publication via API endpoint returns replay
    res_repeat = client.post(f"/api/schedule-slots/{slot_id}/publish")
    assert res_repeat.status_code == 200
    assert res_repeat.json()["is_replay"] is True
    assert res_repeat.json()["external_post_id"] == orig_post_id

    # 3. Simulate worker restart & polling run
    worker_restarted = SchedulerWorker(session_factory=lambda: db_session)
    outcomes2 = worker_restarted.run_once(current_time=now + timedelta(minutes=5))
    assert len(outcomes2) == 0

    # Still strictly exactly 1 mock post in storage!
    mock_posts_after = MockPostRepository.list_by_platform(db_session, "mock_x")
    assert len(mock_posts_after) == 1
    assert mock_posts_after[0].mock_post_id == orig_post_id


# ==============================================================================
# Probe 6: Switching from Telegram to mock_x through configuration changes adapter.
# ==============================================================================
def test_acceptance_probe_6_switching_adapter_through_config_changes_adapter(
    sample_post: Post,
    db_session: Session,
):
    """Probe 6: Switching through configuration changes the adapter without changing campaign business logic."""
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Configurable adapter testing! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )

    # 1. When configured with mock_x:
    with patch.object(settings, "DEFAULT_PUBLISHER", "mock_x"):
        publisher_x = PublisherRegistry.get_default_publisher()
        assert publisher_x.platform_name == "mock_x"

        outcome_x = PublishingService.publish_variant(
            db=db_session,
            variant_id=v.id,
            platform_override="mock_x",
        )
        assert outcome_x.success is True
        assert outcome_x.platform == "mock_x"

    # 2. When configured with mock_linkedin:
    v2 = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_linkedin",
        content="Configurable adapter testing. What are your thoughts on this? #Leadership #Engineering",
        status=VariantStatus.APPROVED,
    )
    with patch.object(settings, "DEFAULT_PUBLISHER", "mock_linkedin"):
        publisher_li = PublisherRegistry.get_default_publisher()
        assert publisher_li.platform_name == "mock_linkedin"

        outcome_li = PublishingService.publish_variant(
            db=db_session,
            variant_id=v2.id,
            platform_override="mock_linkedin",
        )
        assert outcome_li.success is True
        assert outcome_li.platform == "mock_linkedin"
