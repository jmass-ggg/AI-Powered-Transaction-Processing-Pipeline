from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from celery import Celery

from app.config import settings
from app.database import SessionLocal, init_db
from app.models import Job, JobSummary, Transaction
from app.services.anomaly import add_anomaly_flags
from app.services.cleaner import clean_dataframe
from app.services.llm import LLMService
from app.services.summary import build_stats

celery_app = Celery(
    "transaction_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="process_job")
def process_job(job_id: int) -> dict:
    """
    Main background processing task. Runs inside the Celery worker.

    Pipeline steps (in order):
        1. Read raw CSV from disk
        2. Clean the DataFrame (dates, amounts, currency, status, duplicates)
        3. Detect anomalies (statistical outliers + currency rule)
        4. Classify missing categories via LLM (batched single API call)
        5. Build aggregated stats
        6. Generate LLM narrative summary
        7. Persist transactions and summary to PostgreSQL
        8. Mark job as completed

    LLM failures do not fail the job — they fall back to deterministic classification.
    Any other unhandled exception marks the job as failed and stores the error message.
    """
    init_db()
    db = SessionLocal()

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return {"status": "failed", "error": "Job not found"}

        job.status = "processing"
        job.error_message = None
        db.commit()

        raw_df = pd.read_csv(job.file_path, dtype=str, keep_default_na=False)
        job.row_count_raw = int(len(raw_df))
        db.commit()

        cleaned_df = clean_dataframe(raw_df)
        cleaned_df = add_anomaly_flags(cleaned_df)

        llm_service = LLMService()
        cleaned_df = _classify_missing_categories(cleaned_df, llm_service)

        stats = build_stats(cleaned_df)
        summary_data, raw_summary_response, _summary_llm_failed = llm_service.summarize(stats)

        _save_transactions(db, job.id, cleaned_df)
        _save_summary(db, job.id, stats, summary_data, raw_summary_response)

        job.status = "completed"
        job.row_count_clean = int(len(cleaned_df))
        job.completed_at = datetime.now(timezone.utc)
        db.commit()

        return {"status": "completed", "job_id": job.id}

    except Exception as exc:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
        return {"status": "failed", "job_id": job_id, "error": str(exc)}

    finally:
        db.close()


def _normalize_category(value) -> str | None:
    """
    Return a usable category string, or None if the value is empty or Uncategorised.
    Used to decide whether a row needs LLM classification.
    """
    category = str(value or "").strip()
    if not category or category.lower() == "uncategorised":
        return None
    return category


def _classify_missing_categories(cleaned_df: pd.DataFrame, llm_service: LLMService) -> pd.DataFrame:
    """
    Send rows with missing categories to the LLM in a single batched call.

    Only rows where category_missing=True are included in the LLM batch.
    Rows that already have a category are skipped entirely.

    If the LLM returns partial results, missing row_ids are filled using
    the deterministic fallback inside LLMService.

    Adds three columns to the DataFrame:
        llm_category     - the category assigned by LLM or fallback
        llm_raw_response - raw JSON string from the LLM batch call
        llm_failed       - True if fallback was used for this specific row
    """
    df = cleaned_df.copy()

    df["llm_category"] = df["category"].apply(lambda v: _normalize_category(v) or "Other")
    df["llm_raw_response"] = None
    df["llm_failed"] = False

    if "category_missing" in df.columns:
        missing_mask = df["category_missing"] == True  # noqa: E712
    else:
        missing_mask = df["category"].apply(lambda v: _normalize_category(v) is None)

    missing_df = df[missing_mask]

    if missing_df.empty:
        return df

    rows = [
        {
            "row_id": int(idx),
            "txn_id": row.get("txn_id"),
            "merchant": row.get("merchant"),
            "amount": row.get("amount"),
            "currency": row.get("currency"),
            "status": row.get("status"),
            "notes": row.get("notes"),
        }
        for idx, row in missing_df.iterrows()
    ]

    mapping, raw_response, llm_failed = llm_service.classify_categories(rows)

    for idx, row in missing_df.iterrows():
        row_id = int(idx)
        category = mapping.get(row_id)
        category = _normalize_category(category) or _normalize_category(row.get("category")) or "Other"

        df.at[idx, "llm_category"] = category
        df.at[idx, "category"] = category
        df.at[idx, "llm_raw_response"] = raw_response
        df.at[idx, "llm_failed"] = llm_failed and row_id not in mapping

    return df


def _save_transactions(db, job_id: int, df: pd.DataFrame) -> None:
    """
    Replace all transactions for this job with the cleaned rows.

    Deletes existing rows before inserting to make re-runs safe.
    Uses bulk_save_objects for performance over row-by-row inserts.
    """
    db.query(Transaction).filter(Transaction.job_id == job_id).delete(synchronize_session=False)

    transactions: list[Transaction] = []

    for _, row in df.iterrows():
        parsed_date = None
        if row.get("date"):
            parsed_date = datetime.fromisoformat(str(row.get("date"))).date()

        category = str(row.get("category") or "Uncategorised")
        llm_category = str(row.get("llm_category") or category or "Other")

        transactions.append(
            Transaction(
                job_id=job_id,
                raw_index=int(row.get("raw_index") or 0),
                txn_id=row.get("txn_id"),
                date=parsed_date,
                merchant=str(row.get("merchant") or "Unknown Merchant"),
                amount=float(row.get("amount") or 0.0),
                currency=str(row.get("currency") or "UNKNOWN"),
                status=str(row.get("status") or "UNKNOWN"),
                category=category,
                account_id=row.get("account_id"),
                notes=row.get("notes"),
                is_anomaly=bool(row.get("is_anomaly")),
                anomaly_reason=row.get("anomaly_reason"),
                llm_category=llm_category,
                llm_raw_response=row.get("llm_raw_response"),
                llm_failed=bool(row.get("llm_failed")),
            )
        )

    db.bulk_save_objects(transactions)
    db.commit()


def _save_summary(db, job_id: int, stats: dict, summary_data: dict, raw_response: dict) -> None:
    """
    Replace the summary for this job.

    summary_data is from the LLM or fallback summarizer.
    stats is the deterministic aggregation from build_stats() and
    is used as a fallback for any fields the LLM did not return.
    """
    db.query(JobSummary).filter(JobSummary.job_id == job_id).delete(synchronize_session=False)

    total_spend = summary_data.get("total_spend_by_currency") or stats.get("total_spend_by_currency", {})

    summary = JobSummary(
        job_id=job_id,
        total_spend_inr=float(total_spend.get("INR", 0.0) or 0.0),
        total_spend_usd=float(total_spend.get("USD", 0.0) or 0.0),
        top_merchants=summary_data.get("top_3_merchants") or stats.get("top_merchants", []),
        category_breakdown=stats.get("category_breakdown", {}),
        anomaly_count=int(summary_data.get("anomaly_count", stats.get("anomaly_count", 0))),
        narrative=summary_data.get("narrative"),
        risk_level=summary_data.get("risk_level", "low"),
        raw_llm_response=raw_response,
    )

    db.add(summary)
    db.commit()
