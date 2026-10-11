from typing import Dict, List, Optional, Type

from app.adapters.mock_linkedin import MockLinkedInPublisher
from app.adapters.mock_x import MockXPublisher
from app.adapters.publisher_base import SocialPublisher
from app.adapters.telegram import TelegramPublisher
from app.config import get_settings


class UnsupportedPublisherError(Exception):
    """Raised when an unknown publisher platform identifier is requested."""
    pass


class PublisherRegistry:
    """Factory and registry for selecting SocialPublisher adapters through configuration."""

    _ADAPTER_MAP: Dict[str, Type[SocialPublisher]] = {
        "mock_x": MockXPublisher,
        "mock_linkedin": MockLinkedInPublisher,
        "telegram": TelegramPublisher,
    }

    @classmethod
    def get_publisher(cls, platform_name: Optional[str] = None) -> SocialPublisher:
        """Instantiate a publisher adapter by platform name or configured default.

        Args:
            platform_name: Target platform identifier. If None, falls back to
                DEFAULT_PUBLISHER environment variable setting.

        Returns:
            An instance implementing SocialPublisher.

        Raises:
            UnsupportedPublisherError: If the platform identifier is not recognized.
        """
        settings = get_settings()
        target = (platform_name or settings.DEFAULT_PUBLISHER).strip().lower()

        adapter_cls = cls._ADAPTER_MAP.get(target)
        if not adapter_cls:
            supported = sorted(list(cls._ADAPTER_MAP.keys()))
            raise UnsupportedPublisherError(
                f"Unknown publisher adapter '{target}'. Configured supported adapters: {supported}"
            )

        return adapter_cls()

    @classmethod
    def get_default_publisher(cls) -> SocialPublisher:
        """Instantiate the default publisher configured in settings."""
        return cls.get_publisher(None)

    @classmethod
    def list_supported_publishers(cls) -> List[str]:
        """Return list of supported adapter names."""
        return sorted(list(cls._ADAPTER_MAP.keys()))
