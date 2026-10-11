from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import (
    Platform,
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


def test_1_post_creation_and_retrieval(db_session: Session):
    """1. A post can be created and retrieved."""
    post = PostRepository.create(
        db=db_session,
        title="10 Architecture Tips",
        source_content="# 10 Tips\n\nTip 1: Keep it simple.",
        source_url="https://example.com/blog/tips",
    )
    assert post.id is not None
    assert post.created_at is not None
    assert post.updated_at is not None

    retrieved = PostRepository.get_by_id(db_session, post.id)
    assert retrieved is not None
    assert retrieved.title == "10 Architecture Tips"
    assert retrieved.source_content == "# 10 Tips\n\nTip 1: Keep it simple."
    assert retrieved.source_url == "https://example.com/blog/tips"


def test_2_post_can_have_multiple_variants(db_session: Session):
    """2. A post can have multiple variants."""
    post = PostRepository.create(
        db=db_session,
        title="Multi-variant Post",
        source_content="Content for multiple platforms",
    )

    v1 = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform=Platform.TELEGRAM.value,
        content="Telegram post format",
        status=VariantStatus.DRAFT,
    )
    v2 = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform=Platform.MOCK_X.value,
        content="X thread post format",
        status=VariantStatus.DRAFT,
    )
    v3 = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform=Platform.MOCK_LINKEDIN.value,
        content="LinkedIn professional format",
        status=VariantStatus.DRAFT,
    )

    post_with_variants = PostRepository.get_with_variants(db_session, post.id)
    assert post_with_variants is not None
    assert len(post_with_variants.variants) == 3
    platforms = {v.platform for v in post_with_variants.variants}
    assert platforms == {
        Platform.TELEGRAM.value,
        Platform.MOCK_X.value,
        Platform.MOCK_LINKEDIN.value,
    }


def test_3_variant_belongs_to_correct_post(db_session: Session):
    """3. A variant belongs to the correct post."""
    post1 = PostRepository.create(db=db_session, title="Post One", source_content="Content 1")
    post2 = PostRepository.create(db=db_session, title="Post Two", source_content="Content 2")

    variant = VariantRepository.create(
        db=db_session,
        post_id=post2.id,
        platform="telegram",
        content="Variant for Post Two",
    )

    retrieved = VariantRepository.get_by_id(db_session, variant.id)
    assert retrieved is not None
    assert retrieved.post_id == post2.id
    assert retrieved.post.id == post2.id
    assert retrieved.post.title == "Post Two"
    assert retrieved.post.id != post1.id


def test_4_schedule_slot_references_correct_variant(db_session: Session):
    """4. A schedule slot references the correct variant."""
    post = PostRepository.create(db=db_session, title="Scheduled Post", source_content="Content")
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="telegram",
        content="Approved variant text",
        status=VariantStatus.APPROVED,
    )

    slot_time = datetime.now(timezone.utc) + timedelta(hours=2)
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=variant.id,
        scheduled_at=slot_time,
        idempotency_key="idemp-slot-001",
        status=ScheduleSlotStatus.PENDING,
    )

    retrieved = ScheduleSlotRepository.get_by_id(db_session, slot.id)
    assert retrieved is not None
    assert retrieved.variant_id == variant.id
    assert retrieved.variant.content == "Approved variant text"
    assert retrieved.variant.post.title == "Scheduled Post"


def test_5_publish_attempts_associated_with_correct_schedule_slot(db_session: Session):
    """5. Publish attempts are associated with the correct schedule slot."""
    post = PostRepository.create(db=db_session, title="Publish Post", source_content="Content")
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="mock_x",
        content="X content",
        status=VariantStatus.APPROVED,
    )
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=variant.id,
        scheduled_at=datetime.now(timezone.utc),
        idempotency_key="idemp-slot-publish-1",
    )

    # First attempt: failed
    attempt1 = PublishAttemptRepository.create(
        db=db_session,
        schedule_slot_id=slot.id,
        attempt_number=1,
        status=PublishAttemptStatus.FAILED,
        error_message="Network timeout",
    )

    # Second attempt: succeeded
    attempt2 = PublishAttemptRepository.create(
        db=db_session,
        schedule_slot_id=slot.id,
        attempt_number=2,
        status=PublishAttemptStatus.SUCCEEDED,
        external_post_id="ext-x-12345",
    )

    slot_with_attempts = ScheduleSlotRepository.get_with_attempts(db_session, slot.id)
    assert slot_with_attempts is not None
    assert len(slot_with_attempts.publish_attempts) == 2
    assert slot_with_attempts.publish_attempts[0].attempt_number == 1
    assert slot_with_attempts.publish_attempts[0].status == PublishAttemptStatus.FAILED
    assert slot_with_attempts.publish_attempts[1].attempt_number == 2
    assert slot_with_attempts.publish_attempts[1].status == PublishAttemptStatus.SUCCEEDED
    assert slot_with_attempts.publish_attempts[1].external_post_id == "ext-x-12345"


def test_6_invalid_foreign_key_references_rejected(db_session: Session):
    """6. Invalid foreign-key references are rejected."""
    # Attempt to insert a Variant referencing a non-existent Post ID
    invalid_variant = Variant(
        post_id=999999,  # Does not exist
        platform="telegram",
        content="Orphan variant",
        status=VariantStatus.DRAFT,
    )
    db_session.add(invalid_variant)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()

    # Attempt to insert a ScheduleSlot referencing a non-existent Variant ID
    invalid_slot = ScheduleSlot(
        variant_id=888888,  # Does not exist
        scheduled_at=datetime.now(timezone.utc),
        idempotency_key="idemp-nonexistent",
        status=ScheduleSlotStatus.PENDING,
    )
    db_session.add(invalid_slot)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_7_duplicate_idempotency_keys_rejected(db_session: Session):
    """7. Duplicate idempotency keys are rejected by the database."""
    post = PostRepository.create(db=db_session, title="Idempotency Test", source_content="Content")
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="telegram",
        content="Content",
    )

    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(days=1)

    # First slot with key
    ScheduleSlotRepository.create(
        db=db_session,
        variant_id=variant.id,
        scheduled_at=t1,
        idempotency_key="unique-idemp-key-001",
    )

    # Second slot with duplicate key
    with pytest.raises(IntegrityError):
        ScheduleSlotRepository.create(
            db=db_session,
            variant_id=variant.id,
            scheduled_at=t2,
            idempotency_key="unique-idemp-key-001",  # Duplicate key
        )

    db_session.rollback()


def test_8_duplicate_variant_time_slots_rejected(db_session: Session):
    """8. Duplicate variant/time slots are rejected where the schema enforces that constraint."""
    post = PostRepository.create(db=db_session, title="Time Slot Test", source_content="Content")
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="telegram",
        content="Content",
    )

    scheduled_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

    # First slot for this variant and timestamp
    ScheduleSlotRepository.create(
        db=db_session,
        variant_id=variant.id,
        scheduled_at=scheduled_time,
        idempotency_key="slot-key-a",
    )

    # Second slot for the SAME variant and SAME timestamp, but different idempotency key
    with pytest.raises(IntegrityError):
        ScheduleSlotRepository.create(
            db=db_session,
            variant_id=variant.id,
            scheduled_at=scheduled_time,
            idempotency_key="slot-key-b",
        )

    db_session.rollback()


def test_9_status_fields_reject_invalid_values(db_session: Session):
    """9. Status fields reject invalid values through the appropriate validation layer."""
    post = PostRepository.create(db=db_session, title="Enum Test", source_content="Content")

    # Variant status validation
    with pytest.raises(ValueError, match="Invalid VariantStatus"):
        Variant(
            post_id=post.id,
            platform="telegram",
            content="Content",
            status="not_a_real_status",  # Invalid
        )

    # ScheduleSlot status validation
    with pytest.raises(ValueError, match="Invalid ScheduleSlotStatus"):
        ScheduleSlot(
            variant_id=1,
            scheduled_at=datetime.now(timezone.utc),
            idempotency_key="slot-dummy",
            status="unknown_slot_status",  # Invalid
        )

    # PublishAttempt status validation
    with pytest.raises(ValueError, match="Invalid PublishAttemptStatus"):
        PublishAttempt(
            schedule_slot_id=1,
            attempt_number=1,
            status="super_succeeded",  # Invalid
        )


def test_10_database_transactions_rollback_correctly_after_failure(db_session: Session):
    """10. Database transactions roll back correctly after a failure."""
    # Commit initial state
    post1 = PostRepository.create(
        db=db_session,
        title="Committed Post",
        source_content="Safe content",
    )
    initial_count = len(PostRepository.list_all(db_session))
    assert initial_count >= 1

    # Attempt a batch that contains a valid post and then an invalid entity that violates constraints
    try:
        # Uncommitted valid post
        uncommitted_post = Post(title="Uncommitted Post", source_content="Will be rolled back")
        db_session.add(uncommitted_post)

        # Deliberately invalid variant with non-existent foreign key
        invalid_variant = Variant(
            post_id=999999,
            platform="telegram",
            content="Orphan variant",
            status=VariantStatus.DRAFT,
        )
        db_session.add(invalid_variant)

        db_session.commit()
    except IntegrityError:
        db_session.rollback()

    # Verify that the uncommitted post was NOT persisted due to rollback
    current_posts = PostRepository.list_all(db_session)
    assert len(current_posts) == initial_count
    titles = [p.title for p in current_posts]
    assert "Uncommitted Post" not in titles
    assert "Committed Post" in titles


def test_11_cascading_delete_removes_descendants(db_session: Session):
    """Verify that deleting a Post cascades to its variants, slots, and attempts."""
    post = PostRepository.create(db=db_session, title="Cascade Root", source_content="Content")
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="telegram",
        content="Content",
    )
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=variant.id,
        scheduled_at=datetime.now(timezone.utc),
        idempotency_key="cascade-slot-key",
    )
    attempt = PublishAttemptRepository.create(
        db=db_session,
        schedule_slot_id=slot.id,
        attempt_number=1,
    )

    post_id = post.id
    variant_id = variant.id
    slot_id = slot.id
    attempt_id = attempt.id

    # Delete the parent post
    deleted = PostRepository.delete(db_session, post_id)
    assert deleted is True

    # Check that post and all descendants are removed
    assert PostRepository.get_by_id(db_session, post_id) is None
    assert VariantRepository.get_by_id(db_session, variant_id) is None
    assert ScheduleSlotRepository.get_by_id(db_session, slot_id) is None
    assert PublishAttemptRepository.get_by_id(db_session, attempt_id) is None


def test_12_repository_status_and_content_updates(db_session: Session):
    """Verify repository methods for updating status and content."""
    post = PostRepository.create(db=db_session, title="Update Test", source_content="Content")
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="telegram",
        content="Original content",
    )
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=variant.id,
        scheduled_at=datetime.now(timezone.utc),
        idempotency_key="slot-update-key",
    )
    attempt = PublishAttemptRepository.create(
        db=db_session,
        schedule_slot_id=slot.id,
        attempt_number=1,
    )

    # Variant status update
    updated_v = VariantRepository.update_status(db_session, variant.id, VariantStatus.APPROVED)
    assert updated_v.status == VariantStatus.APPROVED

    # Variant content update
    updated_v2 = VariantRepository.update_content(
        db_session,
        variant.id,
        content="Edited content",
        validation_errors={"warnings": ["tone is casual"]},
    )
    assert updated_v2.content == "Edited content"
    assert updated_v2.validation_errors == {"warnings": ["tone is casual"]}

    # Slot status update
    updated_slot = ScheduleSlotRepository.update_status(
        db_session,
        slot.id,
        ScheduleSlotStatus.COMPLETED,
    )
    assert updated_slot.status == ScheduleSlotStatus.COMPLETED

    # Attempt success mark
    updated_attempt = PublishAttemptRepository.mark_success(
        db_session,
        attempt.id,
        external_post_id="tg_msg_99",
    )
    assert updated_attempt.status == PublishAttemptStatus.SUCCEEDED
    assert updated_attempt.external_post_id == "tg_msg_99"
    assert updated_attempt.finished_at is not None


def test_13_duplicate_attempt_numbers_rejected(db_session: Session):
    """Verify that duplicate attempt numbers for the same slot are rejected."""
    post = PostRepository.create(db=db_session, title="Attempt Post", source_content="Content")
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="mock_x",
        content="Content",
    )
    slot = ScheduleSlotRepository.create(
        db=db_session,
        variant_id=variant.id,
        scheduled_at=datetime.now(timezone.utc),
        idempotency_key="attempt-slot-key",
    )

    # Attempt 1
    PublishAttemptRepository.create(
        db=db_session,
        schedule_slot_id=slot.id,
        attempt_number=1,
    )

    # Duplicate Attempt 1 for the same slot
    with pytest.raises(IntegrityError):
        PublishAttemptRepository.create(
            db=db_session,
            schedule_slot_id=slot.id,
            attempt_number=1,
        )

    db_session.rollback()

