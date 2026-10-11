import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.validators.profiles import PlatformProfile, get_platform_profile


@dataclass
class ValidationResult:
    """Outcome of validating a variant against platform constraints."""

    platform: str
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    character_count: int = 0
    hashtag_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert validation result to a dictionary suitable for JSON storage."""
        return asdict(self)


class VariantValidator:
    """Service validating variant text against explicit platform constraints."""

    @staticmethod
    def extract_hashtags(content: str) -> List[str]:
        """Extract all hashtag tokens (e.g., #Python, #Linux) from content."""
        return re.findall(r"#\w+", content)

    @classmethod
    def validate(cls, platform: str, content: str) -> ValidationResult:
        """Validate content against a target platform profile.

        Performs deterministic checks:
        1. Maximum character length.
        2. Maximum hashtag count.
        3. Explicit, rule-based tone checks.
        """
        profile = get_platform_profile(platform)
        if not profile:
            return ValidationResult(
                platform=platform,
                is_valid=False,
                errors=[f"Unknown platform '{platform}'. No constraint profile available."],
            )

        errors: List[str] = []
        warnings: List[str] = []

        char_count = len(content)
        hashtags = cls.extract_hashtags(content)
        hashtag_count = len(hashtags)

        # 1. Length constraint
        if char_count > profile.max_length:
            errors.append(
                f"Content exceeds maximum length of {profile.max_length} characters "
                f"(current length: {char_count})."
            )

        if char_count == 0 or content.strip() == "":
            errors.append("Content cannot be empty.")

        # 2. Hashtag constraint
        if hashtag_count > profile.max_hashtags:
            errors.append(
                f"Content exceeds maximum of {profile.max_hashtags} hashtags "
                f"(found {hashtag_count}: {', '.join(hashtags)})."
            )

        # 3. Explicit tone rules
        cls._check_tone_rules(platform, content, errors, warnings)

        return ValidationResult(
            platform=platform,
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            character_count=char_count,
            hashtag_count=hashtag_count,
        )

    @classmethod
    def _check_tone_rules(
        cls,
        platform: str,
        content: str,
        errors: List[str],
        warnings: List[str],
    ) -> None:
        """Evaluate explicit, reproducible heuristics for platform tone."""
        lower = content.lower()

        if platform == "mock_x":
            # Conversational check: Should have conversational markers or CTA or question mark
            conversational_markers = [
                "?", "check", "what", "how", "explore", "thoughts", "tip",
                "read", "dive", "learn", "see", "here's", "why", "let's"
            ]
            has_marker = any(marker in lower for marker in conversational_markers)
            if not has_marker:
                errors.append(
                    "Tone violation: Mock X content must be conversational and include an engaging "
                    "call-to-action or prompt (e.g. question, tip, or invite)."
                )

        elif platform == "mock_linkedin":
            # Professional check: Avoid vulgarity or excessive shouting
            if re.search(r"[A-Z]{5,}\b", content):
                errors.append(
                    "Tone violation: Mock LinkedIn content must be professional and avoid "
                    "excessive uppercase words/shouting."
                )

            # Discussion prompt: Must include question mark or discussion invitation
            discussion_markers = [
                "?", "thoughts", "agree", "perspective", "experience", "what do you think",
                "how do you", "join the conversation", "discussion"
            ]
            has_discussion = any(marker in lower for marker in discussion_markers)
            if not has_discussion:
                errors.append(
                    "Tone violation: Mock LinkedIn content must include a discussion-oriented "
                    "prompt or question to encourage professional engagement."
                )

        elif platform == "telegram":
            # Readability check: If longer than 200 characters, require paragraph breaks (\n)
            if len(content) > 200 and "\n" not in content:
                errors.append(
                    "Tone violation: Telegram content must be readable with paragraph formatting "
                    "or line breaks rather than a single dense block of text."
                )
