from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.post_repository import PostRepository
from app.schemas.post import PostCreateMarkdown, PostCreateUrl, PostResponse
from app.schemas.variant import GenerateVariantsRequest, VariantResponse
from app.services.url_extractor import ExtractionError, SSRError, URLExtractorService
from app.services.variant_generator import (
    PostNotFoundError,
    UnknownPlatformError,
    VariantGenerationService,
)

router = APIRouter(prefix="/posts", tags=["posts"])


@router.post(
    "",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a post from Markdown or plain text",
)
def create_post(
    payload: PostCreateMarkdown,
    db: Session = Depends(get_db),
) -> PostResponse:
    """Ingest a new blog post from Markdown or plain text content."""
    post = PostRepository.create(
        db=db,
        title=payload.title,
        source_content=payload.content,
        source_url=None,
    )
    return PostResponse.model_validate(post)


@router.post(
    "/from-url",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a post from a public URL",
)
def create_post_from_url(
    payload: PostCreateUrl,
    db: Session = Depends(get_db),
) -> PostResponse:
    """Fetch and extract article content from a URL with SSRF protection."""
    try:
        article = URLExtractorService.extract_article(payload.url)
    except SSRError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security violation (SSRF): {e}",
        )
    except ExtractionError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"URL extraction failed: {e}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch content from URL: {e}",
        )

    post = PostRepository.create(
        db=db,
        title=article.title,
        source_content=article.content,
        source_url=article.source_url,
    )
    return PostResponse.model_validate(post)


@router.get(
    "/{post_id}",
    response_model=PostResponse,
    summary="Get post by ID",
)
def get_post(
    post_id: int,
    db: Session = Depends(get_db),
) -> PostResponse:
    """Retrieve an ingested blog post by its ID."""
    post = PostRepository.get_by_id(db, post_id)
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Post with ID {post_id} does not exist.",
        )
    return PostResponse.model_validate(post)


@router.post(
    "/{post_id}/generate",
    response_model=List[VariantResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate social media variants for a post",
)
def generate_variants(
    post_id: int,
    payload: Optional[GenerateVariantsRequest] = None,
    db: Session = Depends(get_db),
) -> List[VariantResponse]:
    """Generate platform-specific social variants from the stored Post source."""
    platforms = payload.platforms if payload else None
    service = VariantGenerationService()

    try:
        variants = service.generate_variants_for_post(
            db=db,
            post_id=post_id,
            target_platforms=platforms,
        )
        return [VariantResponse.model_validate(v) for v in variants]
    except PostNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except UnknownPlatformError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
