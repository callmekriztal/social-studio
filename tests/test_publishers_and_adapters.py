import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.mock_linkedin import MockLinkedInPublisher
from app.adapters.mock_x import MockXPublisher
from app.adapters.publisher_base import PublishResult, SocialPublisher
from app.adapters.registry import PublisherRegistry, UnsupportedPublisherError
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
from app.services.publishing_service import PublishingService
from app.services.scheduling_guard import (
    SchedulingEligibilityError,
    SchedulingEligibilityGuard,
)


@pytest.fixture
def sample_post(db_session: Session) -> Post:
    """Fixture providing a persisted Post."""
    return PostRepository.create(
        db=db_session,
        title="Publisher Test Post",
        source_content="Content about multi-platform social media publishing.",
    )


@pytest.fixture
def approved_x_variant(db_session: Session, sample_post: Post) -> Variant:
    """Fixture providing an approved valid X variant meeting tone rules."""
    return VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Check out this test publishing on X! Thoughts? #Tech",
        status=VariantStatus.APPROVED,
    )


@pytest.fixture
def approved_linkedin_variant(db_session: Session, sample_post: Post) -> Variant:
    """Fixture providing an approved valid LinkedIn variant meeting tone rules."""
    return VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_linkedin",
        content="Testing publishing on LinkedIn. What are your thoughts on this? #Leadership #Engineering",
        status=VariantStatus.APPROVED,
    )


@pytest.fixture
def approved_telegram_variant(db_session: Session, sample_post: Post) -> Variant:
    """Fixture providing an approved valid Telegram variant."""
    return VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="telegram",
        content="📢 Testing Telegram broadcast.\n\nKey takeaways from the engineering team.\n\n#Tech",
        status=VariantStatus.APPROVED,
    )


# 1. Mock publishers implement SocialPublisher interface
def test_1_mock_publishers_implement_social_publisher_interface():
    mock_x = MockXPublisher()
    mock_li = MockLinkedInPublisher()
    telegram = TelegramPublisher(bot_token="token", chat_id="123")

    assert isinstance(mock_x, SocialPublisher)
    assert isinstance(mock_li, SocialPublisher)
    assert isinstance(telegram, SocialPublisher)
    assert hasattr(mock_x, "publish")
    assert hasattr(mock_li, "publish")
    assert hasattr(telegram, "publish")


# 2. MockXPublisher successfully 'publishes' and stores post
def test_2_mock_x_publisher_stores_post(db_session: Session):
    publisher = MockXPublisher()
    content = "Check out breaking tech news from Studio! Thoughts? #Tech"
    key = f"test-key-{uuid.uuid4().hex[:6]}"

    result = publisher.publish(content=content, idempotency_key=key, db=db_session)

    assert result.success is True
    assert result.platform == "mock_x"
    assert result.external_post_id is not None
    assert result.external_post_id.startswith("mock_x_")
    assert result.error is None
    assert result.published_at is not None

    # Verify persistence in mock_platform_posts
    saved_posts = MockPostRepository.list_by_platform(db_session, "mock_x")
    matching = [p for p in saved_posts if p.mock_post_id == result.external_post_id]
    assert len(matching) == 1
    assert matching[0].content == content
    assert matching[0].mock_post_id == result.external_post_id


# 3. MockLinkedInPublisher successfully 'publishes' and stores post
def test_3_mock_linkedin_publisher_stores_post(db_session: Session):
    publisher = MockLinkedInPublisher()
    content = "Insights on engineering architecture and scaling. What are your thoughts? #Leadership"
    key = f"test-key-{uuid.uuid4().hex[:6]}"

    result = publisher.publish(content=content, idempotency_key=key, db=db_session)

    assert result.success is True
    assert result.platform == "mock_linkedin"
    assert result.external_post_id is not None
    assert result.external_post_id.startswith("urn:li:share:mock_")
    assert result.error is None
    assert result.published_at is not None

    # Verify persistence in mock_platform_posts
    saved_posts = MockPostRepository.list_by_platform(db_session, "mock_linkedin")
    matching = [p for p in saved_posts if p.mock_post_id == result.external_post_id]
    assert len(matching) == 1
    assert matching[0].content == content
    assert matching[0].mock_post_id == result.external_post_id


# 4. Mock publishers return appropriate previews and mock IDs
def test_4_mock_publishers_return_previews_and_mock_ids(db_session: Session):
    mock_x = MockXPublisher()
    mock_li = MockLinkedInPublisher()

    res_x = mock_x.publish(content="Sample tweet #X", db=db_session)
    res_li = mock_li.publish(content="Sample LinkedIn post #Career", db=db_session)

    assert "[Mock X Post Preview]" in res_x.preview
    assert "Sample tweet #X" in res_x.preview
    assert "mock_x_" in res_x.external_post_id

    assert "[Mock LinkedIn Feed Preview]" in res_li.preview
    assert "Sample LinkedIn post #Career" in res_li.preview
    assert "urn:li:share:mock_" in res_li.external_post_id


# 5. TelegramPublisher correctly constructs Bot API request payload
def test_5_telegram_publisher_constructs_request_payload():
    publisher = TelegramPublisher(bot_token="test_token_123", chat_id="-100987654321")
    content = "<b>Weekly Update</b>\n\nContent here."

    with patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {
                "message_id": 4242,
                "date": 1710000000,
                "chat": {"id": -100987654321},
            },
        }
        mock_client.post.return_value = mock_resp

        result = publisher.publish(content=content)

        assert mock_client.post.called
        call_args, call_kwargs = mock_client.post.call_args
        assert call_args[0] == "https://api.telegram.org/bottest_token_123/sendMessage"
        json_data = call_kwargs["json"]
        assert json_data["chat_id"] == "-100987654321"
        assert json_data["text"] == content
        assert result.success is True


# 6. TelegramPublisher handles successful API response (parses message_id)
def test_6_telegram_publisher_handles_successful_response():
    publisher = TelegramPublisher(bot_token="test_token", chat_id="12345")
    mock_response_data = {
        "ok": True,
        "result": {
            "message_id": 88123,
            "date": 1700000000,
            "chat": {"id": 12345, "title": "Test Channel"},
        },
    }

    with patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data
        mock_client.post.return_value = mock_resp

        result = publisher.publish(content="Hello Telegram!")

        assert result.success is True
        assert result.platform == "telegram"
        assert result.external_post_id == "88123"
        assert result.error is None
        assert result.published_at is not None
        assert "#88123" in result.preview


# 7. TelegramPublisher handles API errors (400, 403, etc.) cleanly
def test_7_telegram_publisher_handles_api_errors():
    bot_token = "secret_bot_token_xyz"
    publisher = TelegramPublisher(bot_token=bot_token, chat_id="-100123")

    # 400 Bad Request
    with patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
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

        result = publisher.publish(content="Hello!")

        assert result.success is False
        assert "400" in result.error
        assert "chat not found" in result.error
        # Ensure secret token is NEVER in error message
        assert bot_token not in result.error

    # 403 Forbidden
    with patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {
            "ok": False,
            "error_code": 403,
            "description": "Forbidden: bot was kicked from the channel",
        }
        mock_client.post.return_value = mock_resp

        result = publisher.publish(content="Hello!")

        assert result.success is False
        assert "403" in result.error
        assert "bot was kicked" in result.error
        assert bot_token not in result.error


# 8. TelegramPublisher handles network errors / timeouts cleanly
def test_8_telegram_publisher_handles_network_errors_and_timeouts():
    publisher = TelegramPublisher(bot_token="token", chat_id="123")

    # Timeout
    with patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("Read timed out")

        result = publisher.publish(content="Hello!")
        assert result.success is False
        assert "timed out" in result.error

    # Network connect error
    with patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("Failed to resolve host")

        result = publisher.publish(content="Hello!")
        assert result.success is False
        assert "connection error" in result.error


# 9. TelegramPublisher with missing credentials fails cleanly without affecting mock publishers
def test_9_telegram_missing_credentials_fails_cleanly(db_session: Session):
    empty_telegram = TelegramPublisher(bot_token="", chat_id="")
    result = empty_telegram.publish(content="Hello Telegram")

    assert result.success is False
    assert "Telegram configuration missing" in result.error

    # Mock publishers remain completely unaffected
    mock_x = MockXPublisher()
    mock_li = MockLinkedInPublisher()
    res_x = mock_x.publish(content="Works regardless! Thoughts? #Tech", db=db_session)
    res_li = mock_li.publish(content="Works regardless too! Thoughts? #Work", db=db_session)
    assert res_x.success is True
    assert res_li.success is True


# 10. Unknown publisher name rejected with configuration error
def test_10_unknown_publisher_name_rejected(client: TestClient, db_session: Session, approved_x_variant: Variant):
    # Direct registry lookup
    with pytest.raises(UnsupportedPublisherError) as exc_info:
        PublisherRegistry.get_publisher("facebook")
    assert "Unknown publisher adapter 'facebook'" in str(exc_info.value)

    # API publishing endpoint with unknown platform override
    res = client.post(f"/api/variants/{approved_x_variant.id}/publish?platform=facebook")
    assert res.status_code == 400
    assert "Unknown publisher adapter 'facebook'" in res.json()["detail"]


# 11. Publisher selection can be changed via config without modifying business logic
def test_11_publisher_selection_changed_via_config():
    with patch.object(settings, "DEFAULT_PUBLISHER", "mock_linkedin"):
        pub = PublisherRegistry.get_default_publisher()
        assert isinstance(pub, MockLinkedInPublisher)
        assert pub.platform_name == "mock_linkedin"

    with patch.object(settings, "DEFAULT_PUBLISHER", "mock_x"):
        pub = PublisherRegistry.get_default_publisher()
        assert isinstance(pub, MockXPublisher)
        assert pub.platform_name == "mock_x"


# 12. Unapproved variant cannot be published (enforce review workflow)
def test_12_unapproved_variant_cannot_be_published(
    client: TestClient, db_session: Session, sample_post: Post
):
    # A. Draft variant
    draft_var = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Draft post that should not publish? #Draft",
        status=VariantStatus.DRAFT,
    )
    res_draft = client.post(f"/api/variants/{draft_var.id}/publish")
    assert res_draft.status_code == 400
    assert "Variant is in DRAFT status" in res_draft.json()["detail"]

    # B. Rejected variant
    rejected_var = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Rejected post? #Rejected",
        status=VariantStatus.REJECTED,
    )
    res_rejected = client.post(f"/api/variants/{rejected_var.id}/publish")
    assert res_rejected.status_code == 400
    assert "Variant is REJECTED" in res_rejected.json()["detail"]

    # C. Variant with validation errors marked invalid
    invalid_var = VariantRepository.create(
        db=db_session,
        post_id=sample_post.id,
        platform="mock_x",
        content="Invalid post content with error and shouting SO MUCH LOUD",
        status=VariantStatus.APPROVED,
    )
    res_invalid = client.post(f"/api/variants/{invalid_var.id}/publish")
    assert res_invalid.status_code == 400
    assert "violates platform constraints" in res_invalid.json()["detail"]


# 13. Published variant cannot be published again
def test_13_published_variant_cannot_be_published_again(
    client: TestClient, db_session: Session, approved_x_variant: Variant
):
    # First publish: should succeed
    res1 = client.post(f"/api/variants/{approved_x_variant.id}/publish")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["success"] is True
    assert data1["external_post_id"] is not None

    # Refresh variant from DB: status must now be 'published'
    db_session.refresh(approved_x_variant)
    assert approved_x_variant.status == VariantStatus.PUBLISHED

    # Second publish attempt: must be rejected
    res2 = client.post(f"/api/variants/{approved_x_variant.id}/publish")
    assert res2.status_code == 400
    assert "already been PUBLISHED" in res2.json()["detail"]


# 14. Failed publishing attempts are recorded in publish_attempts
def test_14_failed_publishing_attempts_recorded(
    client: TestClient, db_session: Session, approved_telegram_variant: Variant
):
    # Configure Telegram credentials and simulate Telegram failure (e.g. 400 chat not found)
    with patch.object(settings, "TELEGRAM_BOT_TOKEN", "mock_bot_token"), \
         patch.object(settings, "TELEGRAM_CHAT_ID", "-100123456"), \
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

        res = client.post(f"/api/variants/{approved_telegram_variant.id}/publish")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["attempt_id"] is not None
        assert "chat not found" in data["error"]

        # Verify PublishAttempt record in DB
        attempt = PublishAttemptRepository.get_by_id(db_session, data["attempt_id"])
        assert attempt is not None
        assert attempt.status == PublishAttemptStatus.FAILED
        assert "chat not found" in attempt.error_message
        assert attempt.started_at is not None
        assert attempt.finished_at is not None

        # Variant status remains APPROVED (eligible for retry later)
        db_session.refresh(approved_telegram_variant)
        assert approved_telegram_variant.status == VariantStatus.APPROVED


# 15. No external network calls are made during tests (all Telegram calls mocked)
def test_15_no_real_external_network_calls():
    publisher = TelegramPublisher(bot_token="test_token", chat_id="123")

    with patch("app.adapters.telegram.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"message_id": 99999, "date": 1700000000},
        }
        mock_client.post.return_value = mock_resp

        result = publisher.publish("Simulated content without real network access")
        assert result.success is True
        assert mock_client.post.called
