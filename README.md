# Social Media Studio

[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-red.svg)](https://www.sqlalchemy.org/)
[![Tests](https://img.shields.io/badge/tests-112%20passed%20(100%25)-brightgreen.svg)](tests/)
[![Deploy](https://img.shields.io/badge/Render%20%2B%20Neon-Free--Tier%20Ready-purple.svg)](render.yaml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **Ingest long-form blog posts, generate platform-constrained social variants, govern editorial review, and release exactly once through durable, idempotent workers.**

---

## ⚡ Key Highlights

* 🛡️ **Strict Approval Boundary**: Invalid variants never enter review; unapproved variants can **never** be scheduled or published.
* 🔁 **Zero Duplicate Releases**: Deterministic idempotency keys (`slot:var-{id}:{timestamp}`) and atomic conditional SQL updates eliminate double-posts.
* 🌐 **SSRF-Protected Ingestion**: URL scraping validates DNS across all hops, rejecting loopback, link-local, and private IP addresses.
* 🔌 **Interchangeable Adapters**: Decoupled `SocialPublisher` interface supporting **Mock X**, **Mock LinkedIn**, and **Telegram Bot API**.
* ⏳ **Durable Background Scheduler**: Database-backed release queue with crash recovery and bounded batch processing.
* 🚦 **Two-Generals Problem Safety**: Ambiguous external network timeouts quarantine slots into `reconciliation_required` instead of blind resends.
* 🧪 **112 Hermetic Tests**: 100% test pass rate in <2s with zero external network dependencies.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    Ingest[Blog Post Ingestion\nMarkdown or SSRF-Safe URL] --> Post[(Post Storage\nSingle Source of Truth)]
    Post --> Gen[Variant Generator\nPlatform Constraints Profile]
    Gen --> Validate{Validation Engine\nLength / Tags / Tone}
    Validate -- Fails --> RejectDraft[Quarantined as REJECTED]
    Validate -- Passes --> Draft[Saved as DRAFT]
    Draft --> Review[Human Review Workflow\nEdit / Approve / Reject / Revise]
    Review -- Approved --> AppVar[Approved Variant]
    AppVar --> Guard{Scheduling Guard\nEnforces Approval}
    Guard -- Valid & Approved --> Slot[(ScheduleSlot\nUnique Idempotency Key)]
    Slot --> Worker[Background Scheduler Worker\nAtomic Conditional Claims]
    Worker --> Adapter{Publisher Adapter}
    Adapter --> MockX[Mock X\nSQLite Audit]
    Adapter --> MockLI[Mock LinkedIn\nSQLite Audit]
    Adapter --> Telegram[Telegram Bot API\nSecret Redaction]
    Telegram -- Timeout --> Quarantine[Slot: RECONCILIATION_REQUIRED\nNo Blind Retries]
    Adapter -- Success --> History[(Publish History\nAttempt: SUCCEEDED)]
```

---

## 🚀 Quickstart in 60 Seconds

### 1. Setup & Run
```bash
# Clone repository
git clone https://github.com/callmekriztal/social-studio.git
cd social-studio

# Create virtual environment & install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure environment & initialize database
cp .env.example .env
python -c "from app.database import init_db; init_db()"

# Start API server (Live at http://localhost:8000 | Interactive Docs: /docs)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Start Background Scheduler Worker (Separate Terminal)
```bash
python -m app.scheduler.worker
```

### 3. Run Test Suite
```bash
pytest tests/ -v  # 112 passed in ~1.5s
```

---

## 🔄 End-to-End Workflow

```bash
# 1. Ingest a Blog Post
curl -s -X POST http://localhost:8000/api/posts \
  -H "Content-Type: application/json" \
  -d '{"title":"Why Linux Matters","content":"Linux gives developers powerful CLI tools, customizable kernels, and robust package managers."}'

# 2. Generate Platform Variants
curl -s -X POST http://localhost:8000/api/posts/1/generate \
  -H "Content-Type: application/json" \
  -d '{"platforms":["mock_x","mock_linkedin","telegram"]}'

# 3. Approve a Valid Draft
curl -s -X POST http://localhost:8000/api/variants/1/approve

# 4. Schedule for Release (UTC ISO-8601)
curl -s -X POST http://localhost:8000/api/variants/1/schedule \
  -H "Content-Type: application/json" \
  -d '{"scheduled_at":"2026-10-11T12:00:00Z"}'

# 5. Check Publish History Audit Trail
curl -s "http://localhost:8000/api/publish-history?variant_id=1"
```

---

## 📐 Platform Constraints Matrix

Profiles enforced in code via [`app/validators/profiles.py`](app/validators/profiles.py):

| Platform | Max Chars | Max Hashtags | Tone Heuristic Requirement |
| :--- | :---: | :---: | :--- |
| `mock_x` | 280 | 3 | Conversational marker, question, or Call to Action |
| `mock_linkedin` | 3,000 | 5 | Discussion prompt; suppresses all-caps shouting |
| `telegram` | 4,096 | 5 | Structured paragraph breaks (`\n`) for posts >200 chars |

---

## 🔁 Variant State Machine

```
   ┌─────────┐         approve (if valid)        ┌──────────┐
──>│  DRAFT  │──────────────────────────────────>│ APPROVED │
   └─────────┘                                   └──────────┘
     │     ▲                                           │
     │     │ revise                                    │
     │     │                                    reject │
     v     │                                           v
   ┌─────────┐                                   ┌──────────┐
   │REJECTED │<──────────────────────────────────│ (Worker) │
   └─────────┘                                   └──────────┘
                                                       │
                                                       v
                                                 ┌──────────┐
                                                 │PUBLISHED │ (Terminal)
                                                 └──────────┘
```

* Editing an `approved` variant automatically reverts status back to `draft` and triggers re-validation.
* `published` is an immutable terminal state — edits and transitions are blocked.

---

## 📡 REST API Reference

Interactive Swagger documentation available at `/docs` or `/api/v1/health`.

| Category | Method | Path | Description | Key Status |
| :--- | :--- | :--- | :--- | :---: |
| **Health** | `GET` | `/health` | Application status & database connectivity check | `200` |
| **Posts** | `POST` | `/api/posts` | Ingest Markdown or raw text | `201`, `422` |
| | `POST` | `/api/posts/from-url` | Ingest article from URL with SSRF protection | `201`, `400` |
| | `GET` | `/api/posts/{id}` | Retrieve post and associated variants | `200`, `404` |
| | `POST` | `/api/posts/{id}/generate` | Generate variants for requested platforms | `201`, `404` |
| **Review** | `GET` | `/api/variants` | List variants (`?status=draft&platform=...`) | `200` |
| | `GET` | `/api/variants/{id}` | Retrieve variant by ID | `200`, `404` |
| | `PATCH`| `/api/variants/{id}` | Edit variant (auto-reverts approved to draft) | `200`, `422` |
| | `POST` | `/api/variants/{id}/approve` | Promote valid draft to approved | `200`, `422` |
| | `POST` | `/api/variants/{id}/reject` | Reject variant with reason | `200`, `400` |
| | `POST` | `/api/variants/{id}/revise` | Reset rejected variant to draft | `200`, `400` |
| | `POST` | `/api/variants/{id}/validate` | Dry-run constraint validation | `200` |
| **Publishing**| `POST` | `/api/variants/{id}/schedule` | Schedule release slot (`scheduled_at` in UTC) | `201`, `400`, `409` |
| | `POST` | `/api/variants/{id}/publish` | Immediate release through platform adapter | `200`, `400` |
| | `POST` | `/api/schedule-slots/{id}/publish`| Idempotent test execution of release slot | `200`, `409` |
| **Audit** | `GET` | `/api/scheduler/status` | Real-time queue metrics (pending/due/processing) | `200` |
| | `GET` | `/api/publish-history` | Paginated audit log with status/platform filters | `200` |
| | `GET` | `/api/publish-history/{id}` | Full audit attempt details with external IDs | `200`, `404` |

---

## 🔒 Reliability & Concurrency Safeguards

### 1. Deterministic Idempotency Keys
```python
def derive_slot_idempotency_key(variant_id: int, scheduled_at: datetime) -> str:
    ts = scheduled_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"slot:var-{variant_id}:{ts}"
```
Keys are generated deterministically from variant ID and release timestamp — retries never spawn duplicate slots.

### 2. Atomic Slot Claiming
```sql
UPDATE schedule_slots
SET status = 'processing', processing_started_at = :now
WHERE id = :slot_id AND status IN ('pending', 'failed');
```
Workers acquire slots via conditional SQL row updates. Concurrent attempts receive `HTTP 409 Conflict` (`SlotLockedError`), preventing race conditions across multiple workers.

### 3. Safe Replay
Repeating a publish request on a `completed` slot returns cached success data with `"is_replay": true` without re-calling external publisher APIs.

### 4. Remote Uncertainty Quarantine (Two-Generals Problem)
Timeouts during Telegram API calls mark the attempt `UNCERTAIN` and the slot `RECONCILIATION_REQUIRED`. Automatic retries are locked out to protect against duplicate broadcasts.

---

## ☁️ Production Deployment (Render + Neon Postgres)

Deploy free-tier with permanent PostgreSQL persistence and 24/7 background worker:

### 1. Provision Free PostgreSQL on Neon
1. Create a free database at [neon.tech](https://neon.tech).
2. Copy the connection string:
   ```text
   postgresql://user:pass@ep-xyz.region.aws.neon.tech/social_media?sslmode=require
   ```

### 2. One-Click Deploy to Render
1. In [dashboard.render.com](https://dashboard.render.com), click **New +** $\to$ **Blueprint**.
2. Select repository `callmekriztal/social-studio`. Render auto-detects [`render.yaml`](render.yaml).
3. Set `DATABASE_URL` to your Neon PostgreSQL connection string $\to$ Click **Apply**.

> [!TIP]
> `scripts/start_production.sh` runs both Uvicorn and the scheduler worker inside Render's single free container. To prevent Render's 15-minute idle sleep, set up a free 10-minute ping to `/health` on [cron-job.org](https://cron-job.org) or [uptimerobot.com](https://uptimerobot.com).

---

## ⚙️ Environment Variables

| Variable | Default | Purpose |
| :--- | :---: | :--- |
| `DATABASE_URL` | `sqlite:///./social_media.db` | SQLite path or PostgreSQL connection string |
| `DEFAULT_PUBLISHER` | `mock_x` | Primary adapter: `mock_x`, `mock_linkedin`, or `telegram` |
| `TELEGRAM_BOT_TOKEN` | *None* | Bot token from `@BotFather` (optional for mock mode) |
| `TELEGRAM_CHAT_ID` | *None* | Destination chat/channel ID (optional for mock mode) |
| `SCHEDULER_POLL_INTERVAL_SECONDS` | `2.0` | Worker polling frequency |
| `SCHEDULER_BATCH_SIZE` | `10` | Maximum due slots processed per batch |
| `ENVIRONMENT` | `development` | Deployment environment (`development` / `production`) |
| `DEBUG` | `True` | SQL query echo and debug output |

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest --cov=app tests/
```
All **112 automated tests** run hermetically in ~1.5 seconds without network access.
