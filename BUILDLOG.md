# Build Log - Social Media Studio

## Phase 1: Architecture and Project Scaffolding
- **Date**: 2026-10-10
- **Goal**: Establish decoupled architecture, module boundaries, database connectivity, configuration management, test harness, and health checks.

### Key Milestones Completed:
1. **Environment & Dependency Setup**:
   - Initialized Python virtual environment (`.venv`).
   - Defined `requirements.txt` with FastAPI, SQLAlchemy 2.x, Pydantic, pydantic-settings, pytest, httpx, and python-dotenv.
   - Configured `.gitignore` and `.env.example`.

2. **Core Architectural Scaffolding**:
   - Implemented `app/config.py` using `pydantic-settings` to load settings from `.env` without hardcoded credentials.
   - Implemented `app/database.py` with SQLAlchemy 2.x `create_engine`, `SessionLocal`, `DeclarativeBase`, and `get_db` dependency generator.
   - Designed the `SocialPublisher` abstract base class and `PublishResult` in `app/adapters/publisher_base.py` completely independent from FastAPI.
   - Created module packages: `app/models/`, `app/schemas/`, `app/api/`, `app/services/`, `app/repositories/`, `app/validators/`, `app/scheduler/`, and `app/adapters/`.

3. **API & Health Check**:
   - Implemented `app/main.py` application factory and lifespan manager.
   - Added `GET /health` and `GET /api/v1/health` verifying SQLite `SELECT 1` connectivity.

4. **Testing Harness**:
   - Built `tests/conftest.py` with in-memory SQLite isolation and test client overrides.
   - Built `tests/test_health.py` validating root health, prefixed health, and docs redirect.
   - Ran `pytest tests/ -v`: All 3 tests passed.

## Phase 2: Database Models and Repositories
- **Date**: 2026-10-10
- **Goal**: Implement SQLAlchemy 2.x domain models, relationships, database-level constraints (foreign keys, uniqueness, idempotency), status enums, and decoupled repository layer.

### Key Milestones Completed:
1. **Domain Models & Relationships**:
   - `Post` (`app/models/post.py`): Single source of truth for original content with title, source_content, source_url, timestamps.
   - `Variant` (`app/models/variant.py`): Platform-specific text linked to `Post` via cascading FK, with `VariantStatus` enum, platform, and JSON `validation_errors`.
   - `ScheduleSlot` (`app/models/schedule_slot.py`): Scheduled release window linked to `Variant` via cascading FK, with `ScheduleSlotStatus` enum, unique `idempotency_key`, and unique constraint `(variant_id, scheduled_at)`.
   - `PublishAttempt` (`app/models/publish_attempt.py`): Audit record linked to `ScheduleSlot` via cascading FK, with `attempt_number`, `PublishAttemptStatus` enum, `external_post_id`, `error_message`, and timestamps. Unique constraint `(schedule_slot_id, attempt_number)`.
   - SQLite foreign keys enabled on connection via event listener pragma in `app/database.py`.

2. **Repository Layer**:
   - `PostRepository` (`app/repositories/post_repository.py`): CRUD and eager loading for posts.
   - `VariantRepository` (`app/repositories/variant_repository.py`): Creation, retrieval, status/content updates, and cascading deletion.
   - `ScheduleSlotRepository` (`app/repositories/schedule_slot_repository.py`): Slot persistence, idempotency key lookups, and slot status management.
   - `PublishAttemptRepository` (`app/repositories/publish_attempt_repository.py`): Attempt audit logging, latest attempt queries, and success/failure status transitions.

3. **Testing Suite**:
   - Added `tests/test_models_and_repositories.py` covering all 10 required verification scenarios + 3 extra edge cases (cascading deletes, content/status updates, duplicate attempt numbers).
   - Upgraded test harness in `tests/conftest.py` with `join_transaction_mode="create_savepoint"` and SQLite foreign keys pragma.
   - Total test suite: 16 passed, 0 failed.

## Phase 3: Post Ingestion, Variant Generation, and Constraint Validation
- **Date**: 2026-10-10
- **Goal**: Implement Markdown ingestion, SSRF-protected URL ingestion, centralized platform profiles, deterministic variant generation, and constraint validation.

### Key Milestones Completed:
1. **Post Ingestion**:
   - `POST /api/posts`: Ingests Markdown/text with Pydantic validation (rejects empty/whitespace fields).
   - `POST /api/posts/from-url`: Ingests URLs with full SSRF protection (DNS verification rejecting private, loopback, link-local, carrier-grade NAT, and internal addresses across all redirect hops, with 10s timeout and 2MB limit). Uses BeautifulSoup for article text/title extraction.
2. **Platform Profiles & Constraint Validation**:
   - `app/validators/profiles.py`: Centralized constraints for `mock_x` (280 chars, 3 hashtags, conversational), `mock_linkedin` (3000 chars, 5 hashtags, professional discussion prompt), and `telegram` (4096 chars, 5 hashtags, paragraph formatting).
   - `app/validators/validator.py`: `VariantValidator` enforcing length, regex hashtag count (`#\w+`), and explicit reproducible tone rules.
   - `POST /api/variants/{id}/validate`: Revalidation endpoint for existing variants.
3. **Deterministic Variant Generator**:
   - `app/services/variant_generator.py`: `VariantGenerationService` reading solely from stored `Post` source content. Produces platform-tailored variants and validates them.
   - Guaranteed: Valid variants saved as `draft` (review-ready); invalid variants saved as `rejected` with `validation_errors` so they can never enter review.
   - Transactional atomicity: partial batch failures roll back completely.
4. **Testing Suite**:
   - Added `tests/test_ingestion_and_generation.py` with 15 test cases covering markdown ingestion, empty rejection, URL mocking, SSRF attacks, 404/400 errors, content isolation, length/hashtag/tone rule violations, invalid variant rejection, and batch rollbacks.
   - Total test suite: 31 passed, 0 failed.

## Phase 4: Variant Review and Approval Workflow
- **Date**: 2026-10-10
- **Goal**: Implement human review state machine transitions, review endpoints, rejection reasoning, and the scheduling eligibility guard.

### Key Milestones Completed:
1. **Schema & Model Updates**:
   - Added `rejection_reason` (`Text`, nullable=True) to `Variant` model.
   - Updated Pydantic schemas (`VariantResponse`, `VariantUpdateContent`, `RejectVariantRequest`).
2. **Review Domain Service (`ReviewService`)**:
   - `edit_variant`: Allows editing draft/approved variants. Approved variants are automatically reverted to `draft`. Revalidates content against platform profile; invalid edits raise 422 and leave the record untouched. Published variants cannot be edited.
   - `approve_variant`: Revalidates content immediately; promotes valid draft variants to `approved`. Rejects invalid variants with 422. Rejects published/rejected variants.
   - `reject_variant`: Transitions draft/approved variants to `rejected`, recording optional rejection reason.
   - `revise_variant`: Transitions rejected variants back to `draft` for revision.
   - `list_variants`: Filters variants by status (e.g. `status=draft`) and platform.
3. **Scheduling Eligibility Guard (`SchedulingEligibilityGuard`)**:
   - Enforces the critical rule: an unapproved or invalid variant can NEVER be scheduled.
   - Verifies that draft, rejected, published, and invalid variants fail the guard.
   - Reusable domain assertion callable by background scheduling workers.
4. **API Endpoints**:
   - `GET /api/variants`: List variants with status and platform filtering.
   - `GET /api/variants/{id}`: Retrieve variant.
   - `PATCH /api/variants/{id}`: Edit variant content (revalidates, 422 on failure).
   - `POST /api/variants/{id}/approve`: Approve valid draft (422 on failure).
   - `POST /api/variants/{id}/reject`: Reject variant with optional reason.
   - `POST /api/variants/{id}/revise`: Return rejected variant to draft.
5. **Testing Suite**:
   - Added `tests/test_review_workflow.py` with 15 test cases covering all lifecycle transitions, edge cases, error handling, and the scheduling guard.
   - Total test suite: 46 passed, 0 failed.

## Phase 5: SocialPublisher Interface, Mock Adapters, and Telegram
- **Date**: 2026-10-10
- **Goal**: Implement decoupled `SocialPublisher` interface, concrete mock adapters (`MockXPublisher`, `MockLinkedInPublisher`) with local SQLite post persistence, production-grade `TelegramPublisher` via official Bot API, adapter registry, and controlled publishing pipeline enforcing `SchedulingEligibilityGuard`.

### Key Milestones Completed:
1. **`SocialPublisher` Interface & Models**:
   - `app/adapters/publisher_base.py`: Normalized `PublishResult` dataclass (`success`, `platform`, `external_post_id`, `published_at`, `preview`, `error`, `raw_response`, `idempotency_key`) and `SocialPublisher` ABC completely independent from FastAPI/HTTP objects.
   - `MockPlatformPost` (`app/models/mock_post.py`): Dedicated SQLite model for mock publishing persistence with `platform`, `content`, `mock_post_id`, and `created_at`.
   - `MockPostRepository` (`app/repositories/mock_post_repository.py`): CRUD and querying by platform or mock ID.
2. **Concrete Platform Adapters**:
   - `MockXPublisher` (`app/adapters/mock_x.py`): Simulates publishing on X, stores record in `mock_platform_posts`, generates realistic ID (`mock_x_post_<hex>`), and returns formatted preview.
   - `MockLinkedInPublisher` (`app/adapters/mock_linkedin.py`): Simulates publishing on LinkedIn, stores record in `mock_platform_posts`, generates realistic URN (`urn:li:share:mock_<hex>`), and returns formatted feed preview.
   - `TelegramPublisher` (`app/adapters/telegram.py`): Connects to Telegram Bot API `POST /bot<token>/sendMessage`. Sanitizes bot tokens from error messages, enforces 10s HTTP timeout, validates configuration, and parses `message_id`.
3. **Adapter Registry & Configuration**:
   - `app/config.py`: Added `DEFAULT_PUBLISHER: str = "mock_x"`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`.
   - `PublisherRegistry` (`app/adapters/registry.py`): Factory resolving publishers by name or falling back to `DEFAULT_PUBLISHER`. Raises `UnsupportedPublisherError` for unknown platforms.
4. **Publishing Service & Controlled Endpoint**:
   - `PublishingService` (`app/services/publishing_service.py`): Enforces `SchedulingEligibilityGuard` (only valid approved variants allowed), invokes adapter, creates `ScheduleSlot` and `PublishAttempt` records, and transitions `Variant.status` to `published` on success. Retains `approved` on failure for retry.
   - `POST /api/variants/{variant_id}/publish`: Controlled endpoint with optional `?platform=` override. Returns `PublishVariantResponse`.
5. **Testing Suite**:
   - Added `tests/test_publishers_and_adapters.py` covering all 15 scenarios (interface compliance, MockX/MockLinkedIn persistence, Telegram payload/success/error/timeout handling, missing credentials safety, invalid adapter handling, config switching, guard enforcement, publish state lockout, attempt logging, zero real network calls).
   - Total test suite: 61 passed, 0 failed.

## Phase 6: Idempotent Publishing and Duplicate Prevention
- **Date**: 2026-10-10
- **Goal**: Implement deterministic idempotency keys, atomic slot claiming, concurrency collision prevention, replay of completed slots, uncertainty handling for remote APIs (Telegram), and dedicated slot publishing endpoint.

### Key Milestones Completed:
1. **Deterministic Idempotency Key Derivation**:
   - Implemented `derive_slot_idempotency_key(variant_id, scheduled_at)` producing stable keys (`slot:var-<id>:<timestamp>`) across all retries, requests, and worker restarts.
   - Preserved database-level uniqueness constraints on `ScheduleSlot.idempotency_key`.
2. **Schema & Model Enhancements**:
   - Added `processing_started_at`, `completed_at`, `last_error` to `ScheduleSlot`.
   - Added `RECONCILIATION_REQUIRED` status to `ScheduleSlotStatus`.
   - Added `UNCERTAIN` and `RECONCILIATION_REQUIRED` statuses to `PublishAttemptStatus`.
   - Added `idempotency_key` unique column to `MockPlatformPost` for deterministic mock deduplication.
3. **Atomic Slot Claiming & Concurrency Protection**:
   - `ScheduleSlotRepository.claim_slot_for_processing`: Uses atomic conditional update (`WHERE status IN ('pending', 'failed')`) checking `rowcount == 1`.
   - Concurrent worker collisions return `SlotLockedError` (HTTP 409 Conflict).
   - Succeeded attempts replay existing success results (`is_replay=True`) without invoking adapters.
4. **Uncertain External Outcomes (Telegram)**:
   - Differentiated confirmed failures (HTTP 400/403/validation) from uncertain outcomes (timeouts mid-request, dropped connections).
   - Persisted `PublishAttempt` with `STARTED` status *prior* to external calls.
   - Uncertain outcomes transition attempt to `UNCERTAIN` and slot to `RECONCILIATION_REQUIRED`, permanently preventing automatic blind retries and worker crashes from duplicating messages.
5. **Mock Adapter Idempotency**:
   - Updated `MockXPublisher` and `MockLinkedInPublisher` to look up existing mock posts by `idempotency_key`. Repeated calls return the original post ID and preview without creating secondary records.
6. **API Endpoints**:
   - `POST /api/schedule-slots/{slot_id}/publish`: Internal/testing operation verifying idempotent publishing, replaying completed results, returning 409 on locked/reconciliation slots, and rejecting unapproved variants with 400.
7. **Testing Suite**:
   - Added `tests/test_idempotency_and_publishing.py` with 12 comprehensive tests covering deterministic mock publishing, replay of completed slots, 404 missing slots, unapproved/invalid rejections, DB uniqueness, concurrency lockouts, confirmed failure tracking, uncertain outcome lockouts, worker crash simulation, and Telegram error safety.
   - Total test suite: 73 passed, 0 failed.

## Phase 7: Durable Scheduler, Background Worker, and Publish History
- **Date**: 2026-10-10
- **Goal**: Implement durable database-backed scheduling endpoint, deterministic time-injected scheduler service, graceful background worker daemon, audit history queries, and scheduler status observability.

### Key Milestones Completed:
1. **Schedule Creation Endpoint**:
   - Implemented `POST /api/variants/{variant_id}/schedule` accepting timezone-aware UTC normalized timestamps.
   - Enforced `SchedulingEligibilityGuard`: rejects draft, rejected, published, and constraint-violating variants with HTTP 400.
   - Enforces stable idempotency keys and prevents duplicate schedules for the same slot with HTTP 409 Conflict.
2. **Durable Database-Backed Scheduler Service**:
   - Implemented `SchedulerService.process_due_slots` in `app/scheduler/service.py`.
   - Filters pending slots where `scheduled_at <= now` in UTC, processed in bounded batches (`batch_size=10`).
   - Isolates errors per slot so a failure in one slot does not halt remaining batch items.
   - Supports test time injection (`current_time`) for deterministic verification without sleeping.
3. **Background Worker Daemon**:
   - Implemented `SchedulerWorker` in `app/scheduler/worker.py` with standalone entrypoint `python -m app.scheduler.worker`.
   - Supports graceful termination via SIGINT and SIGTERM.
   - Restart-safe: never blindly resets processing slots to pending; routes uncertain attempts to manual reconciliation.
4. **Publish History & Audit Endpoints**:
   - Implemented `GET /api/publish-history` supporting filtering by `variant_id`, `slot_id`, `platform`, `status`, plus pagination (`limit`, `offset`) in reverse chronological order.
   - Implemented `GET /api/publish-history/{attempt_id}` returning detailed audit information or 404.
5. **Observability Endpoint**:
   - Implemented `GET /api/scheduler/status` and `GET /api/schedule-slots/status` returning real-time metrics: pending, due, processing, completed, failed, and reconciliation-required counts.
6. **Testing Suite**:
   - Added `tests/test_scheduler_and_history.py` with 17 tests covering all requirements.
   - Total test suite: 90 passed, 0 failed.

## Phase 8: Integration Tests, Failure Recovery, and Acceptance Checks
- **Date**: 2026-10-10
- **Goal**: Perform comprehensive code audit, build end-to-end integration test suite (Scenarios A through F), build regression and reliability test suite, implement capstone acceptance probes (1 through 6), verify failure recovery and real-world procedures, and validate 100% test pass rate without network dependencies.

### Key Milestones Completed:
1. **Capstone Architectural & Safety Audit**:
   - Audited the entire codebase against capstone requirements. Verified constraint validation before review, scheduling guards before publishing, database uniqueness constraints (`idempotency_key` and `(variant_id, scheduled_at)`), atomic slot claiming against concurrent worker collisions, persistent database state surviving restarts, safe handling of uncertain Telegram timeouts/exceptions via `reconciliation_required`, decoupled `SocialPublisher` adapter interface, and git secret safety.
2. **End-to-End Integration Suite (`tests/test_e2e_integration.py`)**:
   - **Scenario A**: Complete campaign lifecycle from Markdown ingestion to multi-platform generation, review, scheduling, background worker execution, audit history, and status checks.
   - **Scenario B**: Refusal of invalid and unapproved content across review, manual publishing, and scheduling.
   - **Scenario C**: Duplicate publishing prevention and idempotency key enforcement across multiple attempts.
   - **Scenario D**: Background worker crash, restart, and recovery preserving slot states without duplicate publishing.
   - **Scenario E**: Publisher adapter swapping without changes to core business logic.
   - **Scenario F**: Telegram publisher lifecycle with mock external API calls, success parsing, and network failure containment.
3. **Regression and Reliability Suite (`tests/test_regression_and_reliability.py`)**:
   - Covered missing entity 404s, malformed 422s, invalid state machine transitions, database integrity violations, concurrent claiming race conditions, safe failure retries, uncertain timeout lockouts, pagination and audit history filtering, missing configuration safety, and SSRF loopback/private IP blocking.
4. **Capstone Acceptance Probes (`tests/test_acceptance_probes.py`)**:
   - **Probe 1**: Ingests Markdown, verifies variant generation across all configured platforms, verifies constraints, approves valid variants, schedules for future release, and confirms pending slot in DB.
   - **Probe 2**: Ingests URL (mocked HTML), extracts content, generates variants, verifies validation errors on invalid variants, and rejects unapproved scheduling attempts with 400.
   - **Probe 3**: Advances time, runs worker, verifies publishing through configured adapter, confirms `published` status, and checks audit record with `external_post_id`.
   - **Probe 4**: Simulates duplicate worker execution and API calls on the same slot, confirming exactly-once publishing, unchanged post ID, and zero duplicate attempts.
   - **Probe 5**: Tests Telegram adapter with mocked success response, verifies `message_id` capture, tests mocked network failure, and verifies error capture without process crashes.
   - **Probe 6**: Simulates worker crash mid-execution, restarts worker, and verifies recovery without double-publishing.
5. **Operational Runbooks & Manual Verification**:
   - Documented step-by-step procedures in `EVIDENCE.md` for live Telegram publishing verification and manual worker restart recovery with status verification.
6. **Testing Suite**:
   - Added 22 new tests across 3 new test modules.
   - Total test suite: 112 passed, 0 failed, 0 regressions, zero external network calls.

## Phase 9: Final Documentation, Repository Audit, and Submission Readiness
- **Date**: 2026-10-10
- **Goal**: Finalize documentation, audit repository security and .gitignore exclusions, verify zero committed secrets, ensure reproducibility, and prepare repository for public GitHub submission.

### Key Milestones Completed:
1. **README.md Finalization**:
   - Created comprehensive project documentation including problem overview, end-to-end Mermaid architecture flow diagram, detailed platform profiles, state transition rules, full REST API reference (all 19 endpoints), 7-step curl workflow, reliability architecture, and troubleshooting runbook.
2. **Environment & Security Finalization**:
   - Updated `.env.example` to explicitly document all 12 application settings, explaining optional vs. required settings for mock publishing vs. real Telegram.
   - Audited `.gitignore` to ensure `.env`, SQLite databases (`*.db`), virtual environments (`.venv`), pytest caches, and bytecode are completely excluded.
   - Scanned tracked files and history to verify zero committed API keys or bot tokens.
3. **Database Initialization Helper**:
   - Added explicit `init_db()` in `app/database.py` enabling clean one-liner database initialization without starting the full web server.
4. **EVIDENCE.md Finalization**:
   - Added the Capstone Requirement-by-Requirement Evidence Matrix covering all 9 requirement areas with verified status, test commands, observed results, and limitations.

---

## Retrospective & Development Analysis

### 1. Important Architectural Decisions
* **Layered Architecture with Dependency Injection**: The API controllers in `app/api/` depend strictly on domain services and repositories via FastAPI's `Depends(get_db)`. No business or publishing logic resides in route handlers.
* **Database Models as Single Source of Truth**: Original blog posts (`Post.source_content`) are stored permanently. Variants reference posts via foreign keys with cascading deletions, guaranteeing relational integrity.
* **Decoupled `SocialPublisher` Interface**: The publishing interface in `app/adapters/publisher_base.py` is an abstract base class returning a normalized `PublishResult`. It has zero dependencies on FastAPI or HTTP request contexts, allowing adapters (`MockX`, `MockLinkedIn`, `Telegram`) to be swapped or configured independently.
* **Deterministic Idempotency Key Derivation**: Idempotency keys are deterministically generated from `variant_id` and normalized UTC timestamps (`slot:var-<id>:<timestamp>`). This avoids the risk of non-deterministic random UUIDs when clients retry requests.
* **Atomic Slot Claiming**: Rather than relying on application-level locks, release slots are claimed via atomic conditional SQL updates (`UPDATE ... WHERE status IN ('pending', 'failed')`). This provides strong concurrency protection against multiple worker processes.
* **Two-Phase Commit Mitigation for External APIs**: Network timeouts mid-flight during Telegram publishing are handled as `UNCERTAIN` and slots are moved to `RECONCILIATION_REQUIRED`. This honors the Two-Generals Problem by preventing automatic blind resends that could double-post to external channels.

### 2. Bugs Encountered and Fixes
* **Bug 1: SQLite Foreign Key Constraints Disabled by Default**:
  - *Symptom*: Foreign key deletions and constraints initially did not prevent invalid parent references in raw SQLite.
  - *Fix*: Added an event listener on the SQLAlchemy Engine (`@event.listens_for(Engine, "connect")`) to execute `PRAGMA foreign_keys=ON` on every SQLite connection.
* **Bug 2: Fast Test Rollback Savepoints in SQLite**:
  - *Symptom*: Pytest transactional rollbacks were closing SQLite connections between requests in integration tests.
  - *Fix*: Configured the SQLAlchemy session fixture with `join_transaction_mode="create_savepoint"`, enabling clean sub-transaction rollbacks per test case.
* **Bug 3: Sensitive Token Exposure in Telegram API Error Messages**:
  - *Symptom*: When Telegram API raised connection or HTTP errors, the URL containing the raw bot token (`https://api.telegram.org/bot<TOKEN>/...`) was included in default exception strings.
  - *Fix*: Implemented token redaction in `TelegramPublisher._redact_token()`, replacing all occurrences of the bot token in error messages with `[REDACTED_BOT_TOKEN]` before logging or returning results.
* **Bug 4: Ambiguous HTTP Timeouts Leading to Double Post Risk**:
  - *Symptom*: If an HTTP timeout occurred while awaiting Telegram's HTTP response, classifying it as a generic failure would allow an automated retry that could double-post if Telegram had actually received the payload.
  - *Fix*: Differentiated confirmed failures (HTTP 400/403) from uncertain exceptions (read timeouts, dropped connections). Uncertain outcomes quarantine the slot into `RECONCILIATION_REQUIRED`, blocking automatic retries.

### 3. AI Tools Assistance & Corrections
* **Assistance Provided**:
  - AI assisted with generating exhaustive test matrices across edge cases (SSRF private network IP ranges, hashtag regex corner cases, concurrent slot collision tests).
  - AI generated initial boilerplate for layered repository patterns, Pydantic schemas, and FastAPI route structures.
* **Incorrect Suggestions Corrected**:
  - *Correction 1*: An early suggestion proposed generating a random `uuid4()` idempotency key on each publish request. This was rejected because idempotency keys must be deterministic across retries to prevent duplicate publishing.
  - *Correction 2*: An early suggestion proposed automatically retrying failed Telegram publish attempts on any network exception. This was corrected because read timeouts can occur after the external server has already broadcasted the message; blind retries would violate exactly-once guarantees.
  - *Correction 3*: An early suggestion proposed storing social media variants in an in-memory Redis queue for scheduling. This was replaced with SQLite-backed persistent `ScheduleSlot` records to satisfy durability without external infrastructure requirements.

### 4. Tests Performed
* Total tests: **112 automated tests** across 10 test modules.
* Unit tests: Models, repositories, constraint profiles, tone heuristics, publisher interfaces, token redaction.
* Integration tests: Post ingestion, variant generation atomicity, review state machine, scheduling guard, durable scheduler service, publish history.
* Reliability & Recovery tests: Concurrent claiming collisions, crash recovery simulations, duplicate API replays, uncertain timeout quarantining, SSRF protection.
* Acceptance Probes: Probes 1 through 6 verifying complete end-to-end capstone functionality.

### 5. Remaining Limitations
* **Deterministic Template Generation**: Uses deterministic heuristic summarization and formatting. An external LLM API (such as Google Gemini) can be plugged in behind the generator service in future iterations.
* **Heuristic Tone Checks**: Evaluates explicit markers (question prompts, conversational phrases, all-caps suppression, paragraph spacing). Full subjective tone alignment relies on human editorial review.
* **External API Delivery Boundaries**: Telegram does not support client-supplied idempotency keys. Exactly-once delivery across external APIs with network drops is bounded by the Two-Generals Problem.

---

## User Placeholders for Submission

Before submitting the repository or presenting it publicly, please supply the following personal project details:

* **Repository URL**: `https://github.com/[YOUR_GITHUB_USERNAME]/flyrank-capstone-social-studio`
* **Author Name**: `[YOUR_NAME_OR_HANDLE]`
* **Contact Email**: `[YOUR_EMAIL_ADDRESS]`
* **Final Git Commit Hash**: `[RUN 'git rev-parse HEAD' AFTER FIRST COMMIT]`
* **Live Telegram Verification Date / Channel**: `[OPTIONAL: DATE AND CHANNEL WHERE LIVE BOT WAS TESTED, e.g. @MyTestChannel on 2026-10-10]`

