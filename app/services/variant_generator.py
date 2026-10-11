import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.enums import VariantStatus
from app.models.post import Post
from app.models.variant import Variant
from app.repositories.post_repository import PostRepository
from app.repositories.variant_repository import VariantRepository
from app.validators.profiles import get_all_supported_platforms, get_platform_profile
from app.validators.validator import VariantValidator


class GeneratorError(Exception):
    """Base error for generation failures."""
    pass


class PostNotFoundError(GeneratorError):
    """Raised when the specified Post ID does not exist."""
    pass


class UnknownPlatformError(GeneratorError):
    """Raised when an unsupported platform identifier is requested."""
    pass


class BaseVariantGenerator(ABC):
    """Abstract contract for variant generation engines."""

    @abstractmethod
    def generate_text(self, platform: str, post: Post) -> str:
        """Produce platform-specific copy from the stored post."""
        pass


class TemplateVariantGenerator(BaseVariantGenerator):
    """Deterministic, template-driven variant generator without external AI dependencies."""

    @staticmethod
    def _extract_summary(text: str, max_words: int = 30) -> str:
        """Extract a clean summary sentence or clause from source text."""
        # Clean markdown headers and extra whitespace
        cleaned = re.sub(r"#+\s*", "", text).strip()
        lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
        first_paragraph = lines[0] if lines else cleaned

        words = first_paragraph.split()
        if len(words) <= max_words:
            return first_paragraph
        return " ".join(words[:max_words]) + "..."

    def generate_text(self, platform: str, post: Post) -> str:
        summary = self._extract_summary(post.source_content, max_words=20)

        if platform == "mock_x":
            # Concise (<=280 chars), conversational with CTA, 2 hashtags
            text = (
                f"{post.title}\n\n"
                f"{summary}\n\n"
                f"What are your thoughts? Check it out!\n"
                f"#Tech #Dev"
            )
            return text

        elif platform == "mock_linkedin":
            # Professional (<=3000 chars), structured, discussion prompt, 3 hashtags
            text = (
                f"Key Takeaways: {post.title}\n\n"
                f"In our latest analysis:\n\n"
                f"{post.source_content.strip()}\n\n"
                f"What are your thoughts on this approach? How do you tackle this in your experience?\n\n"
                f"#Engineering #SoftwareDevelopment #TechLeadership"
            )
            return text

        elif platform == "telegram":
            # Readable (<=4096 chars), formatted with paragraphs and informative header, 3 hashtags
            text = (
                f"📌 {post.title}\n\n"
                f"{post.source_content.strip()}\n\n"
                f"🔗 Source: {post.source_url or 'Original Submission'}\n\n"
                f"#TechNotes #Insights #DailyRead"
            )
            return text

        raise UnknownPlatformError(f"Platform '{platform}' is not supported.")


class VariantGenerationService:
    """Orchestrates variant generation, constraint validation, and persistence."""

    def __init__(self, generator: Optional[BaseVariantGenerator] = None):
        self.generator = generator or TemplateVariantGenerator()

    def generate_variants_for_post(
        self,
        db: Session,
        post_id: int,
        target_platforms: Optional[List[str]] = None,
    ) -> List[Variant]:
        """Generate and validate variants for a given post.

        Guarantees:
        - Source content is strictly read from the database Post record.
        - Validates all target platforms up front.
        - Runs validation on every generated variant.
        - Variants that pass validation are set to DRAFT (review-ready).
        - Variants that fail validation are set to REJECTED (with structured validation_errors).
        - Operates in a single database transaction.
        """
        post = PostRepository.get_by_id(db, post_id)
        if not post:
            raise PostNotFoundError(f"Post with ID {post_id} was not found.")

        platforms = target_platforms or get_all_supported_platforms()
        supported = set(get_all_supported_platforms())

        # Validate platform identifiers up front
        for p in platforms:
            if p not in supported:
                raise UnknownPlatformError(f"Unsupported platform '{p}'. Configured: {sorted(list(supported))}")

        created_variants: List[Variant] = []

        try:
            for platform in platforms:
                generated_content = self.generator.generate_text(platform, post)

                # Validate against platform constraints
                validation = VariantValidator.validate(platform, generated_content)

                if validation.is_valid:
                    status = VariantStatus.DRAFT
                    validation_errors = None
                else:
                    # Enforce that invalid variants are never review-eligible drafts
                    status = VariantStatus.REJECTED
                    validation_errors = validation.to_dict()

                variant = VariantRepository.create(
                    db=db,
                    post_id=post.id,
                    platform=platform,
                    content=generated_content,
                    status=status,
                    validation_errors=validation_errors,
                    commit=False,
                )
                created_variants.append(variant)

            db.commit()
            for v in created_variants:
                db.refresh(v)

            return created_variants

        except Exception:
            db.rollback()
            raise
