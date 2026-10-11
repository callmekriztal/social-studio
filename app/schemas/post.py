from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PostCreateMarkdown(BaseModel):
    """Schema for creating a post from raw markdown or text."""

    title: str = Field(..., min_length=1, description="Blog post title")
    content: str = Field(..., min_length=1, description="Original blog post Markdown or text")

    @field_validator("title", "content")
    @classmethod
    def validate_non_whitespace(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or purely whitespace.")
        return stripped


class PostCreateUrl(BaseModel):
    """Schema for ingesting a post by fetching a URL."""

    url: str = Field(..., min_length=1, description="Public article URL")

    @field_validator("url")
    @classmethod
    def validate_url_format(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("URL cannot be empty.")
        if not (stripped.startswith("http://") or stripped.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")
        return stripped


class PostResponse(BaseModel):
    """Schema for returning post data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    source_content: str
    source_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
