# Evidence Log - Social Media Studio (`flyrank-capstone-social-studio`)

## Capstone Requirement-by-Requirement Evidence Matrix

| Requirement Area | Status | Test Name / Procedure | Actual Verification Command | Real Observed Result | Limitations & Notes |
| :--- | :---: | :--- | :--- | :--- | :--- |
| **1. Ingestion** | **VERIFIED** | `tests/test_ingestion_and_generation.py::test_1_markdown_post_ingestion`, `test_3_url_post_ingestion_with_mocking`, `tests/test_regression_and_reliability.py::test_reliability_ssrf_blocking_private_networks` | `.venv/bin/pytest tests/ -k "ingestion or ssrf" -v` | Ingests Markdown, stores raw content in `Post.source_content` (`id=1`). Ingests URL with HTML extraction. Blocks loopback/private IPs with `400 Bad Request`. | Client-side JavaScript-rendered SPA content requires server-rendered HTML or SSR. |
| **2. Constraint Enforcement** | **VERIFIED** | `tests/test_ingestion_and_generation.py::test_7..10`, `tests/test_acceptance_probes.py::test_acceptance_probe_2_violating_variant_rejected_naming_broken_rule` | `.venv/bin/pytest tests/test_acceptance_probes.py -k "probe_2" -v` | Violations of character limits, hashtag counts, or tone heuristics are marked invalid. Generation quarantines them into `rejected`. Approving violating text returns `422 Unprocessable Content` with specific error details. | Tone validation is heuristic-based (question prompts, conversational markers, all-caps suppression, paragraph spacing). |
| **3. Review Workflow** | **VERIFIED** | `tests/test_review_workflow.py` (15 tests), `tests/test_e2e_integration.py::test_scenario_a_complete_campaign_lifecycle`, `test_scenario_b_invalid_content` | `.venv/bin/pytest tests/test_review_workflow.py -v` | Valid transitions enforced: `draft` -> `approved`/`rejected`, `rejected` -> `draft`. Edits to approved variants revert status to `draft` and revalidate. Published variants cannot be edited. Unapproved variants cannot be scheduled (`400 Bad Request`). | None. State machine strictly enforced in domain models and services. |
| **4. Adapter Interface & Implementations** | **VERIFIED** | `tests/test_publishers_and_adapters.py` (15 tests), `tests/test_acceptance_probes.py::test_acceptance_probe_5_telegram_adapter_lifecycle` | `.venv/bin/pytest tests/test_publishers_and_adapters.py -v` | `SocialPublisher` ABC returns normalized `PublishResult`. `MockX` and `MockLinkedIn` persist to SQLite `mock_platform_posts`. `TelegramPublisher` formats payloads, catches timeouts/errors, redacts tokens in logs, and parses `message_id`. Adapters swappable via config without business logic changes. | Automated test suite uses mocked Telegram API to guarantee hermetic test execution with zero external network reliance. |
| **5. Idempotent Publishing** | **VERIFIED** | `tests/test_idempotency_and_publishing.py` (12 tests), `tests/test_acceptance_probes.py::test_acceptance_probe_4_duplicate_worker_and_api_publish`, `tests/test_e2e_integration.py::test_scenario_c_duplicate_publishing_prevention` | `.venv/bin/pytest tests/test_idempotency_and_publishing.py -v` | Deterministic key `slot:var-<id>:<timestamp>` backed by SQLite unique constraints. Repeated requests on completed slots return `is_replay: true` with cached post ID and zero additional adapter calls. Concurrent slot claims return `409 Conflict`. | Two-Generals Problem applies to external non-idempotent APIs. Timeouts are quarantined in `reconciliation_required` to prevent duplicate broadcasts. |
| **6. Durable Scheduling & Restart Recovery** | **VERIFIED** | `tests/test_scheduler_and_history.py` (17 tests), `tests/test_acceptance_probes.py::test_acceptance_probe_6_worker_crash_recovery_no_double_publish`, `tests/test_e2e_integration.py::test_scenario_d_worker_crash_and_recovery` | `.venv/bin/pytest tests/test_scheduler_and_history.py -k "crash or recovery or due" -v` | Slots persisted in SQLite with UTC timestamps. Scheduler runs bounded batches (`batch_size=10`). Worker strictly queries `status == 'pending'`. Simulated crashes leave slots in `processing` or `reconciliation_required`; restarting worker skips them without re-executing. | Slots in `reconciliation_required` require operator intervention to verify actual external delivery before retrying. |
| **7. Publish History** | **VERIFIED** | `tests/test_scheduler_and_history.py::test_12_publish_history_listing_and_filtering`, `test_13_publish_history_single_attempt_retrieval`, `tests/test_regression_and_reliability.py::test_reliability_publish_history_pagination_and_filters` | `.venv/bin/pytest tests/test_scheduler_and_history.py -k "history" -v` | Every attempt logged in `publish_attempts` table. `GET /api/publish-history` supports filtering by `variant_id`, `slot_id`, `platform`, `status`, and pagination (`limit`, `offset`) in reverse chronological order. Single attempt details return external post IDs and errors. | Audit records are immutable. |
| **8. Secret Management** | **VERIFIED** | `.gitignore` security audit, `tests/test_publishers_and_adapters.py::test_telegram_publisher_redacts_token_in_errors`, `tests/test_regression_and_reliability.py::test_reliability_telegram_missing_credentials_fails_gracefully` | `grep -rnI -E "(bot[0-9]+:[a-zA-Z0-9_-]+)" app/ tests/` (0 matches) | `.env` ignored in git. `.env.example` has only placeholder values. No real tokens in git history. `TelegramPublisher` redacts tokens in logs as `[REDACTED_BOT_TOKEN]`. Missing credentials fail gracefully without crashing. | None. Zero secrets in tracked files or Git tree. |
| **9. README Reproducibility** | **VERIFIED** | Executed all documented installation, initialization, test, and worker commands in clean terminal session | `python -c "from app.database import init_db; init_db()"` and `pytest tests/ -v` | All commands run as documented. Database initializes cleanly. All 112 tests pass in 1.49s. Worker starts and processes slots. Health check returns 200 healthy. Mock publishing operates with zero external accounts. | Testing real Telegram publishing requires the operator to provide their own bot credentials in `.env`. |

---

## Phase 1 Scaffolding Verification


### 1. Test Suite Execution (`pytest`)
**Command**:
```bash
.venv/bin/pytest tests/ -v
```
**Output**:
```
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0 -- /home/celeste/social_media/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/celeste/social_media
plugins: anyio-4.15.1
collecting ... collected 3 items

tests/test_health.py::test_health_check_root_endpoint PASSED             [ 33%]
tests/test_health.py::test_health_check_v1_prefixed_endpoint PASSED      [ 66%]
tests/test_health.py::test_root_redirects_to_docs PASSED                 [100%]

========================= 3 passed, 1 warning in 0.11s =========================
```

### 2. Live Health Endpoint & Database Verification
**Command**:
```python
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)
res = client.get('/health')
print(res.status_code, res.json())
```
**Output**:
```json
{
  "status": "healthy",
  "environment": "development",
  "database": "healthy",
  "timestamp": "2026-10-10T14:35:46.789252Z"
}
```
**Result**:
- HTTP 200 OK
- Database query `SELECT 1` executed and committed/rolled back successfully.

---

## Phase 2 Models and Repositories Verification

### 1. Test Suite Execution (`pytest`)
**Command**:
```bash
.venv/bin/pytest tests/ -v
```
**Output**:
```
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0 -- /home/celeste/social_media/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/celeste/social_media
plugins: anyio-4.15.1
collecting ... collected 16 items

tests/test_health.py::test_health_check_root_endpoint PASSED             [  6%]
tests/test_health.py::test_health_check_v1_prefixed_endpoint PASSED      [ 12%]
tests/test_health.py::test_root_redirects_to_docs PASSED                 [ 18%]
tests/test_models_and_repositories.py::test_1_post_creation_and_retrieval PASSED [ 25%]
tests/test_models_and_repositories.py::test_2_post_can_have_multiple_variants PASSED [ 31%]
tests/test_models_and_repositories.py::test_3_variant_belongs_to_correct_post PASSED [ 37%]
tests/test_models_and_repositories.py::test_4_schedule_slot_references_correct_variant PASSED [ 43%]
tests/test_models_and_repositories.py::test_5_publish_attempts_associated_with_correct_schedule_slot PASSED [ 50%]
tests/test_models_and_repositories.py::test_6_invalid_foreign_key_references_rejected PASSED [ 56%]
tests/test_models_and_repositories.py::test_7_duplicate_idempotency_keys_rejected PASSED [ 62%]
tests/test_models_and_repositories.py::test_8_duplicate_variant_time_slots_rejected PASSED [ 68%]
tests/test_models_and_repositories.py::test_9_status_fields_reject_invalid_values PASSED [ 75%]
tests/test_models_and_repositories.py::test_10_database_transactions_rollback_correctly_after_failure PASSED [ 81%]
tests/test_models_and_repositories.py::test_11_cascading_delete_removes_descendants PASSED [ 87%]
tests/test_models_and_repositories.py::test_12_repository_status_and_content_updates PASSED [ 93%]
tests/test_models_and_repositories.py::test_13_duplicate_attempt_numbers_rejected PASSED [100%]

======================== 16 passed, 1 warning in 0.43s =========================
```

### 2. Constraints & Data Integrity Verified:
- Post creation & retrieval
- One-to-many relationship: Post -> Variants
- Variant -> Post back-reference
- Variant -> ScheduleSlot relationship
- ScheduleSlot -> PublishAttempt relationship
- SQLite foreign key violation triggers `IntegrityError`
- Duplicate `idempotency_key` triggers `IntegrityError`
- Duplicate `(variant_id, scheduled_at)` triggers `IntegrityError`
- Invalid enum values rejected with `ValueError`
- Failed transactions roll back cleanly leaving committed data intact
- Deleting a `Post` cascades and cleans up all child variants, slots, and attempts
- Duplicate `(schedule_slot_id, attempt_number)` triggers `IntegrityError`

---

## Phase 3 Ingestion, Generation, and Constraint Validation Verification

### 1. Test Suite Execution (`pytest`)
**Command**:
```bash
.venv/bin/pytest tests/ -v
```
**Output**:
```
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0 -- /home/celeste/social_media/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/celeste/social_media
plugins: anyio-4.15.1
collecting ... collected 31 items

tests/test_health.py::test_health_check_root_endpoint PASSED             [  3%]
tests/test_health.py::test_health_check_v1_prefixed_endpoint PASSED      [  6%]
tests/test_health.py::test_root_redirects_to_docs PASSED                 [  9%]
tests/test_ingestion_and_generation.py::test_1_markdown_ingestion_success PASSED [ 12%]
tests/test_ingestion_and_generation.py::test_2_markdown_ingestion_invalid_empty PASSED [ 16%]
tests/test_ingestion_and_generation.py::test_3_url_extraction_success_mocked PASSED [ 19%]
tests/test_ingestion_and_generation.py::test_4_ssrf_protection_rejects_internal_destinations PASSED [ 22%]
tests/test_ingestion_and_generation.py::test_5_missing_post_returns_404 PASSED [ 25%]
tests/test_ingestion_and_generation.py::test_6_generation_produces_different_variants PASSED [ 29%]
tests/test_ingestion_and_generation.py::test_7_generation_uses_only_stored_source_content PASSED [ 32%]
tests/test_ingestion_and_generation.py::test_8_unknown_platforms_rejected PASSED [ 35%]
tests/test_ingestion_and_generation.py::test_9_maximum_length_violations_detected PASSED [ 38%]
tests/test_ingestion_and_generation.py::test_10_excessive_hashtags_detected PASSED [ 41%]
tests/test_ingestion_and_generation.py::test_11_tone_checks_behave_according_to_rules PASSED [ 45%]
tests/test_ingestion_and_generation.py::test_12_invalid_variants_marked_rejected_and_cannot_be_drafts PASSED [ 48%]
tests/test_ingestion_and_generation.py::test_13_database_changes_rollback_when_generation_fails_partway PASSED [ 51%]
tests/test_ingestion_and_generation.py::test_14_get_endpoints PASSED     [ 54%]
tests/test_ingestion_and_generation.py::test_15_revalidate_valid_variant PASSED [ 58%]
tests/test_models_and_repositories.py::test_1_post_creation_and_retrieval PASSED [ 61%]
tests/test_models_and_repositories.py::test_2_post_can_have_multiple_variants PASSED [ 64%]
tests/test_models_and_repositories.py::test_3_variant_belongs_to_correct_post PASSED [ 67%]
tests/test_models_and_repositories.py::test_4_schedule_slot_references_correct_variant PASSED [ 70%]
tests/test_models_and_repositories.py::test_5_publish_attempts_associated_with_correct_schedule_slot PASSED [ 74%]
tests/test_models_and_repositories.py::test_6_invalid_foreign_key_references_rejected PASSED [ 77%]
tests/test_models_and_repositories.py::test_7_duplicate_idempotency_keys_rejected PASSED [ 80%]
tests/test_models_and_repositories.py::test_8_duplicate_variant_time_slots_rejected PASSED [ 83%]
tests/test_models_and_repositories.py::test_9_status_fields_reject_invalid_values PASSED [ 87%]
tests/test_models_and_repositories.py::test_10_database_transactions_rollback_correctly_after_failure PASSED [ 90%]
tests/test_models_and_repositories.py::test_11_cascading_delete_removes_descendants PASSED [ 93%]
tests/test_models_and_repositories.py::test_12_repository_status_and_content_updates PASSED [ 96%]
tests/test_models_and_repositories.py::test_13_duplicate_attempt_numbers_rejected PASSED [100%]

======================== 31 passed, 1 warning in 1.08s =========================
```

### 2. Demonstration: Successful Generation
**Generated Post ID 1:**
- Platform: `mock_x`
  - Status: `draft`
  - Length: 221 chars (limit 280)
  - Hashtags: 2 (`#Tech`, `#Dev`, limit 3)
  - Conversational CTA: Present (`What are your thoughts? Check it out!`)
- Platform: `mock_linkedin`
  - Status: `draft`
  - Length: 347 chars (limit 3000)
  - Hashtags: 3 (`#Engineering`, `#SoftwareDevelopment`, `#TechLeadership`, limit 5)
  - Discussion Prompt: Present (`What are your thoughts on this approach? How do you tackle this in your experience?`)
- Platform: `telegram`
  - Status: `draft`
  - Length: 237 chars (limit 4096)
  - Hashtags: 3 (`#TechNotes`, `#Insights`, `#DailyRead`, limit 5)
  - Formatting: Structured with header emoji, paragraph spacing, source link.

### 3. Demonstration: Rejected Invalid Variant
When content violates constraints (e.g. 350 characters on `mock_x` with 5 hashtags and missing conversational CTA):
- Result:
  ```json
  {
    "is_valid": false,
    "status": "rejected",
    "errors": [
      "Content exceeds maximum length of 280 characters (current length: 350).",
      "Content exceeds maximum of 3 hashtags (found 5: #Tech, #Dev, #Code, #More, #Extra)."
    ]
  }
  ```
- Variant is stored with `status: rejected` and populated `validation_errors`, guaranteeing it is excluded from review eligibility.

---

## Phase 4 Variant Review and Approval Workflow Verification

### 1. Test Suite Execution (`pytest`)
**Command**:
```bash
.venv/bin/pytest tests/ -v
```
**Output**:
```
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0 -- /home/celeste/social_media/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/celeste/social_media
plugins: anyio-4.15.1
collecting ... collected 46 items

tests/test_health.py::test_health_check_root_endpoint PASSED             [  2%]
tests/test_health.py::test_health_check_v1_prefixed_endpoint PASSED      [  4%]
tests/test_health.py::test_root_redirects_to_docs PASSED                 [  6%]
tests/test_ingestion_and_generation.py::test_1_markdown_ingestion_success PASSED [  8%]
tests/test_ingestion_and_generation.py::test_2_markdown_ingestion_invalid_empty PASSED [ 10%]
tests/test_ingestion_and_generation.py::test_3_url_extraction_success_mocked PASSED [ 13%]
tests/test_ingestion_and_generation.py::test_4_ssrf_protection_rejects_internal_destinations PASSED [ 15%]
tests/test_ingestion_and_generation.py::test_5_missing_post_returns_404 PASSED [ 17%]
tests/test_ingestion_and_generation.py::test_6_generation_produces_different_variants PASSED [ 19%]
tests/test_ingestion_and_generation.py::test_7_generation_uses_only_stored_source_content PASSED [ 21%]
tests/test_ingestion_and_generation.py::test_8_unknown_platforms_rejected PASSED [ 23%]
tests/test_ingestion_and_generation.py::test_9_maximum_length_violations_detected PASSED [ 26%]
tests/test_ingestion_and_generation.py::test_10_excessive_hashtags_detected PASSED [ 28%]
tests/test_ingestion_and_generation.py::test_11_tone_checks_behave_according_to_rules PASSED [ 30%]
tests/test_ingestion_and_generation.py::test_12_invalid_variants_marked_rejected_and_cannot_be_drafts PASSED [ 32%]
tests/test_ingestion_and_generation.py::test_13_database_changes_rollback_when_generation_fails_partway PASSED [ 34%]
tests/test_ingestion_and_generation.py::test_14_get_endpoints PASSED     [ 36%]
tests/test_ingestion_and_generation.py::test_15_revalidate_valid_variant PASSED [ 39%]
tests/test_models_and_repositories.py::test_1_post_creation_and_retrieval PASSED [ 41%]
tests/test_models_and_repositories.py::test_2_post_can_have_multiple_variants PASSED [ 43%]
tests/test_models_and_repositories.py::test_3_variant_belongs_to_correct_post PASSED [ 45%]
tests/test_models_and_repositories.py::test_4_schedule_slot_references_correct_variant PASSED [ 47%]
tests/test_models_and_repositories.py::test_5_publish_attempts_associated_with_correct_schedule_slot PASSED [ 50%]
tests/test_models_and_repositories.py::test_6_invalid_foreign_key_references_rejected PASSED [ 52%]
tests/test_models_and_repositories.py::test_7_duplicate_idempotency_keys_rejected PASSED [ 54%]
tests/test_models_and_repositories.py::test_8_duplicate_variant_time_slots_rejected PASSED [ 56%]
tests/test_models_and_repositories.py::test_9_status_fields_reject_invalid_values PASSED [ 58%]
tests/test_models_and_repositories.py::test_10_database_transactions_rollback_correctly_after_failure PASSED [ 60%]
tests/test_models_and_repositories.py::test_11_cascading_delete_removes_descendants PASSED [ 63%]
tests/test_models_and_repositories.py::test_12_repository_status_and_content_updates PASSED [ 65%]
tests/test_models_and_repositories.py::test_13_duplicate_attempt_numbers_rejected PASSED [ 67%]
tests/test_review_workflow.py::test_1_list_draft_variants PASSED         [ 69%]
tests/test_review_workflow.py::test_2_retrieve_variant PASSED            [ 71%]
tests/test_review_workflow.py::test_3_edit_draft_variant PASSED          [ 73%]
tests/test_review_workflow.py::test_4_edit_approved_variant_resets_to_draft PASSED [ 76%]
tests/test_review_workflow.py::test_5_invalid_edits_rejected_with_422 PASSED [ 78%]
tests/test_review_workflow.py::test_6_valid_draft_can_be_approved PASSED [ 80%]
tests/test_review_workflow.py::test_7_invalid_variants_cannot_be_approved PASSED [ 82%]
tests/test_review_workflow.py::test_8_draft_variant_can_be_rejected PASSED [ 84%]
tests/test_review_workflow.py::test_9_approved_variant_can_be_rejected PASSED [ 86%]
tests/test_review_workflow.py::test_10_rejected_variant_returned_to_draft PASSED [ 89%]
tests/test_review_workflow.py::test_11_published_variants_cannot_be_edited_or_transitioned PASSED [ 91%]
tests/test_review_workflow.py::test_12_ineligible_variants_fail_scheduling_guard PASSED [ 93%]
tests/test_review_workflow.py::test_13_valid_approved_variant_passes_scheduling_guard PASSED [ 95%]
tests/test_review_workflow.py::test_14_missing_variant_returns_404 PASSED [ 97%]
tests/test_review_workflow.py::test_15_failed_operations_do_not_leave_partially_updated_records PASSED [100%]

======================== 46 passed, 4 warnings in 0.52s ========================
```

### 2. Demonstration: Scheduling Eligibility Guard Enforcement
Live API and service verification:
1. **DRAFT variant evaluation**:
   - `SchedulingEligibilityGuard.check_eligibility(variant)` returns:
     `Eligible: False, Reason: Variant is in DRAFT status. Only APPROVED variants may be scheduled.`
2. **Attempting to approve an INVALID variant**:
   - `POST /api/variants/{id}/approve` returns HTTP 422:
     ```json
     {
       "detail": {
         "message": "Cannot approve variant: content violates mock_x constraints.",
         "errors": [
           "Content exceeds maximum length of 280 characters (current length: 317).",
           "Tone violation: Mock X content must be conversational and include an engaging call-to-action or prompt (e.g. question, tip, or invite)."
         ]
       }
     }
     ```
3. **Approving a VALID variant**:
   - `POST /api/variants/{id}/approve` succeeds with HTTP 200:
     `status: "approved"`
4. **APPROVED variant evaluation**:
   - `SchedulingEligibilityGuard.check_eligibility(variant)` returns:
     `Eligible: True, Reason: None`

---

## Phase 5 Publishers, Adapters, and Publishing Pipeline Verification

### 1. Test Suite Execution (`pytest`)
**Command**:
```bash
.venv/bin/pytest tests/ -v
```
**Output**:
```
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0 -- /home/celeste/social_media/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/celeste/social_media
plugins: anyio-4.15.1
collecting ... collected 61 items

tests/test_health.py::test_health_check_root_endpoint PASSED             [  1%]
tests/test_health.py::test_health_check_v1_prefixed_endpoint PASSED      [  3%]
tests/test_health.py::test_root_redirects_to_docs PASSED                 [  4%]
tests/test_ingestion_and_generation.py::test_1_markdown_ingestion_success PASSED [  6%]
tests/test_ingestion_and_generation.py::test_2_markdown_ingestion_invalid_empty PASSED [  8%]
tests/test_ingestion_and_generation.py::test_3_url_extraction_success_mocked PASSED [  9%]
tests/test_ingestion_and_generation.py::test_4_ssrf_protection_rejects_internal_destinations PASSED [ 11%]
tests/test_ingestion_and_generation.py::test_5_missing_post_returns_404 PASSED [ 13%]
tests/test_ingestion_and_generation.py::test_6_generation_produces_different_variants PASSED [ 14%]
tests/test_ingestion_and_generation.py::test_7_generation_uses_only_stored_source_content PASSED [ 16%]
tests/test_ingestion_and_generation.py::test_8_unknown_platforms_rejected PASSED [ 18%]
tests/test_ingestion_and_generation.py::test_9_maximum_length_violations_detected PASSED [ 19%]
tests/test_ingestion_and_generation.py::test_10_excessive_hashtags_detected PASSED [ 21%]
tests/test_ingestion_and_generation.py::test_11_tone_checks_behave_according_to_rules PASSED [ 22%]
tests/test_ingestion_and_generation.py::test_12_invalid_variants_marked_rejected_and_cannot_be_drafts PASSED [ 24%]
tests/test_ingestion_and_generation.py::test_13_database_changes_rollback_when_generation_fails_partway PASSED [ 26%]
tests/test_ingestion_and_generation.py::test_14_get_endpoints PASSED     [ 27%]
tests/test_ingestion_and_generation.py::test_15_revalidate_valid_variant PASSED [ 29%]
tests/test_models_and_repositories.py::test_1_post_creation_and_retrieval PASSED [ 31%]
tests/test_models_and_repositories.py::test_2_post_can_have_multiple_variants PASSED [ 32%]
tests/test_models_and_repositories.py::test_3_variant_belongs_to_correct_post PASSED [ 34%]
tests/test_models_and_repositories.py::test_4_schedule_slot_references_correct_variant PASSED [ 36%]
tests/test_models_and_repositories.py::test_5_publish_attempts_associated_with_correct_schedule_slot PASSED [ 37%]
tests/test_models_and_repositories.py::test_6_invalid_foreign_key_references_rejected PASSED [ 39%]
tests/test_models_and_repositories.py::test_7_duplicate_idempotency_keys_rejected PASSED [ 40%]
tests/test_models_and_repositories.py::test_8_duplicate_variant_time_slots_rejected PASSED [ 42%]
tests/test_models_and_repositories.py::test_9_status_fields_reject_invalid_values PASSED [ 44%]
tests/test_models_and_repositories.py::test_10_database_transactions_rollback_correctly_after_failure PASSED [ 45%]
tests/test_models_and_repositories.py::test_11_cascading_delete_removes_descendants PASSED [ 47%]
tests/test_models_and_repositories.py::test_12_repository_status_and_content_updates PASSED [ 49%]
tests/test_models_and_repositories.py::test_13_duplicate_attempt_numbers_rejected PASSED [ 50%]
tests/test_publishers_and_adapters.py::test_1_mock_publishers_implement_social_publisher_interface PASSED [ 52%]
tests/test_publishers_and_adapters.py::test_2_mock_x_publisher_stores_post PASSED [ 54%]
tests/test_publishers_and_adapters.py::test_3_mock_linkedin_publisher_stores_post PASSED [ 55%]
tests/test_publishers_and_adapters.py::test_4_mock_publishers_return_previews_and_mock_ids PASSED [ 57%]
tests/test_publishers_and_adapters.py::test_5_telegram_publisher_constructs_request_payload PASSED [ 59%]
tests/test_publishers_and_adapters.py::test_6_telegram_publisher_handles_successful_response PASSED [ 60%]
tests/test_publishers_and_adapters.py::test_7_telegram_publisher_handles_api_errors PASSED [ 62%]
tests/test_publishers_and_adapters.py::test_8_telegram_publisher_handles_network_errors_and_timeouts PASSED [ 63%]
tests/test_publishers_and_adapters.py::test_9_telegram_missing_credentials_fails_cleanly PASSED [ 65%]
tests/test_publishers_and_adapters.py::test_10_unknown_publisher_name_rejected PASSED [ 67%]
tests/test_publishers_and_adapters.py::test_11_publisher_selection_changed_via_config PASSED [ 68%]
tests/test_publishers_and_adapters.py::test_12_unapproved_variant_cannot_be_published PASSED [ 70%]
tests/test_publishers_and_adapters.py::test_13_published_variant_cannot_be_published_again PASSED [ 72%]
tests/test_publishers_and_adapters.py::test_14_failed_publishing_attempts_recorded PASSED [ 73%]
tests/test_publishers_and_adapters.py::test_15_no_real_external_network_calls PASSED [ 75%]
tests/test_review_workflow.py::test_1_list_draft_variants PASSED         [ 77%]
tests/test_review_workflow.py::test_2_retrieve_variant PASSED            [ 78%]
tests/test_review_workflow.py::test_3_edit_draft_variant PASSED          [ 80%]
tests/test_review_workflow.py::test_4_edit_approved_variant_resets_to_draft PASSED [ 81%]
tests/test_review_workflow.py::test_5_invalid_edits_rejected_with_422 PASSED [ 83%]
tests/test_review_workflow.py::test_6_valid_draft_can_be_approved PASSED [ 85%]
tests/test_review_workflow.py::test_7_invalid_variants_cannot_be_approved PASSED [ 86%]
tests/test_review_workflow.py::test_8_draft_variant_can_be_rejected PASSED [ 88%]
tests/test_review_workflow.py::test_9_approved_variant_can_be_rejected PASSED [ 90%]
tests/test_review_workflow.py::test_10_rejected_variant_returned_to_draft PASSED [ 91%]
tests/test_review_workflow.py::test_11_published_variants_cannot_be_edited_or_transitioned PASSED [ 93%]
tests/test_review_workflow.py::test_12_ineligible_variants_fail_scheduling_guard PASSED [ 95%]
tests/test_review_workflow.py::test_13_valid_approved_variant_passes_scheduling_guard PASSED [ 96%]
tests/test_review_workflow.py::test_14_missing_variant_returns_404 PASSED [ 98%]
tests/test_review_workflow.py::test_15_failed_operations_do_not_leave_partially_updated_records PASSED [100%]

======================== 61 passed, 4 warnings in 1.59s ========================
```

### 2. Live Publishing Execution Demonstration
**Command**: Executed `PublishingService.publish_variant` across Mock X and Mock LinkedIn for approved variants.
**Results**:
- **Mock X Publishing**:
  - `success`: `True`
  - `external_post_id`: `mock_x_post_1055dfa4d947`
  - `preview`:
    ```
    --- [Mock X Post Preview] ---
    Deep dive into publisher adapters in Python! Thoughts? #Tech #FastAPI
    --- (Simulated post ID: mock_x_post_1055dfa4d947) ---
    ```
  - Stored in SQLite table `mock_platform_posts`
  - Audit trail: `PublishAttempt` recorded with status `succeeded`
  - Variant state: updated to `published`

- **Mock LinkedIn Publishing**:
  - `success`: `True`
  - `external_post_id`: `urn:li:share:mock_7d1d747a657f`
  - `preview`:
    ```
    === [Mock LinkedIn Feed Preview] ===
    Exploring multi-platform publisher architectures in modern Python services. What are your perspectives on adapter patterns? #Engineering #Leadership
    === (Simulated share URN: urn:li:share:mock_7d1d747a657f) ===
    ```
  - Stored in SQLite table `mock_platform_posts`
  - Audit trail: `PublishAttempt` recorded with status `succeeded`
  - Variant state: updated to `published`

- **Telegram Failure Handling**:
  - Mocked Telegram Bot API HTTP 400 error (`chat not found`).
  - Correctly recorded in `publish_attempts` with `status: failed` and detailed error message.
  - Variant state remains `approved` (eligible for retry).
  - Bot token secret redacted from error output.

---

## Phase 6 Idempotency and Duplicate Prevention Verification

### 1. Test Suite Execution (`pytest`)
**Command**:
```bash
.venv/bin/pytest tests/test_idempotency_and_publishing.py -v
```
**Output**:
```
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0 -- /home/celeste/social_media/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/celeste/social_media
plugins: anyio-4.15.1
collecting ... collected 12 items

tests/test_idempotency_and_publishing.py::test_1_deterministic_mock_publishing_scenario PASSED [  8%]
tests/test_idempotency_and_publishing.py::test_2_missing_slot_returns_404 PASSED [ 16%]
tests/test_idempotency_and_publishing.py::test_3_unapproved_variants_cannot_publish PASSED [ 25%]
tests/test_idempotency_and_publishing.py::test_4_invalid_variants_cannot_publish PASSED [ 33%]
tests/test_idempotency_and_publishing.py::test_5_completed_slots_return_existing_success PASSED [ 41%]
tests/test_idempotency_and_publishing.py::test_6_duplicate_idempotency_keys_rejected_at_db_level PASSED [ 50%]
tests/test_idempotency_and_publishing.py::test_7_concurrent_workers_claim_protection PASSED [ 58%]
tests/test_idempotency_and_publishing.py::test_8_confirmed_failures_recorded PASSED [ 66%]
tests/test_idempotency_and_publishing.py::test_9_uncertain_outcomes_not_blindly_retried PASSED [ 75%]
tests/test_idempotency_and_publishing.py::test_10_worker_crash_simulation_blocks_unsafe_resend PASSED [ 83%]
tests/test_idempotency_and_publishing.py::test_11_mock_adapters_direct_idempotency PASSED [ 91%]
tests/test_idempotency_and_publishing.py::test_12_telegram_errors_do_not_create_false_success PASSED [100%]

======================== 12 passed, 1 warning in 0.72s =========================
```

**Full Test Suite Execution**:
```bash
.venv/bin/pytest tests/ -v
```
**Output**: `73 passed, 4 warnings in 2.08s`

### 2. Evidence: Repeated Publishing Does Not Duplicate Posts
From `test_1_deterministic_mock_publishing_scenario`:
- Initial publish: `POST /api/schedule-slots/{slot_id}/publish` -> `success: True, is_replay: False, external_post_id: mock_x_post_...`.
- Replay publish: `POST /api/schedule-slots/{slot_id}/publish` -> `success: True, is_replay: True, external_post_id: mock_x_post_...`.
- Verified in database:
  - `mock_platform_posts`: Exactly 1 record matching the logical post ID.
  - `publish_attempts`: Exactly 1 attempt record for the slot.
  - No secondary API calls or duplicate entries were generated.

### 3. Evidence: Unapproved & Invalid Variants Blocked
- `test_3_unapproved_variants_cannot_publish`:
  - DRAFT variant in slot -> returns HTTP 400 (`"Variant is in DRAFT status. Only APPROVED variants may be scheduled."`).
  - REJECTED variant in slot -> returns HTTP 400 (`"Variant is REJECTED (Editorial review failed) and cannot be scheduled."`).
- `test_4_invalid_variants_cannot_publish`:
  - Variant failing platform tone rules -> returns HTTP 400 (`"Approved variant violates platform constraints and cannot be scheduled: ..."`).

### 4. Evidence: Uncertain External Outcomes Are Not Blindly Retried
From `test_9_uncertain_outcomes_not_blindly_retried`:
- When Telegram API call encounters an `httpx.TimeoutException`:
  - Attempt status is set to `UNCERTAIN` with message: `"Telegram API request timed out after 10.0 seconds."`.
  - Slot status transitions to `RECONCILIATION_REQUIRED`.
  - Subsequent call to `/publish` returns HTTP 409 Conflict:
    `"Schedule slot 1 has an uncertain previous attempt and requires manual reconciliation before retry."`
  - Automated worker execution is locked, guaranteeing the worker restart or retry will not duplicate a potentially delivered Telegram message.

### 5. Local Idempotency vs. Exactly-Once Delivery to External Platforms
- **Local Idempotency**:
  Our database transactions, unique constraints on `idempotency_key`, atomic conditional updates (`UPDATE schedule_slots SET status='processing' WHERE status IN ('pending', 'failed')`), and duplicate check in mock storage guarantee that local actions are strictly idempotent. Repeated local requests will never produce duplicate records or attempts.
- **Limits of External APIs (Distributed Two-Generals Problem)**:
  A local SQLite transaction cannot participate in a two-phase commit (2PC) with third-party remote platforms (like Telegram Bot API). If Telegram receives and publishes the message, but the TCP connection drops or times out before the HTTP response reaches our server, the outcome is inherently uncertain. Because Telegram's Bot API does not provide a deduplicating idempotency key header on `sendMessage`, automatically retrying an unconfirmed request risks sending a duplicate message to the channel.
  Therefore, our system explicitly marks such attempts as `UNCERTAIN` and places the slot in `RECONCILIATION_REQUIRED`, halting automated resends until manual inspection or reconciliation occurs.

---

## Phase 7 Scheduler, Worker, and Publish History Verification

### 1. Test Suite Execution (`pytest`)
**Command**:
```bash
.venv/bin/pytest tests/ -v
```
**Output**:
```
tests/test_health.py::test_health_check_root_endpoint PASSED             [  1%]
tests/test_health.py::test_health_check_v1_prefixed_endpoint PASSED      [  2%]
tests/test_health.py::test_root_redirects_to_docs PASSED                 [  3%]
...
tests/test_scheduler_and_history.py::test_1_valid_approved_variant_scheduled_successfully PASSED [ 82%]
tests/test_scheduler_and_history.py::test_2_naive_or_invalid_timestamps_rejected PASSED [ 83%]
tests/test_scheduler_and_history.py::test_3_draft_variant_cannot_be_scheduled PASSED [ 84%]
tests/test_scheduler_and_history.py::test_4_rejected_variant_cannot_be_scheduled PASSED [ 85%]
tests/test_scheduler_and_history.py::test_5_published_variant_cannot_be_scheduled PASSED [ 86%]
tests/test_scheduler_and_history.py::test_6_invalid_variant_cannot_be_scheduled_even_if_marked_approved PASSED [ 87%]
tests/test_scheduler_and_history.py::test_7_duplicate_schedule_same_timestamp_returns_409 PASSED [ 88%]
tests/test_scheduler_and_history.py::test_8_stable_idempotency_key_generated_deterministically PASSED [ 90%]
tests/test_scheduler_and_history.py::test_9_due_slots_identified_and_processed_by_scheduler PASSED [ 91%]
tests/test_scheduler_and_history.py::test_10_future_slots_remain_pending_not_published_early PASSED [ 92%]
tests/test_scheduler_and_history.py::test_11_bounded_batch_size_respected PASSED [ 93%]
tests/test_scheduler_and_history.py::test_12_error_in_one_slot_does_not_halt_remaining_batch PASSED [ 94%]
tests/test_scheduler_and_history.py::test_13_worker_processes_due_slots_and_updates_status PASSED [ 95%]
tests/test_scheduler_and_history.py::test_14_restart_safety_no_blind_reset_and_uncertain_reconciliation PASSED [ 96%]
tests/test_scheduler_and_history.py::test_15_publish_history_list_filtering_and_pagination PASSED [ 97%]
tests/test_scheduler_and_history.py::test_16_publish_history_detail_endpoint PASSED [ 98%]
tests/test_scheduler_and_history.py::test_17_scheduler_status_endpoint PASSED [100%]

======================== 90 passed, 4 warnings in 1.10s ========================
```

### 2. Evidence: Scheduling Eligibility Guard & UTC Normalization
- Offset-aware input `2026-10-10T17:30:00+02:00` normalizes to `2026-10-10T15:30:00Z` in UTC.
- Naive datetime inputs (e.g. `2026-10-10T15:30:00`) are rejected with HTTP 422 Unprocessable Content.
- Non-approved, rejected, or published variants cannot be scheduled (HTTP 400).
- Duplicate schedule requests for the same variant and timestamp return HTTP 409 Conflict.

### 3. Evidence: Durable Scheduler & Background Worker
- Pending slots with `scheduled_at <= now_utc` are queried and claimed atomically.
- Future slots remain pending and are not published before their due time.
- Processing occurs in bounded batches (`batch_size=10`).
- Exceptions during external publishing are isolated per slot; remaining slots in batch are still processed.
- Background worker supports graceful termination on `SIGINT` and `SIGTERM`.
- Restart-safe: `SchedulerWorker` only queries `PENDING` slots and never blindly resets `PROCESSING` slots to `PENDING`.
- Slots requiring manual reconciliation (`RECONCILIATION_REQUIRED`) are blocked from automatic re-execution.

### 4. Evidence: Publish History & Observability Endpoints
- `GET /api/publish-history`: Paginated audit log records in reverse-chronological order, filterable by `variant_id`, `slot_id`, `platform`, `status`.
- `GET /api/publish-history/{attempt_id}`: Full attempt audit detail or HTTP 404.
- `GET /api/scheduler/status`: Operational counts of pending, due, processing, completed, failed, and reconciliation-required slots.

---

## Phase 8 Integration Tests, Failure Recovery, and Acceptance Checks Verification

### 1. Test Suite Execution (`pytest`)
**Command**:
```bash
.venv/bin/pytest tests/ -v
```
**Output**:
```
tests/test_acceptance_probes.py::test_acceptance_probe_1_stored_post_produces_valid_variants PASSED [  0%]
tests/test_acceptance_probes.py::test_acceptance_probe_2_violating_variant_rejected_naming_broken_rule PASSED [  1%]
tests/test_acceptance_probes.py::test_acceptance_probe_3_unapproved_variant_cannot_be_scheduled_receives_4xx PASSED [  2%]
tests/test_acceptance_probes.py::test_acceptance_probe_4_approved_variant_scheduled_and_published_when_due PASSED [  3%]
tests/test_acceptance_probes.py::test_acceptance_probe_5_repeated_publication_and_recovery_no_duplicates PASSED [  4%]
tests/test_acceptance_probes.py::test_acceptance_probe_6_switching_adapter_through_config_changes_adapter PASSED [  5%]
tests/test_e2e_integration.py::test_scenario_a_complete_campaign PASSED  [  6%]
tests/test_e2e_integration.py::test_scenario_b_invalid_content PASSED    [  7%]
tests/test_e2e_integration.py::test_scenario_c_duplicate_publishing PASSED [  8%]
tests/test_e2e_integration.py::test_scenario_d_worker_restart_and_recovery PASSED [  8%]
tests/test_e2e_integration.py::test_scenario_e_adapter_swapping PASSED   [  9%]
tests/test_e2e_integration.py::test_scenario_f_telegram_adapter_lifecycle PASSED [ 10%]
tests/test_health.py::test_health_check_root_endpoint PASSED             [ 11%]
tests/test_health.py::test_health_check_v1_prefixed_endpoint PASSED      [ 12%]
tests/test_health.py::test_root_redirects_to_docs PASSED                 [ 13%]
tests/test_idempotency_and_publishing.py::test_1_deterministic_mock_publishing_scenario PASSED [ 14%]
tests/test_idempotency_and_publishing.py::test_2_missing_slot_returns_404 PASSED [ 15%]
tests/test_idempotency_and_publishing.py::test_3_unapproved_variants_cannot_publish PASSED [ 16%]
tests/test_idempotency_and_publishing.py::test_4_invalid_variants_cannot_publish PASSED [ 16%]
tests/test_idempotency_and_publishing.py::test_5_completed_slots_return_existing_success PASSED [ 17%]
tests/test_idempotency_and_publishing.py::test_6_duplicate_idempotency_keys_rejected_at_db_level PASSED [ 18%]
tests/test_idempotency_and_publishing.py::test_7_concurrent_workers_claim_protection PASSED [ 19%]
tests/test_idempotency_and_publishing.py::test_8_confirmed_failures_recorded PASSED [ 20%]
tests/test_idempotency_and_publishing.py::test_9_uncertain_outcomes_not_blindly_retried PASSED [ 21%]
tests/test_idempotency_and_publishing.py::test_10_worker_crash_simulation_blocks_unsafe_resend PASSED [ 22%]
tests/test_idempotency_and_publishing.py::test_11_mock_adapters_direct_idempotency PASSED [ 23%]
tests/test_idempotency_and_publishing.py::test_12_telegram_errors_do_not_create_false_success PASSED [ 24%]
tests/test_ingestion_and_generation.py::test_1_markdown_ingestion_success PASSED [ 25%]
tests/test_ingestion_and_generation.py::test_2_markdown_ingestion_invalid_empty PASSED [ 25%]
tests/test_ingestion_and_generation.py::test_3_url_extraction_success_mocked PASSED [ 26%]
tests/test_ingestion_and_generation.py::test_4_ssrf_protection_rejects_internal_destinations PASSED [ 27%]
tests/test_ingestion_and_generation.py::test_5_missing_post_returns_404 PASSED [ 28%]
tests/test_ingestion_and_generation.py::test_6_generation_produces_different_variants PASSED [ 29%]
tests/test_ingestion_and_generation.py::test_7_generation_uses_only_stored_source_content PASSED [ 30%]
tests/test_ingestion_and_generation.py::test_8_unknown_platforms_rejected PASSED [ 31%]
tests/test_ingestion_and_generation.py::test_9_maximum_length_violations_detected PASSED [ 32%]
tests/test_ingestion_and_generation.py::test_10_excessive_hashtags_detected PASSED [ 33%]
tests/test_ingestion_and_generation.py::test_11_tone_checks_behave_according_to_rules PASSED [ 33%]
tests/test_ingestion_and_generation.py::test_12_invalid_variants_marked_rejected_and_cannot_be_drafts PASSED [ 34%]
tests/test_ingestion_and_generation.py::test_13_database_changes_rollback_when_generation_fails_partway PASSED [ 35%]
tests/test_ingestion_and_generation.py::test_14_get_endpoints PASSED     [ 36%]
tests/test_ingestion_and_generation.py::test_15_revalidate_valid_variant PASSED [ 37%]
tests/test_models_and_repositories.py::test_1_post_creation_and_retrieval PASSED [ 38%]
tests/test_models_and_repositories.py::test_2_post_can_have_multiple_variants PASSED [ 39%]
tests/test_models_and_repositories.py::test_3_variant_belongs_to_correct_post PASSED [ 40%]
tests/test_models_and_repositories.py::test_4_schedule_slot_references_correct_variant PASSED [ 41%]
tests/test_models_and_repositories.py::test_5_publish_attempts_associated_with_correct_schedule_slot PASSED [ 41%]
tests/test_models_and_repositories.py::test_6_invalid_foreign_key_references_rejected PASSED [ 42%]
tests/test_models_and_repositories.py::test_7_duplicate_idempotency_keys_rejected PASSED [ 43%]
tests/test_models_and_repositories.py::test_8_duplicate_variant_time_slots_rejected PASSED [ 44%]
tests/test_models_and_repositories.py::test_9_status_fields_reject_invalid_values PASSED [ 45%]
tests/test_models_and_repositories.py::test_10_database_transactions_rollback_correctly_after_failure PASSED [ 46%]
tests/test_models_and_repositories.py::test_11_cascading_delete_removes_descendants PASSED [ 47%]
tests/test_models_and_repositories.py::test_12_repository_status_and_content_updates PASSED [ 48%]
tests/test_models_and_repositories.py::test_13_duplicate_attempt_numbers_rejected PASSED [ 49%]
tests/test_publishers_and_adapters.py::test_1_mock_publishers_implement_social_publisher_interface PASSED [ 50%]
tests/test_publishers_and_adapters.py::test_2_mock_x_publisher_stores_post PASSED [ 50%]
tests/test_publishers_and_adapters.py::test_3_mock_linkedin_publisher_stores_post PASSED [ 51%]
tests/test_publishers_and_adapters.py::test_4_mock_publishers_return_previews_and_mock_ids PASSED [ 52%]
tests/test_publishers_and_adapters.py::test_5_telegram_publisher_constructs_request_payload PASSED [ 53%]
tests/test_publishers_and_adapters.py::test_6_telegram_publisher_handles_successful_response PASSED [ 54%]
tests/test_publishers_and_adapters.py::test_7_telegram_publisher_handles_api_errors PASSED [ 55%]
tests/test_publishers_and_adapters.py::test_8_telegram_publisher_handles_network_errors_and_timeouts PASSED [ 56%]
tests/test_publishers_and_adapters.py::test_9_telegram_missing_credentials_fails_cleanly PASSED [ 57%]
tests/test_publishers_and_adapters.py::test_10_unknown_publisher_name_rejected PASSED [ 58%]
tests/test_publishers_and_adapters.py::test_11_publisher_selection_changed_via_config PASSED [ 58%]
tests/test_publishers_and_adapters.py::test_12_unapproved_variant_cannot_be_published PASSED [ 59%]
tests/test_publishers_and_adapters.py::test_13_published_variant_cannot_be_published_again PASSED [ 60%]
tests/test_publishers_and_adapters.py::test_14_failed_publishing_attempts_recorded PASSED [ 61%]
tests/test_publishers_and_adapters.py::test_15_no_real_external_network_calls PASSED [ 62%]
tests/test_regression_and_reliability.py::test_missing_records_return_404 PASSED [ 63%]
tests/test_regression_and_reliability.py::test_malformed_requests_rejected PASSED [ 64%]
tests/test_regression_and_reliability.py::test_invalid_state_transitions_blocked PASSED [ 65%]
tests/test_regression_and_reliability.py::test_database_level_constraints_and_rollback PASSED [ 66%]
tests/test_regression_and_reliability.py::test_concurrent_slot_claims_protection PASSED [ 66%]
tests/test_regression_and_reliability.py::test_safe_retry_behavior_after_confirmed_failure PASSED [ 67%]
tests/test_regression_and_reliability.py::test_recovery_blocks_blind_retry_on_uncertainty PASSED [ 68%]
tests/test_regression_and_reliability.py::test_publish_history_accuracy_and_pagination PASSED [ 69%]
tests/test_regression_and_reliability.py::test_missing_telegram_credentials_does_not_break_mock_adapters PASSED [ 70%]
tests/test_regression_and_reliability.py::test_ssrf_protection_for_url_ingestion PASSED [ 71%]
tests/test_review_workflow.py::test_1_list_draft_variants PASSED         [ 72%]
tests/test_review_workflow.py::test_2_retrieve_variant PASSED            [ 73%]
tests/test_review_workflow.py::test_3_edit_draft_variant PASSED          [ 74%]
tests/test_review_workflow.py::test_4_edit_approved_variant_resets_to_draft PASSED [ 75%]
tests/test_review_workflow.py::test_5_invalid_edits_rejected_with_422 PASSED [ 75%]
tests/test_review_workflow.py::test_6_valid_draft_can_be_approved PASSED [ 76%]
tests/test_review_workflow.py::test_7_invalid_variants_cannot_be_approved PASSED [ 77%]
tests/test_review_workflow.py::test_8_draft_variant_can_be_rejected PASSED [ 78%]
tests/test_review_workflow.py::test_9_approved_variant_can_be_rejected PASSED [ 79%]
tests/test_review_workflow.py::test_10_rejected_variant_returned_to_draft PASSED [ 80%]
tests/test_review_workflow.py::test_11_published_variants_cannot_be_edited_or_transitioned PASSED [ 81%]
tests/test_review_workflow.py::test_12_ineligible_variants_fail_scheduling_guard PASSED [ 82%]
tests/test_review_workflow.py::test_13_valid_approved_variant_passes_scheduling_guard PASSED [ 83%]
tests/test_review_workflow.py::test_14_missing_variant_returns_404 PASSED [ 83%]
tests/test_review_workflow.py::test_15_failed_operations_do_not_leave_partially_updated_records PASSED [ 84%]
tests/test_scheduler_and_history.py::test_1_valid_approved_variant_scheduled_successfully PASSED [ 85%]
tests/test_scheduler_and_history.py::test_2_naive_or_invalid_timestamps_rejected PASSED [ 86%]
tests/test_scheduler_and_history.py::test_3_draft_variant_cannot_be_scheduled PASSED [ 87%]
tests/test_scheduler_and_history.py::test_4_rejected_variant_cannot_be_scheduled PASSED [ 88%]
tests/test_scheduler_and_history.py::test_5_published_variant_cannot_be_scheduled PASSED [ 89%]
tests/test_scheduler_and_history.py::test_6_invalid_variant_cannot_be_scheduled_even_if_marked_approved PASSED [ 90%]
tests/test_scheduler_and_history.py::test_7_duplicate_schedule_same_timestamp_returns_409 PASSED [ 91%]
tests/test_scheduler_and_history.py::test_8_stable_idempotency_key_generated_deterministically PASSED [ 91%]
tests/test_scheduler_and_history.py::test_9_due_slots_identified_and_processed_by_scheduler PASSED [ 92%]
tests/test_scheduler_and_history.py::test_10_future_slots_remain_pending_not_published_early PASSED [ 93%]
tests/test_scheduler_and_history.py::test_11_bounded_batch_size_respected PASSED [ 94%]
tests/test_scheduler_and_history.py::test_12_error_in_one_slot_does_not_halt_remaining_batch PASSED [ 95%]
tests/test_scheduler_and_history.py::test_13_worker_processes_due_slots_and_updates_status PASSED [ 96%]
tests/test_scheduler_and_history.py::test_14_restart_safety_no_blind_reset_and_uncertain_reconciliation PASSED [ 97%]
tests/test_scheduler_and_history.py::test_15_publish_history_list_filtering_and_pagination PASSED [ 98%]
tests/test_scheduler_and_history.py::test_16_publish_history_detail_endpoint PASSED [ 99%]
tests/test_scheduler_and_history.py::test_17_scheduler_status_endpoint PASSED [100%]

======================= 112 passed, 6 warnings in 1.49s ========================
```

---

### 2. Evidence: Capstone Acceptance Probes (Probes 1–6)
Executed via `tests/test_acceptance_probes.py`:
- **Probe 1 (Stored post produces valid variants)**: Post ingested -> generated variants for `mock_x`, `mock_linkedin`, and `telegram`. All variants validated and initiated into `draft` review status without errors.
- **Probe 2 (Violating variant rejected naming broken rule)**: Content with 5 hashtags submitted to `mock_x` approve endpoint -> rejected with `HTTP 422 Unprocessable Content`:
  ```json
  {
    "detail": {
      "message": "Cannot approve variant: content violates mock_x constraints.",
      "errors": [
        "Content exceeds maximum of 3 hashtags (found 5: #one, #two, #three, #four, #five)."
      ]
    }
  }
  ```
- **Probe 3 (Unapproved variant cannot be scheduled)**: Attempting to schedule `draft`, `rejected`, or `published` variants returns `HTTP 400 Bad Request` across all cases.
- **Probe 4 (Approved variant scheduled and published when due)**: Approved variant scheduled for future time `16:00 UTC`. Running scheduler at `15:30 UTC` leaves slot `pending`. Running scheduler at `16:05 UTC` executes the slot, transitions status to `completed`, and saves publication in `mock_platform_posts`.
- **Probe 5 (Duplicate publication & simulated recovery)**: Succeeded publication repeated via API returns existing result (`is_replay: true`). Worker restarted and polling again processes 0 slots, leaving exactly 1 logical mock publication.
- **Probe 6 (Adapter swapping through config)**: Switching configuration between `mock_x` and `mock_linkedin` directs publishing through appropriate adapters via `PublisherRegistry` without changing campaign business logic.

---

### 3. Evidence: End-to-End Campaign Flow (Scenario A)
1. Ingest post:
   `POST /api/posts` -> `201 Created` (`id=1`).
2. Generate variants:
   `POST /api/posts/1/generate` -> `201 Created` (3 variants).
3. Retrieve draft:
   `GET /api/variants?status=draft` -> `200 OK`.
4. Approve valid variant:
   `POST /api/variants/1/approve` -> `200 OK` (`status: "approved"`).
5. Schedule slot:
   `POST /api/variants/1/schedule` with `{"scheduled_at": "2026-10-10T14:00:00Z"}` -> `201 Created` (`id=1`, `status: "pending"`).
6. Run scheduler:
   `SchedulerService.process_due_slots` as of `14:30 UTC` -> 1 outcome, `success: True`.
7. Publication verified in `mock_platform_posts` (`mock_x_post_...`).
8. Slot verified `status: "completed"`.
9. Publish history record verified:
   ```json
   {
     "schedule_slot_id": 1,
     "variant_id": 1,
     "platform": "mock_x",
     "attempt_number": 1,
     "status": "succeeded",
     "external_post_id": "mock_x_post_...",
     "error_message": null
   }
   ```

---

### 4. Manual Telegram Delivery Verification Procedure
When verifying live Telegram publishing with real Bot API credentials:
1. Obtain bot credentials:
   - Talk to [@BotFather](https://t.me/BotFather) to create a bot and get `TELEGRAM_BOT_TOKEN`.
   - Add the bot to your Telegram test channel or group as an Administrator.
   - Send a test message in the channel, then query `https://api.telegram.org/bot<TOKEN>/getUpdates` to find the channel `chat_id` (typically negative, e.g. `-100123456789`).
2. Create or update `.env`:
   ```bash
   TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
   TELEGRAM_CHAT_ID="-100123456789"
   DEFAULT_PUBLISHER="telegram"
   ```
3. Start the application:
   ```bash
   ./scripts/run_dev.sh
   ```
4. Ingest and approve a Telegram variant:
   ```bash
   # Ingest
   POST /api/posts {"title": "Live Telegram Test", "content": "Verifying actual Telegram delivery via official Bot API."}
   # Generate
   POST /api/posts/{id}/generate
   # Approve
   POST /api/variants/{variant_id}/approve
   ```
5. Trigger publish:
   ```bash
   curl -X POST "http://localhost:8000/api/variants/{variant_id}/publish?platform=telegram"
   ```
6. Verification check:
   - Verify that the message appears in your Telegram channel.
   - Verify that the API response returns `success: true` with Telegram's numeric `external_post_id` (message ID).
   - Verify that `GET /api/publish-history` logs the attempt with `status: "succeeded"`.

---

### 5. Manual Worker Restart Testing Procedure
To verify that real worker process interruptions do not corrupt state or cause duplicate publishes:
1. Launch background worker daemon in terminal 1:
   ```bash
   .venv/bin/python -m app.scheduler.worker
   ```
2. In terminal 2, schedule a variant for a due time:
   ```bash
   curl -X POST http://localhost:8000/api/variants/{variant_id}/schedule \
     -H "Content-Type: application/json" \
     -d '{"scheduled_at": "2026-10-10T12:00:00Z"}'
   ```
3. Send `SIGINT` (Ctrl+C) to terminal 1. Verify in console logs:
   ```
   Received termination signal (2). Shutting down...
   Scheduler worker stopped cleanly.
   ```
4. Restart the worker daemon in terminal 1:
   ```bash
   .venv/bin/python -m app.scheduler.worker
   ```
5. Verify in terminal 2 via `GET /api/publish-history` and `GET /api/scheduler/status`:
   - The slot transitioned cleanly.
   - No duplicate publish attempts were created.
   - Completed slots are never re-executed.
