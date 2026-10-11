from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PlatformProfile:
    """Constraint and styling profile for a social media platform."""

    platform_id: str
    display_name: str
    max_length: int
    max_hashtags: int
    tone: str
    tone_description: str
    required_tone_features: List[str] = field(default_factory=list)


PLATFORM_PROFILES: Dict[str, PlatformProfile] = {
    "mock_x": PlatformProfile(
        platform_id="mock_x",
        display_name="Mock X (Twitter)",
        max_length=280,
        max_hashtags=3,
        tone="concise and conversational",
        tone_description="Short, punchy summary with a casual or engaging call-to-action.",
        required_tone_features=["conversational"],
    ),
    "mock_linkedin": PlatformProfile(
        platform_id="mock_linkedin",
        display_name="Mock LinkedIn",
        max_length=3000,
        max_hashtags=5,
        tone="professional and informative",
        tone_description="Detailed, structured professional insights with a question to prompt discussion.",
        required_tone_features=["professional", "discussion_prompt"],
    ),
    "telegram": PlatformProfile(
        platform_id="telegram",
        display_name="Telegram",
        max_length=4096,
        max_hashtags=5,
        tone="informative and readable",
        tone_description="Structured, readable post with clear headings, bullets, and concluding tags.",
        required_tone_features=["readable", "informative"],
    ),
}


def get_platform_profile(platform: str) -> Optional[PlatformProfile]:
    """Retrieve the constraint profile for a specific platform."""
    return PLATFORM_PROFILES.get(platform)


def get_all_supported_platforms() -> List[str]:
    """Return all configured platform identifiers."""
    return list(PLATFORM_PROFILES.keys())
