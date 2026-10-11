from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class PublishResult:
    """Normalized outcome returned by any SocialPublisher implementation."""

    success: bool
    platform: str
    external_post_id: Optional[str] = None
    published_at: Optional[datetime] = None
    preview: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    is_uncertain: bool = False


class SocialPublisher(ABC):
    """Abstract interface defining the contract for social media publishing adapters.

    All platform adapters (TelegramPublisher, MockXPublisher, MockLinkedInPublisher)
    must implement this common contract. Decoupled from FastAPI and HTTP controllers.
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """The identifier of the platform (e.g., 'mock_x', 'mock_linkedin', 'telegram')."""
        pass

    @abstractmethod
    def publish(
        self,
        content: str,
        **kwargs: Any,
    ) -> PublishResult:
        """Publish content through the platform adapter.

        Args:
            content: The text content of the variant to publish.
            **kwargs: Adapter-specific parameters (e.g. database session, idempotency_key).

        Returns:
            Normalized PublishResult detailing success/failure, post ID, and preview.
        """
        pass
