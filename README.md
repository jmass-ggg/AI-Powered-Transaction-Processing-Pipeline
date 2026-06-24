# AI-Powered Transaction Processing Pipeline

A fully async backend pipeline that accepts a raw financial transactions CSV, cleans the data, detects anomalies, classifies missing categories using an LLM (with a deterministic fallback), generates a risk summary, and exposes everything through a polling REST API.

Built as a Backend + DevOps internship assignment.

---

## Architecture

The solution follows a decoupled async architecture:

```
Client (curl / Postman / Frontend)
         |
         v
   FastAPI (port 8000)
         |
         |── Saves CSV to disk
         |── Creates Job record in PostgreSQL (status: pending)
         |── Enqueues Celery task
         |
         v
   Redis (message broker)
         |
         v
   Celery Worker
         |
         |── 1. Reads CSV from disk
         |── 2. Cleans data (dates, amounts, currencies, deduplication)
         |── 3. Detects anomalies (statistical + currency mismatch + suspicious notes)
         |── 4. Classifies missing categories via LLM or fallback
         |── 5. Builds aggregated stats
         |── 6. Generates LLM narrative summary
         |── 7. Persists transactions + summary to PostgreSQL
         |── 8. Marks Job as completed
         |
         v
   PostgreSQL (port 5433)
         |
         v
   pgAdmin UI (port 5050)  ← browse the database visually
```

### Why this design?

- The API returns immediately after enqueueing — no blocking on slow LLM calls.
- The worker is fully stateless and can be horizontally scaled by adding more replicas.
- LLM failures never fail the job — a deterministic fallback always runs.
- All services are containerized and wired via Docker Compose.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Task queue | Celery |
| Broker | Redis 7 |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2 |
| Data processing | Pandas |
| LLM | Gemini API (optional, with fallback) |
| DB Admin UI | pgAdmin 4 |
| Containerization | Docker + Docker Compose |

---

## Quick Start

```bash
# Clone and start everything
docker compose up --build
```

Services that start:

| Service | URL |
|---|---|
| FastAPI | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| pgAdmin | http://localhost:5050 |
| PostgreSQL | localhost:5433 |
| Redis | localhost:6380 |

---

## pgAdmin — Database UI

pgAdmin is included in Docker Compose for browsing the database visually.

Access it at: **http://localhost:5050**

Login credentials:
- Email: `admin@example.com`
- Password: `admin`

### Connect to the database in pgAdmin

1. Open http://localhost:5050 and log in.
2. Right-click **Servers** → **Register → Server**.
3. On the **General** tab, set Name to anything (e.g. `txn_db`).
4. On the **Connection** tab, fill in:
   - Host: `db`
   - Port: `5432`
   - Database: `transactions_db`
   - Username: `postgres`
   - Password: `postgres`
5. Click **Save**.

You can now browse the `jobs`, `transactions`, and `job_summaries` tables directly.

---

## Optional: Enable Gemini LLM

The pipeline works without any API key — a deterministic fallback handles classification and summaries. To enable Gemini:

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Set these values in `.env`:
   ```env
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=your_gemini_key_here
   GEMINI_MODEL=gemini-1.5-flash
   ```

3. Update `docker-compose.yml` to load `.env` instead of `.env.example`:
   ```yaml
   env_file:
     - .env
   ```

When Gemini is enabled, the worker will call the API with retry + exponential backoff (up to 3 attempts). If all retries fail, the fallback is used and the job still completes normally.

---

## API Reference

### Upload a CSV

```bash
curl -X POST "http://localhost:8000/jobs/upload" \
  -F "file=@sample_transactions.csv"
```

Response:
```json
{
  "job_id": 1,
  "status": "pending",
  "message": "CSV uploaded successfully. Processing started."
}
```

---

### Check Job Status

```bash
curl http://localhost:8000/jobs/1/status
```

While processing:
```json
{
  "job_id": 1,
  "status": "processing",
  "filename": "sample_transactions.csv"
}
```

When done:
```json
{
  "job_id": 1,
  "status": "completed",
  "filename": "sample_transactions.csv",
  "row_count_raw": 90,
  "row_count_clean": 82,
  "summary": {
    "row_count_raw": 90,
    "row_count_clean": 82,
    "anomaly_count": 5,
    "risk_level": "high"
  }
}
```

Possible `status` values: `pending` → `processing` → `completed` / `failed`

---

### Get Full Results

```bash
curl http://localhost:8000/jobs/1/results
```

Returns:
- `cleaned_transactions` — all cleaned rows
- `flagged_anomalies` — subset where `is_anomaly = true`
- `category_breakdown` — per-category total spend
- `summary` — LLM or fallback narrative, risk level, top merchants

Returns `409` if the job is not yet completed.

---

### List All Jobs

```bash
# All jobs
curl http://localhost:8000/jobs

# Filter by status
curl "http://localhost:8000/jobs?status=completed"
```

---

### Health Check

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Processing Pipeline — Details

### 1. Data Cleaning

The worker normalizes the raw CSV before any analysis:

- Strips whitespace from all text fields
- Parses dates to ISO 8601 (`YYYY-MM-DD`), supports `DD-MM-YYYY`, `YYYY/MM/DD`, `YYYY-MM-DD`
- Strips `$` and commas from amounts, converts to `float`
- Uppercases `currency` and `status`
- Fills blank `category` with `Uncategorised` and sets `category_missing = True`
- Removes exact duplicate rows

### 2. Anomaly Detection

Three rules flag a transaction as anomalous:

1. **Statistical outlier** — amount exceeds 3× the median for that `account_id`
2. **Currency mismatch** — currency is `USD` but merchant is a domestic-only service (Swiggy, Ola, IRCTC)
3. **Suspicious notes** — notes field contains keywords like `suspicious`, `fraud`, `duplicate`, `chargeback`

A row can be flagged by multiple rules; all reasons are stored concatenated.

### 3. LLM Category Classification

Rows with `category_missing = True` are sent to the LLM in a single batched API call.

Valid categories: `Food`, `Shopping`, `Travel`, `Transport`, `Utilities`, `Cash Withdrawal`, `Entertainment`, `Other`

Fallback (when LLM is off or fails): deterministic merchant-keyword matching (e.g. Swiggy → Food, Amazon → Shopping, IRCTC → Travel).

### 4. LLM Summary Generation

After stats are computed, the LLM generates:
- Total spend by currency
- Top 3 merchants
- Anomaly count
- 2–3 sentence narrative
- Risk level: `low`, `medium`, or `high`

Fallback risk level rules (no LLM):
- 0–1 anomalies → `low`
- 2–4 anomalies → `medium`
- 5+ anomalies → `high`

---

## Database Schema

### `jobs`

Tracks each uploaded CSV and its processing state.

| Column | Type | Notes |
|---|---|---|
| `id` | int | Primary key |
| `filename` | text | Original filename |
| `file_path` | text | Path on disk |
| `status` | text | pending / processing / completed / failed |
| `row_count_raw` | int | Total rows in uploaded CSV |
| `row_count_clean` | int | Rows after deduplication and cleaning |
| `created_at` | timestamp | Upload time |
| `completed_at` | timestamp | Processing finish time |
| `error_message` | text | Set only on failure |

### `transactions`

One row per cleaned transaction.

| Column | Type | Notes |
|---|---|---|
| `job_id` | int | Foreign key to jobs |
| `txn_id` | text | Original transaction ID |
| `date` | date | Parsed to ISO 8601 |
| `merchant` | text | |
| `amount` | float | Cleaned numeric value |
| `currency` | text | Uppercased |
| `status` | text | Uppercased |
| `category` | text | Final category |
| `is_anomaly` | bool | Flagged by anomaly detector |
| `anomaly_reason` | text | All triggered anomaly reasons |
| `llm_category` | text | LLM or fallback assigned category |
| `llm_failed` | bool | True if fallback was used for this row |

### `job_summaries`

One row per completed job.

| Column | Type | Notes |
|---|---|---|
| `job_id` | int | Foreign key to jobs |
| `total_spend_inr` | float | |
| `total_spend_usd` | float | |
| `top_merchants` | JSON | Top 3 by total spend |
| `category_breakdown` | JSON | Per-category totals |
| `anomaly_count` | int | |
| `narrative` | text | LLM or fallback narrative |
| `risk_level` | text | low / medium / high |
| `raw_llm_response` | JSON | Raw LLM output for debugging |

---

## Scaling Discussion

If traffic grows 100×, the first bottlenecks would be:

- Disk I/O on CSV uploads (local filesystem doesn't scale)
- Large CSV memory usage in Pandas (entire file loaded at once)
- Celery worker throughput (single container)
- Redis queue depth under heavy load
- LLM API rate limits and latency
- PostgreSQL connection pool saturation

Production improvements:

- Store uploaded CSVs in S3 / object storage instead of local disk
- Stream large CSVs in chunks instead of loading entirely into memory
- Horizontally scale Celery workers (`docker compose up --scale worker=N`)
- Use separate Celery queues for cleaning vs. LLM calls
- Add PgBouncer for PostgreSQL connection pooling
- Add database indexes on `jobs.status`, `transactions.job_id`, `transactions.account_id`
- Add observability: structured logs, metrics, distributed tracing
- Add a dead-letter queue for permanently failed tasks
- Cache LLM merchant-category decisions (same merchant always gets same category)
- Replace Alembic-less `init_db()` boot migration with proper Alembic migrations

---

## CSV Format

The uploaded CSV must contain these columns:

```
txn_id, date, merchant, amount, currency, status, category, account_id, notes
```

Extra columns are ignored. Missing required columns return a `400` error immediately.

---

## Running Tests

```bash
docker compose run --rm api pytest tests/
```

---

## Production Gaps (Known)

This is an MVP. For production, also add:

- Alembic migrations (current `init_db()` is boot-time DDL, not safe for schema changes)
- Authentication and authorization
- Object storage for CSV files (S3 or GCS)
- Rate limiting on the upload endpoint
- Structured logging and metrics
- More test coverage (integration + property-based tests)
- Separate LLM provider abstraction layer
