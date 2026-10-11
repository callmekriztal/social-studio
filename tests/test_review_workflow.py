import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import Platform, VariantStatus
from app.models.post import Post
from app.models.variant import Variant
from app.repositories.post_repository import PostRepository
from app.repositories.variant_repository import VariantRepository
from app.services.scheduling_guard import (
    SchedulingEligibilityError,
    SchedulingEligibilityGuard,
)


@pytest.fixture
def sample_post(db_session: Session) -> Post:
    """Fixture providing a persisted Post."""
    return PostRepository.create(
        db=db_session,
        title="Review Workflow Post",
        source_content="Content about system design and review workflows.",
    )


# 1. Listing draft variants with filters
def test_1_list_draft_variants(client: TestClient, db_session: Session, sample_post: Post):
    # Create a draft variant and an approved variant
    v_draft_x = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Draft for X? Check it out! #Tech",
        status=VariantStatus.DRAFT,
    )
    v_draft_tg = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="telegram",
        content="📌 Telegram draft\n\nInfo.\n\n#Tech",
        status=VariantStatus.DRAFT,
    )
    v_approved = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Approved post? Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )

    # Filter status=draft
    res = client.get("/api/variants?status=draft")
    assert res.status_code == 200
    data = res.json()
    ids = [item["id"] for item in data]
    assert v_draft_x.id in ids
    assert v_draft_tg.id in ids
    assert v_approved.id not in ids

    # Filter status=draft and platform=telegram
    res_tg = client.get("/api/variants?status=draft&platform=telegram")
    assert res_tg.status_code == 200
    data_tg = res_tg.json()
    assert all(item["platform"] == "telegram" for item in data_tg)
    assert any(item["id"] == v_draft_tg.id for item in data_tg)


# 2. Retrieving a single variant
def test_2_retrieve_variant(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Content to retrieve? #Tech",
    )

    res = client.get(f"/api/variants/{v.id}")
    assert res.status_code == 200
    assert res.json()["id"] == v.id
    assert res.json()["platform"] == "mock_x"
    assert res.json()["status"] == "draft"


# 3. Editing a draft variant
def test_3_edit_draft_variant(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Original draft content? #Tech",
        status=VariantStatus.DRAFT,
    )

    new_content = "Updated social copy with question? Explore here! #Linux #Dev"
    res = client.patch(f"/api/variants/{v.id}", json={"content": new_content})
    assert res.status_code == 200

    data = res.json()
    assert data["content"] == new_content
    assert data["status"] == "draft"
    assert data["validation_errors"] is None


# 4. Editing an approved variant resets it to draft
def test_4_edit_approved_variant_resets_to_draft(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Already approved text? #Tech",
        status=VariantStatus.APPROVED,
    )

    new_content = "Revised content needs re-review? Check it out! #Tech"
    res = client.patch(f"/api/variants/{v.id}", json={"content": new_content})
    assert res.status_code == 200

    data = res.json()
    assert data["content"] == new_content
    # CRITICAL: Automatically reverted to draft
    assert data["status"] == "draft"


# 5. Invalid edits are rejected
def test_5_invalid_edits_rejected_with_422(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Valid draft? Check it out! #Tech",
        status=VariantStatus.DRAFT,
    )

    # Violates max length (280) and max hashtags (3)
    oversized = "X" * 300 + " #one #two #three #four #five"
    res = client.patch(f"/api/variants/{v.id}", json={"content": oversized})
    assert res.status_code == 422
    assert "errors" in res.json()["detail"]
    assert len(res.json()["detail"]["errors"]) >= 2

    # Verify database record was NOT updated
    unchanged = VariantRepository.get_by_id(db_session, v.id)
    assert unchanged.content == "Valid draft? Check it out! #Tech"


# 6. Valid draft variants can be approved
def test_6_valid_draft_can_be_approved(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Ready to be approved? Check it out! #Tech",
        status=VariantStatus.DRAFT,
    )

    res = client.post(f"/api/variants/{v.id}/approve")
    assert res.status_code == 200

    data = res.json()
    assert data["status"] == "approved"
    assert data["validation_errors"] is None


# 7. Invalid variants cannot be approved
def test_7_invalid_variants_cannot_be_approved(client: TestClient, db_session: Session, sample_post: Post):
    # Store an invalid variant directly in database
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Invalid because no conversational marker and text too long: " + "X" * 290,
        status=VariantStatus.DRAFT,
    )

    res = client.post(f"/api/variants/{v.id}/approve")
    assert res.status_code == 422
    assert "errors" in res.json()["detail"]

    # Verify it was NOT approved in the database
    db_var = VariantRepository.get_by_id(db_session, v.id)
    assert db_var.status == VariantStatus.DRAFT


# 8. Draft variants can be rejected
def test_8_draft_variant_can_be_rejected(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Draft to reject? #Tech",
        status=VariantStatus.DRAFT,
    )

    res = client.post(
        f"/api/variants/{v.id}/reject",
        json={"reason": "Tone is not quite right for our brand."},
    )
    assert res.status_code == 200

    data = res.json()
    assert data["status"] == "rejected"
    assert data["rejection_reason"] == "Tone is not quite right for our brand."


# 9. Approved variants can be rejected
def test_9_approved_variant_can_be_rejected(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Approved previously? Check it out! #Tech",
        status=VariantStatus.APPROVED,
    )

    res = client.post(
        f"/api/variants/{v.id}/reject",
        json={"reason": "Topic cancelled by marketing."},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    assert res.json()["rejection_reason"] == "Topic cancelled by marketing."


# 10. Rejected variants can be returned to draft
def test_10_rejected_variant_returned_to_draft(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Needs revision? Check it out! #Tech",
        status=VariantStatus.REJECTED,
    )
    v.rejection_reason = "Initial rejection reason"
    db_session.commit()

    res = client.post(f"/api/variants/{v.id}/revise")
    assert res.status_code == 200

    data = res.json()
    assert data["status"] == "draft"
    assert data["rejection_reason"] is None


# 11. Published variants cannot be edited or transitioned
def test_11_published_variants_cannot_be_edited_or_transitioned(client: TestClient, db_session: Session, sample_post: Post):
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Published variant immutable copy? #Tech",
        status=VariantStatus.PUBLISHED,
    )

    # Attempt to edit
    res_edit = client.patch(f"/api/variants/{v.id}", json={"content": "New text? #Tech"})
    assert res_edit.status_code == 400
    assert "published" in res_edit.json()["detail"].lower()

    # Attempt to approve
    res_app = client.post(f"/api/variants/{v.id}/approve")
    assert res_app.status_code == 400

    # Attempt to reject
    res_rej = client.post(f"/api/variants/{v.id}/reject")
    assert res_rej.status_code == 400

    # Attempt to revise
    res_rev = client.post(f"/api/variants/{v.id}/revise")
    assert res_rev.status_code == 400


# 12. Draft, rejected, published, and invalid variants fail scheduling eligibility guard
def test_12_ineligible_variants_fail_scheduling_guard(db_session: Session, sample_post: Post):
    # 1. Draft variant fails
    v_draft = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Draft copy? Check it out! #Tech",
        status=VariantStatus.DRAFT,
    )
    eligible, reason = SchedulingEligibilityGuard.check_eligibility(v_draft)
    assert eligible is False
    assert "DRAFT" in reason

    with pytest.raises(SchedulingEligibilityError):
        SchedulingEligibilityGuard.assert_eligible_for_scheduling(v_draft)

    # 2. Rejected variant fails
    v_rejected = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Rejected copy? Check it out! #Tech",
        status=VariantStatus.REJECTED,
    )
    eligible, reason = SchedulingEligibilityGuard.check_eligibility(v_rejected)
    assert eligible is False
    assert "REJECTED" in reason

    # 3. Published variant fails
    v_published = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Already published? Check it out! #Tech",
        status=VariantStatus.PUBLISHED,
    )
    eligible, reason = SchedulingEligibilityGuard.check_eligibility(v_published)
    assert eligible is False
    assert "PUBLISHED" in reason

    # 4. Invalid approved variant fails
    v_invalid_approved = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Too long " + "A" * 300,
        status=VariantStatus.APPROVED,
    )
    eligible, reason = SchedulingEligibilityGuard.check_eligibility(v_invalid_approved)
    assert eligible is False
    assert "violates platform constraints" in reason


# 13. Only valid approved variants pass the guard
def test_13_valid_approved_variant_passes_scheduling_guard(db_session: Session, sample_post: Post):
    v_valid_approved = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Clean and valid copy? Check it out! #Tech #Dev",
        status=VariantStatus.APPROVED,
    )

    eligible, reason = SchedulingEligibilityGuard.check_eligibility(v_valid_approved)
    assert eligible is True
    assert reason is None

    # Does not raise
    SchedulingEligibilityGuard.assert_eligible_for_scheduling(v_valid_approved)


# 14. Missing variant IDs return 404
def test_14_missing_variant_returns_404(client: TestClient):
    assert client.get("/api/variants/999999").status_code == 404
    assert client.patch("/api/variants/999999", json={"content": "Sample? #Tech"}).status_code == 404
    assert client.post("/api/variants/999999/approve").status_code == 404
    assert client.post("/api/variants/999999/reject").status_code == 404
    assert client.post("/api/variants/999999/revise").status_code == 404


# 15. Failed operations do not leave partially updated database records
def test_15_failed_operations_do_not_leave_partially_updated_records(client: TestClient, db_session: Session, sample_post: Post):
    original_text = "Good approved text? Check it out! #Tech"
    v = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content=original_text,
        status=VariantStatus.APPROVED,
    )

    # Submit an invalid edit that will trigger 422
    bad_edit = "Invalid edit with too many tags #one #two #three #four #five"
    res = client.patch(f"/api/variants/{v.id}", json={"content": bad_edit})
    assert res.status_code == 422

    # Ensure record remains APPROVED with original content intact
    refreshed = VariantRepository.get_by_id(db_session, v.id)
    assert refreshed.content == original_text
    assert refreshed.status == VariantStatus.APPROVED
