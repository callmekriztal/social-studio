import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import Platform, VariantStatus
from app.models.post import Post
from app.repositories.post_repository import PostRepository
from app.repositories.variant_repository import VariantRepository
from app.services.url_extractor import ExtractedArticle, URLExtractorService
from app.services.variant_generator import (
    BaseVariantGenerator,
    VariantGenerationService,
)
from app.validators.validator import VariantValidator


# 1. Successful Markdown ingestion
def test_1_markdown_ingestion_success(client: TestClient):
    payload = {
        "title": "Why Linux Is Useful for Developers",
        "content": "Linux gives developers powerful command-line tools and package managers.",
    }
    response = client.post("/api/posts", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["id"] is not None
    assert data["title"] == payload["title"]
    assert data["source_content"] == payload["content"]
    assert data["source_url"] is None
    assert "created_at" in data


# 2. Empty or invalid ingestion requests
def test_2_markdown_ingestion_invalid_empty(client: TestClient):
    # Empty title
    res1 = client.post("/api/posts", json={"title": "", "content": "Valid content"})
    assert res1.status_code == 422

    # Whitespace-only title
    res2 = client.post("/api/posts", json={"title": "   ", "content": "Valid content"})
    assert res2.status_code == 422

    # Empty content
    res3 = client.post("/api/posts", json={"title": "Valid title", "content": ""})
    assert res3.status_code == 422

    # Whitespace content
    res4 = client.post("/api/posts", json={"title": "Valid title", "content": "   \n\t  "})
    assert res4.status_code == 422


# 3. URL extraction and storage using mocked HTTP responses
def test_3_url_extraction_success_mocked(client: TestClient):
    mock_article = ExtractedArticle(
        title="Fetched Article Title",
        content="This is the extracted body text from the public web page.",
        source_url="https://example.com/blog/article-1",
    )

    with patch.object(URLExtractorService, "extract_article", return_value=mock_article):
        response = client.post(
            "/api/posts/from-url",
            json={"url": "https://example.com/blog/article-1"},
        )
        assert response.status_code == 201

        data = response.json()
        assert data["id"] is not None
        assert data["title"] == "Fetched Article Title"
        assert data["source_content"] == "This is the extracted body text from the public web page."
        assert data["source_url"] == "https://example.com/blog/article-1"


# 4. Rejection of unsafe URLs and internal IP addresses (SSRF)
def test_4_ssrf_protection_rejects_internal_destinations(client: TestClient):
    # Localhost IPv4
    res_loopback = client.post("/api/posts/from-url", json={"url": "http://127.0.0.1:8000/secret"})
    assert res_loopback.status_code == 400
    assert "SSRF" in res_loopback.json()["detail"] or "forbidden" in res_loopback.json()["detail"].lower()

    # Private IP RFC1918
    res_private = client.post("/api/posts/from-url", json={"url": "http://192.168.1.100/admin"})
    assert res_private.status_code == 400

    # Link-local / Cloud metadata endpoint
    res_linklocal = client.post(
        "/api/posts/from-url",
        json={"url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert res_linklocal.status_code == 400

    # Unsupported non-HTTP scheme
    res_scheme = client.post("/api/posts/from-url", json={"url": "ftp://example.com/file"})
    assert res_scheme.status_code in (400, 422)


# 5. Missing post returns 404
def test_5_missing_post_returns_404(client: TestClient):
    response = client.post(
        "/api/posts/999999/generate",
        json={"platforms": ["mock_x"]},
    )
    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


# 6. Generation produces different variants for different platforms
def test_6_generation_produces_different_variants(client: TestClient):
    # Create post first
    post_res = client.post(
        "/api/posts",
        json={
            "title": "Clean Code Architecture",
            "content": "Writing modular code with explicit dependencies makes testing straightforward.",
        },
    )
    post_id = post_res.json()["id"]

    # Generate variants
    gen_res = client.post(
        f"/api/posts/{post_id}/generate",
        json={"platforms": ["mock_x", "mock_linkedin", "telegram"]},
    )
    assert gen_res.status_code == 201

    variants = gen_res.json()
    assert len(variants) == 3

    platform_contents = {v["platform"]: v["content"] for v in variants}
    assert "mock_x" in platform_contents
    assert "mock_linkedin" in platform_contents
    assert "telegram" in platform_contents

    # Verify they are distinct
    assert platform_contents["mock_x"] != platform_contents["mock_linkedin"]
    assert platform_contents["mock_linkedin"] != platform_contents["telegram"]

    # All generated variants pass validation and are DRAFT
    for v in variants:
        assert v["status"] == VariantStatus.DRAFT.value
        assert v["validation_errors"] is None


# 7. Only stored source content is used (ignores client injections)
def test_7_generation_uses_only_stored_source_content(client: TestClient, db_session: Session):
    post = PostRepository.create(
        db=db_session,
        title="Original True Title",
        source_content="The true fact is Python was created by Guido van Rossum.",
    )

    # Client attempts to send fake override content in request body
    gen_res = client.post(
        f"/api/posts/{post.id}/generate",
        json={
            "platforms": ["mock_x"],
            "title": "FAKE TITLE INJECTED",
            "content": "FAKE CONTENT INJECTED",
        },
    )
    assert gen_res.status_code == 201
    variant_text = gen_res.json()[0]["content"]

    assert "Original True Title" in variant_text
    assert "Guido van Rossum" in variant_text
    assert "FAKE TITLE INJECTED" not in variant_text
    assert "FAKE CONTENT INJECTED" not in variant_text


# 8. Unknown platforms are rejected
def test_8_unknown_platforms_rejected(client: TestClient, db_session: Session):
    post = PostRepository.create(db=db_session, title="Test Post", source_content="Content")

    response = client.post(
        f"/api/posts/{post.id}/generate",
        json={"platforms": ["unknown_platform", "mock_x"]},
    )
    assert response.status_code == 400
    assert "unknown_platform" in response.json()["detail"].lower()


# 9. Maximum-length violations are detected
def test_9_maximum_length_violations_detected():
    # mock_x max length is 280
    oversized_text = "A" * 281 + " What do you think? #Tech"
    result = VariantValidator.validate("mock_x", oversized_text)

    assert result.is_valid is False
    assert any("exceeds maximum length of 280" in err for err in result.errors)


# 10. Excessive hashtags are detected
def test_10_excessive_hashtags_detected():
    # mock_x max hashtags is 3
    too_many_tags = "Check this out! What do you think? #one #two #three #four"
    result = VariantValidator.validate("mock_x", too_many_tags)

    assert result.is_valid is False
    assert any("exceeds maximum of 3 hashtags" in err for err in result.errors)


# 11. Tone checks behave according to documented rules
def test_11_tone_checks_behave_according_to_rules():
    # mock_x: Missing conversational CTA or question
    dull_x = "Linux is an operating system kernel released in 1991. #Linux"
    res_x = VariantValidator.validate("mock_x", dull_x)
    assert res_x.is_valid is False
    assert any("Tone violation" in err and "conversational" in err for err in res_x.errors)

    # mock_linkedin: Missing discussion prompt
    no_prompt_li = (
        "Here are key insights into software architecture.\n\n"
        "Modular systems scale better.\n\n#Engineering #Software"
    )
    res_li = VariantValidator.validate("mock_linkedin", no_prompt_li)
    assert res_li.is_valid is False
    assert any("Tone violation" in err and "discussion-oriented" in err for err in res_li.errors)

    # mock_linkedin: Excessive shouting/uppercase
    shouting_li = (
        "Check this out. THIS IS AMAZING AND REVOLUTIONARY FOR EVERYONE!\n\n"
        "What are your thoughts? #Tech"
    )
    res_li_shout = VariantValidator.validate("mock_linkedin", shouting_li)
    assert res_li_shout.is_valid is False
    assert any("excessive uppercase" in err for err in res_li_shout.errors)

    # telegram: Dense unbroken text exceeding 200 chars without line breaks
    dense_tg = "A" * 250 + " #Tech #Notes"
    res_tg = VariantValidator.validate("telegram", dense_tg)
    assert res_tg.is_valid is False
    assert any("paragraph formatting" in err for err in res_tg.errors)


# 12. Invalid variants cannot enter the review workflow (marked REJECTED)
def test_12_invalid_variants_marked_rejected_and_cannot_be_drafts(client: TestClient, db_session: Session):
    post = PostRepository.create(db=db_session, title="Short Post", source_content="Some content")

    # Mock generator that returns an invalid variant exceeding length
    class BrokenGenerator(BaseVariantGenerator):
        def generate_text(self, platform: str, post: Post) -> str:
            return "Z" * 350 + " #Tech #Dev #Code #More #Extra"

    service = VariantGenerationService(generator=BrokenGenerator())
    variants = service.generate_variants_for_post(db=db_session, post_id=post.id, target_platforms=["mock_x"])

    assert len(variants) == 1
    v = variants[0]
    # Guaranteed not to be DRAFT
    assert v.status == VariantStatus.REJECTED
    assert v.validation_errors is not None
    assert v.validation_errors["is_valid"] is False
    assert len(v.validation_errors["errors"]) >= 2

    # Revalidation endpoint also keeps it REJECTED
    val_res = client.post(f"/api/variants/{v.id}/validate")
    assert val_res.status_code == 200
    val_data = val_res.json()
    assert val_data["is_valid"] is False
    assert val_data["status"] == VariantStatus.REJECTED.value
    assert len(val_data["errors"]) >= 2


# 13. Database changes roll back when generation fails partway through
def test_13_database_changes_rollback_when_generation_fails_partway(db_session: Session):
    post = PostRepository.create(db=db_session, title="Rollback Test Post", source_content="Content")

    initial_variant_count = len(VariantRepository.list_by_post_id(db_session, post.id))

    class CrashingGenerator(BaseVariantGenerator):
        def generate_text(self, platform: str, post: Post) -> str:
            if platform == "mock_linkedin":
                raise RuntimeError("Simulated crash during batch generation!")
            return "Valid post text with question? #Tech"

    service = VariantGenerationService(generator=CrashingGenerator())

    with pytest.raises(RuntimeError, match="Simulated crash"):
        service.generate_variants_for_post(
            db=db_session,
            post_id=post.id,
            target_platforms=["mock_x", "mock_linkedin"],
        )

    # Verify that mock_x was rolled back and NOT persisted
    after_variants = VariantRepository.list_by_post_id(db_session, post.id)
    assert len(after_variants) == initial_variant_count


# 14. GET endpoints for post and variant retrieval
def test_14_get_endpoints(client: TestClient, db_session: Session):
    post = PostRepository.create(
        db=db_session,
        title="Sample Retrieval Post",
        source_content="Content to retrieve",
        source_url="https://example.com/source",
    )
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="telegram",
        content="Telegram readable text.\n\nMore info.",
    )

    # GET /api/posts/{id}
    res_post = client.get(f"/api/posts/{post.id}")
    assert res_post.status_code == 200
    assert res_post.json()["title"] == "Sample Retrieval Post"

    # GET /api/variants/{id}
    res_var = client.get(f"/api/variants/{variant.id}")
    assert res_var.status_code == 200
    assert res_var.json()["content"] == "Telegram readable text.\n\nMore info."

    # 404s
    assert client.get("/api/posts/888888").status_code == 404
    assert client.get("/api/variants/888888").status_code == 404


# 15. Revalidation endpoint with valid variant
def test_15_revalidate_valid_variant(client: TestClient, db_session: Session):
    post = PostRepository.create(db=db_session, title="Valid Reval", source_content="Content")
    variant = VariantRepository.create(
        db=db_session,
        post_id=post.id,
        platform="telegram",
        content="📌 Valid telegram post\n\nReadable paragraph with info.\n\n#Tech",
        status=VariantStatus.DRAFT,
    )

    res = client.post(f"/api/variants/{variant.id}/validate")
    assert res.status_code == 200
    data = res.json()
    assert data["is_valid"] is True
    assert data["status"] == VariantStatus.DRAFT.value
    assert len(data["errors"]) == 0

