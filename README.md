# AI-Powered Transaction Processing Pipeline

Backend + DevOps internship assignment implementation.

This project accepts a dirty financial transaction CSV, processes it asynchronously using a Redis/Celery worker, cleans the data, detects anomalies, classifies missing transaction categories using an LLM/fallback classifier, generates a structured summary, and exposes results through polling APIs.

## Tech Stack

- FastAPI
- PostgreSQL
- SQLAlchemy
- Redis
- Celery
- Pandas
- Gemini API support with fallback classifier
- Docker + Docker Compose

## Architecture

```text
Client / curl / Postman
        |
        v
FastAPI API
        |
        |-- creates Job row in PostgreSQL
        |-- stores uploaded CSV file
        |-- enqueues Celery task
        |
        v
Redis Queue
        |
        v
Celery Worker
        |
        |-- reads CSV
        |-- cleans transactions
        |-- detects anomalies
        |-- classifies missing categories using LLM/fallback
        |-- generates JSON summary using LLM/fallback
        |-- saves transactions + summary in PostgreSQL
        |
        v
PostgreSQL
```

## Run Locally

```bash
docker compose up --build
```

API will run at:

```text
http://localhost:8000
```

Swagger docs:

```text
http://localhost:8000/docs
```

Health check:

```bash
curl http://localhost:8000/health
```

## Optional Gemini Setup

The app works without an API key using deterministic fallback logic. To use Gemini:

1. Copy `.env.example` to `.env`
2. Set:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key
```

Then update `docker-compose.yml` to use `.env` instead of `.env.example`, or replace values in `.env.example` directly for quick testing.

## API Endpoints

### 1. Upload CSV

```bash
curl -X POST "http://localhost:8000/jobs/upload" \
  -F "file=@sample_transactions.csv"
```

Example response:

```json
{
  "job_id": 1,
  "status": "pending",
  "message": "CSV uploaded successfully. Processing started."
}
```

### 2. Check Job Status

```bash
curl http://localhost:8000/jobs/1/status
```

Example processing response:

```json
{
  "job_id": 1,
  "status": "processing",
  "filename": "sample_transactions.csv"
}
```

Example completed response:

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

### 3. Get Full Results

```bash
curl http://localhost:8000/jobs/1/results
```

Returns:

- cleaned transaction list
- flagged anomalies
- per-category spend breakdown
- LLM/fallback summary

### 4. List Jobs

```bash
curl http://localhost:8000/jobs
```

Filter by status:

```bash
curl "http://localhost:8000/jobs?status=completed"
```

## Processing Pipeline

### Data Cleaning

The worker normalizes:

- dates to ISO format
- `$` amounts to numeric values
- currency to uppercase
- status to uppercase
- blank categories to `Uncategorised`
- duplicate rows are removed

### Anomaly Detection

A transaction is flagged when:

1. Amount is greater than 3x the account median.
2. Currency is USD and merchant is one of: Swiggy, Ola, IRCTC.

### LLM Category Classification

For transactions with missing category, the worker asks the LLM to classify into:

```text
Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other
```

If the LLM is unavailable, the app uses a deterministic merchant-based fallback so the job still completes.

### LLM Summary

The worker builds numerical stats first, then asks the LLM to generate:

- total spend by currency
- top 3 merchants
- anomaly count
- 2-3 sentence narrative
- risk level: low, medium, high

If the LLM fails after retries, the fallback summary is saved and the job still completes.

## Database Design

### jobs

Stores each uploaded CSV processing job.

Important columns:

```text
id, filename, file_path, status, row_count_raw, row_count_clean, created_at, completed_at, error_message
```

### transactions

Stores cleaned transaction rows.

Important columns:

```text
job_id, txn_id, date, merchant, amount, currency, status, category, account_id, notes,
is_anomaly, anomaly_reason, llm_category, llm_raw_response, llm_failed
```

### job_summaries

Stores final report per job.

Important columns:

```text
job_id, total_spend_inr, total_spend_usd, top_merchants, category_breakdown,
anomaly_count, narrative, risk_level, raw_llm_response
```

## Scaling Discussion

If traffic grows 100x, the first bottlenecks would be:

- API upload disk I/O
- large CSV memory usage in Pandas
- Celery worker throughput
- Redis queue depth
- LLM API rate limits and latency
- PostgreSQL connection pool saturation

Production improvements:

- Store uploaded CSVs in S3/object storage instead of local disk
- Stream large CSVs instead of loading entire files into memory
- Horizontally scale Celery workers
- Use separate queues for cleaning and LLM calls
- Add PgBouncer for PostgreSQL connection pooling
- Add indexes on `jobs.status`, `transactions.job_id`, and `transactions.account_id`
- Add observability: structured logs, metrics, tracing
- Add a dead-letter queue for permanently failed tasks
- Cache LLM merchant-category decisions

Trade-off: these changes improve scale and reliability but add infrastructure cost and operational complexity.

## One-Day Submission Notes

This is intentionally built as a complete MVP. For a production system, add:

- Alembic migrations
- Auth
- More tests
- Object storage
- Rate limiting
- Better monitoring
- Separate LLM provider abstraction
