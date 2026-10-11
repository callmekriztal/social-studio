# Social Media Studio - Backend (`flyrank-capstone-social-studio`)

A robust, production-grade backend service built with FastAPI, SQLAlchemy 2.x, SQLite, and Pydantic. Social Media Studio ingests blog posts from raw Markdown or live URLs (with comprehensive SSRF protection), deterministically generates platform-tailored social media variants (for Mock X, Mock LinkedIn, and Telegram), enforces rigid constraint profiles in code, facilitates a human-in-the-loop review workflow, and durably publishes approved variants exactly once through interchangeable adapters backed by atomic locking and idempotent scheduling.

---

## 1. Project Overview

### What Social Media Studio Does
Social Media Studio automates the transformation of long-form blog content into platform-tailored social media posts while guaranteeing editorial oversight and high-reliability publication:
1. **Ingests Long-Form Content**: Ingests raw Markdown or scrapes article content from public web pages.
2. **Generates Tailored Variants**: Automatically drafts concise, engaging variants for X, LinkedIn, and Telegram.
3. **Validates Hard Constraints**: Enforces length, hashtag limits, and tone requirements before drafts ever enter the review queue.
4. **Governs Human Review**: Manages explicit status transitions (`draft` → `approved` / `rejected`), automatically reverting approved variants to `draft` upon editing.
5. **Schedules & Publishes Durably**: Backs scheduled release slots with deterministic idempotency keys, atomic database slot locking, background polling, and an audit trail of every attempt.

### The Problem It Solves
Multi-channel social media publishing systems frequently suffer from:
* **Constraint Violations**: Posts exceeding character counts or hashtag rules get rejected by external APIs or look unformatted.
* **Accidental Unapproved Releases**: Race conditions or UI bugs accidentally publish unreviewed drafts.
* **Duplicate Broadcasts**: Retries after timeouts, worker crashes, or concurrent executions double-post content to live feeds.
* **Silent Failures**: Network drops during API calls leave systems uncertain whether a post succeeded.

### Core Reliability Challenges & Solutions
* **The Approval Boundary**: An unapproved or invalid variant can *never* be scheduled or published. Enforced at the domain layer via `SchedulingEligibilityGuard`.
* **Exactly-Once Release**: Slots use deterministic idempotency keys (`slot:var-<id>:<timestamp>`) enforced by database uniqueness constraints. Completed slots return cached replay responses without re-executing adapters.
* **Concurrency Protection**: Workers atomically claim release slots using conditional SQL updates (`UPDATE ... WHERE status IN ('pending', 'failed')`). Competing workers receive `HTTP 409 Conflict`.
* **Worker Crash & Restart Recovery**: The scheduler worker queries only `pending` slots. Slots left in `processing` or `reconciliation_required` from crashes are never blindly re-executed.
* **Two-Generals Problem & Network Uncertainty**: Timeouts during live Telegram calls are classified as `uncertain` rather than assumed failures, locking the slot into `reconciliation_required` to prevent accidental resends.

### Tech Stack
* **Language & Runtime**: Python 3.12+ (tested on Python 3.14)
* **API Framework**: FastAPI 0.110+, Starlette, Pydantic v2, pydantic-settings
* **Database & ORM**: SQLAlchemy 2.0 (with SQLite foreign key pragmas & transactional isolation)
* **Network & Ingestion**: HTTPX, BeautifulSoup4
* **Testing Harness**: Pytest 8.0+ (112 automated tests, zero external network dependencies)

---

## 2. System Architecture

```mermaid
flowchart TD
    Ingest[Blog Post Ingestion\nPOST /api/posts & /from-url\nSSRF Protection] --> Post[(Post Storage\nSingle Source of Truth)]
    Post --> Gen[Variant Generator\nPOST /api/posts/:id/generate]
    Gen --> Validate{Constraint Validation\nLength, Hashtags, Tone}
    Validate -- Invalid --> RejectedDraft[Saved as REJECTED\nNever Enters Review]
    Validate -- Valid --> Draft[Saved as DRAFT]
    Draft --> Review[Human Review Workflow\nPATCH /approve /reject /revise]
    Review -- Approved --> ApprovedVariant[Approved Variant]
    ApprovedVariant --> Guard{Scheduling Eligibility Guard}
    Guard -- Rejects Unapproved --> Error400[HTTP 400 Bad Request]
    Guard -- Valid & Approved --> Scheduler[Durable Scheduler\nPOST /api/variants/:id/schedule]
    Scheduler --> DB_Slot[(ScheduleSlot in SQLite\nUnique Idempotency Key & Slot Time)]
    DB_Slot --> Worker[Background Scheduler Worker Daemon\nBounded Batch & Due Time Query]
    Worker --> AtomicClaim[Atomic Slot Claiming\nUPDATE ... WHERE status IN ('pending', 'failed')]
    AtomicClaim -- Conflict / Locked --> SlotLocked[HTTP 409 / Skipped by Worker]
    AtomicClaim -- Claimed Successfully --> PreAttempt[(PublishAttempt: STARTED)]
    PreAttempt --> PubInterface[SocialPublisher Interface\nAbstract Base Class]
    PubInterface --> Adapters{Configured Adapter}
    Adapters --> MockX[MockXPublisher\nLocal SQLite Persistence]
    Adapters --> MockLI[MockLinkedInPublisher\nLocal SQLite Persistence]
    Adapters --> Telegram[TelegramPublisher\nOfficial Bot API]
    Telegram -- Timeout / Drop --> Uncertain[Attempt: UNCERTAIN\nSlot: RECONCILIATION_REQUIRED\nNo Blind Resends]
    MockX & MockLI & Telegram -- Success --> Completed[Slot: COMPLETED\nVariant: PUBLISHED]
    Completed --> History[(Publish History Audit Trail\nPublishAttempt: SUCCEEDED)]
```

---

## 3. Directory Structure

```
flyrank-capstone-social-studio/
├── app/
│   ├── main.py                  # FastAPI application factory, lifespan table creation & routes
│   ├── config.py                # Pydantic Settings reading .env and system environment variables
│   ├── database.py              # SQLAlchemy engine, sessionmaker, SQLite PRAGMA listener, init_db()
│   ├── api/
│   │   ├── router.py            # Aggregates sub-routers into a unified API router
│   │   └── routes/
│   │       ├── health.py        # Health check endpoints (/health and /api/v1/health)
│   │       ├── posts.py         # Markdown and URL ingestion, post retrieval & variant generation
│   │       ├── variants.py      # Review, edit, approve, reject, revise, validate, schedule, publish
│   │       ├── schedule_slots.py# Internal slot publish and slot status endpoints
│   │       ├── publish_history.py# Audit history listing, filtering, pagination, and attempt details
│   │       └── scheduler.py     # Scheduler observability and status metrics
│   ├── models/                  # SQLAlchemy ORM persistent data models
│   │   ├── enums.py             # Platform, VariantStatus, ScheduleSlotStatus, PublishAttemptStatus
│   │   ├── post.py              # Post model (single source of truth for ingested content)
│   │   ├── variant.py           # Variant model with JSON validation errors & rejection reason
│   │   ├── schedule_slot.py     # ScheduleSlot with unique idempotency key & composite slot constraint
│   │   ├── publish_attempt.py   # PublishAttempt audit model
│   │   └── mock_post.py         # MockPlatformPost persistent storage for mock adapters
│   ├── schemas/                 # Pydantic validation & serialization models
│   │   ├── health.py            # Health check response schemas
│   │   ├── post.py              # Post ingestion request & response schemas
│   │   ├── variant.py           # Generation, review, update & validation schemas
│   │   └── scheduler.py         # Scheduling request, slot response, history, and status schemas
│   ├── services/                # Core domain and business logic
│   │   ├── url_extractor.py     # SSRF-protected article extractor (DNS verification & safety)
│   │   ├── variant_generator.py # Deterministic multi-platform variant generator
│   │   ├── review_service.py    # State transitions: edit, approve, reject, revise
│   │   ├── scheduling_guard.py  # Guard enforcing only valid approved variants may be scheduled
│   │   └── publishing_service.py# Idempotent publishing pipeline with atomic slot locking
│   ├── repositories/            # Data access layer encapsulating database queries
│   │   ├── post_repository.py
│   │   ├── variant_repository.py
│   │   ├── schedule_slot_repository.py
│   │   ├── publish_attempt_repository.py
│   │   └── mock_post_repository.py
│   ├── validators/              # Platform constraint validators and profiles
│   │   ├── profiles.py          # Platform constraint profiles (mock_x, mock_linkedin, telegram)
│   │   └── validator.py         # Character length, hashtag counts, and explicit tone checks
│   ├── adapters/                # External integration adapters
│   │   ├── publisher_base.py    # Abstract SocialPublisher interface and PublishResult
│   │   ├── mock_x.py            # Mock X publisher with local SQLite persistence & idempotency
│   │   ├── mock_linkedin.py     # Mock LinkedIn publisher with local SQLite persistence & idempotency
│   │   ├── telegram.py          # Telegram Bot API publisher with secret redaction & timeout safety
│   │   └── registry.py          # Publisher registry and config resolver
│   └── scheduler/               # Durable background scheduling & worker
│       ├── service.py           # Database-backed scheduler processing due slots (time-injected)
│       └── worker.py            # Standalone background worker daemon (SIGINT/SIGTERM safe)
├── tests/                       # Automated test suite (112 tests, 0 network calls)
│   ├── conftest.py              # Pytest fixtures (in-memory SQLite session, test client)
│   ├── test_health.py           # Health endpoints and routing
│   ├── test_models_and_repositories.py # Database models, constraints, and cascades
│   ├── test_ingestion_and_generation.py # Ingestion, SSRF protection, constraint validation
│   ├── test_review_workflow.py  # Variant review state machine and scheduling guard
│   ├── test_publishers_and_adapters.py # Publisher adapters and Telegram tests
│   ├── test_idempotency_and_publishing.py # Idempotency, replay, and concurrency protection
│   ├── test_scheduler_and_history.py # Durable scheduler, background worker, and publish history
│   ├── test_e2e_integration.py  # End-to-end integration scenarios (A through F)
│   ├── test_regression_and_reliability.py # Reliability, error handling, DB constraints & SSRF
│   └── test_acceptance_probes.py # Capstone acceptance probes (1 through 6)
├── scripts/
│   └── run_dev.sh               # Local development launch script
├── .env.example                 # Environment configuration template
├── .gitignore                   # Git ignore rules for Python, SQLite, caches, and secrets
├── requirements.txt             # Project dependencies
├── BUILDLOG.md                  # Honest development log and architectural decisions
├── EVIDENCE.md                  # Requirement-by-requirement verification checklist and test logs
└── README.md                    # Project documentation
```

---

## 4. Platform Constraint Profiles

Profiles are configured centrally in [`app/validators/profiles.py`](file:///home/celeste/social_media/app/validators/profiles.py):

| Platform | Max Characters | Max Hashtags | Tone Requirement | Heuristic Tone Rules |
| :--- | :---: | :---: | :--- | :--- |
| `mock_x` | 280 | 3 | Concise and conversational | Must include conversational markers, question, or CTA (e.g., *Check it out!*, *What are your thoughts?*). |
| `mock_linkedin` | 3000 | 5 | Professional and informative | Must include a discussion-oriented question/prompt; prohibits excessive shouting/all-caps words. |
| `telegram` | 4096 | 5 | Informative and readable | Requires structured paragraph breaks (`\n`) for posts exceeding 200 characters. |

---

## 5. Variant Lifecycle & State Transitions

```
               +-------------------------------------------------------+
               |                                                       |
               v                                                       |
         +-----------+          approve (if valid)        +------------+
  -----> |   DRAFT   | ---------------------------------> |  APPROVED  |
         +-----------+                                    +------------+
           |       ^                                             |
           |       | revise                                      |
    reject |       |                                      reject |
           v       |                                             |
         +-----------+ <-----------------------------------------+
         | REJECTED  |
         +-----------+
               |
               | (published state reached via scheduler/publishing pipeline)
               v
         +-----------+
         | PUBLISHED | (Immutable terminal state)
         +-----------+
```

### State Transition Rules
1. **`draft` → `approved`**: Only permitted if the variant satisfies all platform constraints at the moment of approval.
2. **`draft` → `rejected`**: Editorial rejection; stores an optional rejection reason.
3. **`approved` → `rejected`**: Editorial cancellation; stores an optional rejection reason.
4. **`approved` → `draft`**: Automatically triggered whenever an approved variant is edited via `PATCH`.
5. **`rejected` → `draft`**: Explicitly returned to draft via `POST /api/variants/{id}/revise` for revision.
6. **`published`**: Immutable terminal state. Edits, approvals, rejections, or revisions on published variants are rejected.

---

## 6. Features & System Capabilities

### Implemented Features
* **Post Ingestion**:
  - Ingest Markdown or raw text (`POST /api/posts`).
  - Ingest live URLs (`POST /api/posts/from-url`) with DNS resolution filtering to block SSRF attacks against private, loopback, link-local, and internal IP addresses.
  - Store original content as the single source of truth in the `Post` table.
* **Variant Generation & Validation**:
  - Deterministic generation for `mock_x`, `mock_linkedin`, and `telegram`.
  - In-code validation enforcing length, hashtag counts, and tone requirements.
  - Automatic isolation: variants violating rules are saved as `rejected` with structured errors and never enter the review queue.
* **Review & Approval State Machine**:
  - Complete review lifecycle endpoints: list, view, edit, approve, reject, revise.
  - Automatic state rollback to `draft` upon editing approved variants.
  - Strict `SchedulingEligibilityGuard` preventing unapproved or invalid variants from being scheduled or published.
* **Social Publisher Adapters**:
  - Decoupled `SocialPublisher` abstract base class returning normalized `PublishResult`.
  - `MockXPublisher`: Formats 280-char posts, simulates unique IDs, stores in SQLite `mock_platform_posts`.
  - `MockLinkedInPublisher`: Formats feed shares, generates share URNs, stores in SQLite `mock_platform_posts`.
  - `TelegramPublisher`: Publishes to official Telegram Bot API, enforces 10s timeout, parses `message_id`, and redacts secrets.
  - Publisher registry with fallback to `DEFAULT_PUBLISHER` from environment.
* **Idempotency & Duplicate Prevention**:
  - Deterministic idempotency keys (`slot:var-<id>:<timestamp>`).
  - Database-enforced uniqueness constraints on slots and idempotency keys.
  - Atomic conditional slot claiming (`UPDATE ... WHERE status IN ('pending', 'failed')`).
  - Safe replay behavior for completed slots (`is_replay: true`) without re-calling publisher adapters.
  - Remote uncertainty handling: network timeouts transition attempts to `UNCERTAIN` and slots to `RECONCILIATION_REQUIRED`, permanently preventing automatic duplicate resends.
* **Durable Scheduling & Background Worker**:
  - Timezone-aware scheduling endpoint normalized to UTC (`POST /api/variants/{id}/schedule`).
  - Bounded batch scheduler service (`batch_size=10`) reading from SQLite.
  - Standalone worker daemon (`python -m app.scheduler.worker`) with graceful shutdown (`SIGINT`, `SIGTERM`) and crash restart safety.
* **Publish History & Observability**:
  - Full audit logging in `publish_attempts` table.
  - Reverse-chronological history endpoint with pagination and multi-attribute filtering (`GET /api/publish-history`).
  - Real-time scheduler metrics endpoint (`GET /api/scheduler/status`).

### Feature Boundaries & Explicit Limitations
* **Deterministic Template Generation**: The current generator constructs structured summaries and hashtags deterministically. An LLM provider (e.g. Gemini) can be connected behind the generator service in the future.
* **Heuristic Tone Validation**: Tone verification evaluates explicit heuristics (questions, conversational prompts, all-caps suppression, paragraph formatting). Nuanced subjective tone is governed by human review.
* **Two-Generals Problem**: Telegram Bot API does not accept client-provided idempotency keys. While local database operations guarantee logical exactly-once execution for mock publishers, a dropped TCP connection during external Telegram response transmission cannot be proven delivered or undelivered by the client. The system safely prevents duplicate posts by quarantining ambiguous slots into `reconciliation_required` rather than issuing blind retries.

---

## 7. Requirements

* **Python**: Python 3.12 or newer (tested and verified on Python 3.14).
* **Operating System**: Linux, macOS, or Windows (WSL recommended).
* **Dependencies**: Declared in `requirements.txt` (FastAPI, Uvicorn, SQLAlchemy 2.x, Pydantic, pydantic-settings, python-dotenv, httpx, beautifulsoup4, pytest).

---

## 8. Installation & Setup

Follow these exact commands to clone, configure, initialize, and run the service locally:

### 1. Clone the Repository
```bash
git clone https://github.com/<your-username>/flyrank-capstone-social-studio.git
cd flyrank-capstone-social-studio
```

### 2. Create and Activate Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Variables
```bash
cp .env.example .env
```
*(The default configuration uses `DEFAULT_PUBLISHER="mock_x"` and requires zero external credentials or API keys).*

### 5. Initialize Database
Initialize the SQLite database schema:
```bash
python -c "from app.database import init_db; init_db()"
```
*(Alternatively, starting the FastAPI application runs table creation automatically during startup lifespan).*

### 6. Start the API Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
*Or use the provided convenience script:*
```bash
./scripts/run_dev.sh
```
*The API is now live at `http://localhost:8000`. Interactive OpenAPI documentation is available at `http://localhost:8000/docs`.*

### 7. Start the Scheduler Worker (in a separate terminal)
In a second terminal window (with `.venv` activated):
```bash
python -m app.scheduler.worker
```
*The worker continuously polls the database for due release slots and executes them gracefully.*

### 8. Run the Automated Test Suite
In another terminal (with `.venv` activated):
```bash
pytest tests/ -v
```
*Executes all 112 automated unit, integration, reliability, and acceptance tests in ~1.5 seconds with zero external network calls.*

---

## 9. Environment Configuration

The application is configured using environment variables loaded from the host or a `.env` file via `pydantic-settings`:

| Variable | Type | Default | Description & Safe Values |
| :--- | :---: | :---: | :--- |
| `PROJECT_NAME` | string | `Social Media Studio` | Application name displayed in OpenAPI documentation. |
| `ENVIRONMENT` | string | `development` | Deployment environment (`development`, `staging`, `production`). |
| `DEBUG` | boolean | `True` | Enables debug mode and SQL query logging. |
| `API_V1_PREFIX` | string | `/api/v1` | URL prefix for versioned endpoints. |
| `DATABASE_URL` | string | `sqlite:///./social_media.db` | SQLAlchemy connection string. Default uses local SQLite. |
| `DEFAULT_PUBLISHER` | string | `mock_x` | Primary platform adapter: `mock_x`, `mock_linkedin`, or `telegram`. |
| `TELEGRAM_BOT_TOKEN` | string | `None` | **Optional.** Real Telegram bot token from `@BotFather`. |
| `TELEGRAM_CHAT_ID` | string | `None` | **Optional.** Telegram destination channel or chat ID (e.g. `-1001234567890`). |
| `ENABLE_MOCK_X` | boolean | `True` | Toggles availability of Mock X adapter. |
| `ENABLE_MOCK_LINKEDIN` | boolean | `True` | Toggles availability of Mock LinkedIn adapter. |
| `SCHEDULER_POLL_INTERVAL_SECONDS` | float | `2.0` | Frequency in seconds at which the background worker polls for due slots. |
| `SCHEDULER_BATCH_SIZE` | integer | `10` | Maximum number of due slots processed in a single worker batch. |

### Selecting Platform Adapters
* **To publish in mock mode without any external accounts**:
  Leave `DEFAULT_PUBLISHER="mock_x"` or `DEFAULT_PUBLISHER="mock_linkedin"`. No Telegram credentials are needed.
* **To publish to a real Telegram channel**:
  1. Create a bot with [@BotFather](https://t.me/BotFather) and get the bot token.
  2. Add the bot as an administrator to your Telegram channel.
  3. Set `DEFAULT_PUBLISHER="telegram"`, `TELEGRAM_BOT_TOKEN="<token>"`, and `TELEGRAM_CHAT_ID="<chat_id>"` in `.env`.

---

## 10. Complete REST API Reference

All endpoints are accessible at the root `/api/...` and prefixed under `/api/v1/...`.

### Health & Monitoring
* `GET /health` (or `GET /api/v1/health`)
  - **Purpose**: Verifies application status and database connectivity (`SELECT 1`).
  - **Response**: `200 OK`
    ```json
    {
      "status": "healthy",
      "environment": "development",
      "database": "healthy",
      "timestamp": "2026-10-10T14:35:46.789252Z"
    }
    ```

### Posts Ingestion & Retrieval
* `POST /api/posts`
  - **Purpose**: Ingests blog post from Markdown or raw text.
  - **Status**: `201 Created` (`422 Unprocessable Content` on empty title or content).
  - **Request**:
    ```json
    {
      "title": "Why Linux Is Useful for Developers",
      "content": "Linux gives developers powerful command-line tools..."
    }
    ```
  - **Response**: Returns created Post model with `id`, `source_content`, `created_at`.

* `POST /api/posts/from-url`
  - **Purpose**: Ingests article from a web URL with SSRF protection.
  - **Status**: `201 Created` (`400 Bad Request` on SSRF/timeout/empty HTML, `422` on invalid URL format).
  - **Request**:
    ```json
    {
      "url": "https://example.com/blog/linux-for-developers"
    }
    ```

* `GET /api/posts/{post_id}`
  - **Purpose**: Retrieves a post and its associated variants.
  - **Status**: `200 OK` (`404 Not Found` if missing).

* `POST /api/posts/{post_id}/generate`
  - **Purpose**: Generates social media variants for specified platforms.
  - **Status**: `201 Created` (`404 Not Found` if post missing, `422` on invalid platforms).
  - **Request**:
    ```json
    {
      "platforms": ["mock_x", "mock_linkedin", "telegram"]
    }
    ```

### Variants Review & Approval
* `GET /api/variants`
  - **Purpose**: Lists variants filtered by status and/or platform.
  - **Query Params**: `?status=draft&platform=mock_x`
  - **Status**: `200 OK`.

* `GET /api/variants/{variant_id}`
  - **Purpose**: Retrieves a variant by ID.
  - **Status**: `200 OK` (`404 Not Found` if missing).

* `PATCH /api/variants/{variant_id}`
  - **Purpose**: Edits variant content. Content is validated; approved variants automatically revert to `draft`.
  - **Status**: `200 OK` (`400 Bad Request` if published, `422` if content violates constraints).
  - **Request**:
    ```json
    {
      "content": "Why Linux is great for developers? Check it out! #Linux #Dev"
    }
    ```

* `POST /api/variants/{variant_id}/approve`
  - **Purpose**: Revalidates and approves a draft variant.
  - **Status**: `200 OK` (`400 Bad Request` if already published/rejected, `422` if constraints fail).

* `POST /api/variants/{variant_id}/reject`
  - **Purpose**: Rejects a draft or approved variant with an optional reason.
  - **Status**: `200 OK` (`400 Bad Request` if published).
  - **Request**:
    ```json
    {
      "reason": "Tone is too informal for our corporate guidelines."
    }
    ```

* `POST /api/variants/{variant_id}/revise`
  - **Purpose**: Returns a rejected variant to `draft` status for editing.
  - **Status**: `200 OK` (`400 Bad Request` if not currently rejected).

* `POST /api/variants/{variant_id}/validate`
  - **Purpose**: Revalidates variant content against platform constraints without mutating state.
  - **Status**: `200 OK`.

### Scheduling & Publishing
* `POST /api/variants/{variant_id}/schedule`
  - **Purpose**: Schedules an approved variant for publication at a timezone-aware timestamp.
  - **Status**: `201 Created` (`400 Bad Request` if variant is not valid & approved, `409 Conflict` if duplicate slot).
  - **Request**:
    ```json
    {
      "scheduled_at": "2026-10-10T16:00:00Z"
    }
    ```

* `POST /api/variants/{variant_id}/publish`
  - **Purpose**: Immediately publishes an approved variant via platform adapter.
  - **Query Params**: `?platform=mock_x` (optional override).
  - **Status**: `200 OK` (`400 Bad Request` if variant unapproved or already published).

* `POST /api/schedule-slots/{slot_id}/publish`
  - **Purpose**: Internal/testing endpoint to process a specific schedule slot idempotently.
  - **Status**: `200 OK` (`400` if unapproved, `404` if slot missing, `409 Conflict` if slot locked/uncertain).

### Observability & History
* `GET /api/scheduler/status` (or `GET /api/schedule-slots/status`)
  - **Purpose**: Returns real-time metrics across all schedule slots.
  - **Status**: `200 OK`.
  - **Response**:
    ```json
    {
      "pending_slots": 0,
      "due_slots": 0,
      "processing_slots": 0,
      "completed_slots": 1,
      "failed_slots": 0,
      "reconciliation_required_slots": 0,
      "current_time": "2026-10-10T16:05:00Z"
    }
    ```

* `GET /api/publish-history`
  - **Purpose**: Lists publish attempt audit records in reverse chronological order.
  - **Query Params**: `?variant_id=1&slot_id=1&platform=mock_x&status=succeeded&limit=10&offset=0`.
  - **Status**: `200 OK`.

* `GET /api/publish-history/{attempt_id}`
  - **Purpose**: Retrieves detailed audit attempt including external post ID and error logs.
  - **Status**: `200 OK` (`404 Not Found` if missing).

---

## 11. End-to-End Example Workflow

Below is a complete, realistic curl workflow taking a blog post through ingestion, generation, review, approval, scheduling, worker execution, and audit inspection:

### Step 1: Ingest a Markdown Blog Post
```bash
curl -X POST http://localhost:8000/api/posts \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Why Linux Is Useful for Developers",
    "content": "Linux gives developers powerful command-line tools, customizable kernels, and package managers like apt and pacman. Mastering Linux improves workflow efficiency and server deployment confidence."
  }'
```
*Response: Returns Post `id: 1`.*

### Step 2: Generate Platform Variants
```bash
curl -X POST http://localhost:8000/api/posts/1/generate \
  -H "Content-Type: application/json" \
  -d '{
    "platforms": ["mock_x", "mock_linkedin", "telegram"]
  }'
```
*Response: Generates 3 variants in `draft` status.*

### Step 3: Inspect Draft Variants Awaiting Review
```bash
curl "http://localhost:8000/api/variants?status=draft"
```
*Response: Returns draft list including `mock_x` variant with `id: 1`.*

### Step 4: Approve the Variant
```bash
curl -X POST http://localhost:8000/api/variants/1/approve
```
*Response: Revalidates constraints and transitions variant status to `approved`.*

### Step 5: Schedule the Approved Variant
```bash
curl -X POST http://localhost:8000/api/variants/1/schedule \
  -H "Content-Type: application/json" \
  -d '{
    "scheduled_at": "2026-10-10T16:00:00Z"
  }'
```
*Response: Returns ScheduleSlot `id: 1` with `status: "pending"` and deterministic idempotency key `slot:var-1:20261010160000`.*

### Step 6: Process the Slot via Background Worker
If the worker is running in the background (`python -m app.scheduler.worker`), it automatically claims and publishes the slot as soon as `scheduled_at <= now_utc`.
*(To test slot execution manually without waiting):*
```bash
curl -X POST http://localhost:8000/api/schedule-slots/1/publish
```
*Response: Returns `success: true`, `is_replay: false`, and `external_post_id: "mock_x_post_..."`.*

### Step 7: Inspect Publish History Audit Trail
```bash
curl "http://localhost:8000/api/publish-history?variant_id=1"
```
*Response: Returns audit record confirming `status: "succeeded"`, external ID, and attempt timestamp.*

---

## 12. Reliability Architecture & Idempotency Safeguards

### Deterministic Idempotency Key Derivation
Idempotency keys are deterministically generated from the variant ID and normalized UTC timestamp:
```python
def derive_slot_idempotency_key(variant_id: int, scheduled_at: datetime) -> str:
    ts_str = scheduled_at.astimezone(timezone.utc).strftime('%Y%m%d%H%M%S')
    return f'slot:var-{variant_id}:{ts_str}'
```
* **Benefit**: Repeated requests, retries, and worker restarts always re-derive the exact same key. Random UUIDs are never used.
* **Database Guarantee**: Backed by a `UNIQUE` constraint on `schedule_slots.idempotency_key`.

### Atomic Slot Claiming
To eliminate race conditions between concurrent worker threads or cluster instances, slots are claimed using an atomic conditional SQL update:
```sql
UPDATE schedule_slots
SET status = 'processing', processing_started_at = :now, updated_at = :now
WHERE id = :slot_id AND status IN ('pending', 'failed');
```
* If `rowcount == 1`: The caller acquires exclusive ownership.
* If `rowcount == 0`: Another process has already claimed or completed the slot. Competing callers receive `HTTP 409 Conflict` (`SlotLockedError`), preventing duplicate in-flight publishing attempts.

### Safe Replay Behavior
Repeating a publish request on an already `completed` slot returns the original success result with `is_replay: true`:
* Publisher adapters are never invoked a second time.
* No duplicate records or extra attempts are generated.
* The client receives the original `external_post_id` and audit metadata.

### Crash Recovery & Remote API Uncertainty (Two-Generals Problem)
1. **Pre-Call Attempt Logging**: A `PublishAttempt` record with status `started` is committed to SQLite *prior* to initiating external network requests. If a worker process crashes mid-flight, the slot remains locked in `processing` status, preventing subsequent workers from blindly re-executing it.
2. **Confirmed Failures vs. Uncertain Outcomes**:
   * **Confirmed Failure** (e.g. HTTP 400, 403, validation error): The API explicitly confirmed the message was rejected. The attempt transitions to `failed` and the slot resets to `failed` for safe retry.
   * **Uncertain Outcome** (e.g. HTTP read timeout, dropped TCP connection): The external platform may have broadcast the message, but the confirmation response was lost in transit. The attempt transitions to `uncertain` and the slot moves to `reconciliation_required`.
   * **No Blind Resends**: Automatic retries and background workers ignore `reconciliation_required` slots, preventing duplicate posts until an operator verifies the channel status.

---

## 13. Troubleshooting Runbook

### 1. Database Locked (`sqlite3.OperationalError: database is locked`)
* **Cause**: Multiple worker threads or long-running transactions attempting simultaneous write access to SQLite.
* **Fix**: The application enables `PRAGMA foreign_keys=ON` and `check_same_thread=False`. Ensure you are not running long transactions outside of request scopes. For high-concurrency production deployments, swap `DATABASE_URL` to PostgreSQL.

### 2. Variant Rejected with 422 During Approval
* **Cause**: The variant content violates character length limits, hashtag limits, or tone heuristics for the chosen platform.
* **Fix**: Inspect the structured `errors` array in the 422 response. Use `PATCH /api/variants/{id}` to revise the text (e.g., shorten length, add discussion prompt, or remove excess hashtags) and re-approve.

### 3. Worker Not Processing Due Slots
* **Cause**: The background worker daemon is not running or scheduled slots have a future timestamp.
* **Fix**:
  1. Check worker logs in terminal: `python -m app.scheduler.worker`.
  2. Inspect scheduler status metrics: `curl http://localhost:8000/api/scheduler/status`.
  3. Ensure slot timestamp is in the past (`scheduled_at <= now_utc`).

### 4. Telegram Publishing Fails (`TelegramPublisherError`)
* **Cause**: Missing or invalid `TELEGRAM_BOT_TOKEN`, invalid `TELEGRAM_CHAT_ID`, or the bot has not been added as an administrator to the target channel.
* **Fix**:
  1. Verify credentials in `.env`: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
  2. Confirm the bot has admin rights with post permissions in the channel.
  3. Note that the adapter sanitizes credentials from all exception logs to prevent token leakage.
  4. To test without Telegram credentials, set `DEFAULT_PUBLISHER="mock_x"`.

---

## 14. Development & Testing

Run the test suite with pytest:
```bash
.venv/bin/pytest tests/ -v
```
All **112 automated tests** execute in ~1.5 seconds without any internet connection or real Telegram credentials.
# social-studio
