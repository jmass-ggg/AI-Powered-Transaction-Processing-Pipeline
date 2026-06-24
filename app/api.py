import os
import shutil
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models import Job, JobSummary, Transaction
from app.schemas import JobListItem, JobStatusResponse, JobUploadResponse
from app.worker import process_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/upload", response_model=JobUploadResponse, status_code=201)
def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Accept a CSV file upload.

    Steps:
    1. Validates the file has a .csv extension.
    2. Saves the file to UPLOAD_DIR with a UUID prefix to avoid name collisions.
    3. Creates a Job record in the database with status "pending".
    4. Enqueues the processing task to Celery via Redis.
    5. Returns the job_id immediately — the client should poll /jobs/{job_id}/status.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed")

    os.makedirs(settings.upload_dir, exist_ok=True)
    safe_filename = os.path.basename(file.filename)
    stored_filename = f"{uuid.uuid4().hex}_{safe_filename}"
    file_path = os.path.join(settings.upload_dir, stored_filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save uploaded file: {exc}") from exc

    job = Job(filename=safe_filename, file_path=file_path, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)

    process_job.delay(job.id)

    return JobUploadResponse(
        job_id=job.id,
        status=job.status,
        message="CSV uploaded successfully. Processing started.",
    )


@router.get("/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(job_id: int, db: Session = Depends(get_db)):
    """
    Return the current processing status of a job.

    Possible status values: pending, processing, completed, failed.

    When status is "completed", the response includes a summary field with:
    - row_count_raw and row_count_clean
    - anomaly_count
    - risk_level (low / medium / high)

    For the full transaction list, use GET /jobs/{job_id}/results.
    """
    job = db.query(Job).options(joinedload(Job.summary)).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    summary = None
    if job.status == "completed" and job.summary:
        summary = {
            "row_count_raw": job.row_count_raw,
            "row_count_clean": job.row_count_clean,
            "anomaly_count": job.summary.anomaly_count,
            "risk_level": job.summary.risk_level,
        }

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        filename=job.filename,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        error_message=job.error_message,
        summary=summary,
    )


@router.get("/{job_id}/results")
def get_job_results(job_id: int, db: Session = Depends(get_db)):
    """
    Return the full processing results for a completed job.

    Returns 409 if the job is not yet completed.

    Response shape:
    {
        "job": { job metadata },
        "cleaned_transactions": [ list of all transactions ],
        "flagged_anomalies": [ subset of transactions where is_anomaly=true ],
        "category_breakdown": { category: total_amount },
        "summary": {
            "total_spend_inr", "total_spend_usd",
            "top_merchants", "anomaly_count",
            "narrative", "risk_level", "raw_llm_response"
        }
    }
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not completed yet. Current status: {job.status}",
        )

    transactions = (
        db.query(Transaction)
        .filter(Transaction.job_id == job_id)
        .order_by(Transaction.id.asc())
        .all()
    )
    summary = db.query(JobSummary).filter(JobSummary.job_id == job_id).first()
    anomalies = [txn for txn in transactions if txn.is_anomaly]

    response = {
        "job": {
            "job_id": job.id,
            "filename": job.filename,
            "status": job.status,
            "row_count_raw": job.row_count_raw,
            "row_count_clean": job.row_count_clean,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
        },
        "cleaned_transactions": transactions,
        "flagged_anomalies": anomalies,
        "category_breakdown": summary.category_breakdown if summary else {},
        "summary": {
            "total_spend_inr": summary.total_spend_inr if summary else 0,
            "total_spend_usd": summary.total_spend_usd if summary else 0,
            "top_merchants": summary.top_merchants if summary else [],
            "anomaly_count": summary.anomaly_count if summary else 0,
            "narrative": summary.narrative if summary else None,
            "risk_level": summary.risk_level if summary else "low",
            "raw_llm_response": summary.raw_llm_response if summary else None,
        },
    }
    return jsonable_encoder(response)


@router.get("", response_model=list[JobListItem])
def list_jobs(
    status: Optional[str] = Query(
        default=None,
        description="Filter by job status: pending, processing, completed, failed",
    ),
    db: Session = Depends(get_db),
):
    """
    List all jobs ordered by creation time (newest first).

    Optional query parameter:
        ?status=completed   — filter to jobs with a specific status
    """
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status.lower())
    return query.order_by(Job.created_at.desc()).all()
