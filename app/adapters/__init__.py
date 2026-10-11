"""Social platform publishing adapters."""
from app.adapters.mock_linkedin import MockLinkedInPublisher
from app.adapters.mock_x import MockXPublisher
from app.adapters.publisher_base import PublishResult, SocialPublisher
from app.adapters.registry import PublisherRegistry, UnsupportedPublisherError
from app.adapters.telegram import TelegramPublisher

__all__ = [
    "SocialPublisher",
    "PublishResult",
    "MockXPublisher",
    "MockLinkedInPublisher",
    "TelegramPublisher",
    "PublisherRegistry",
    "UnsupportedPublisherError",
]
