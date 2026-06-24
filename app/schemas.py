from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class JobUploadResponse(BaseModel):
    """Returned immediately after a CSV is uploaded and the job is enqueued."""

    job_id: int
    status: str
    message: str


class JobListItem(BaseModel):
    """Single item in the GET /jobs list response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    status: str
    row_count_raw: Optional[int] = None
    row_count_clean: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class JobStatusResponse(BaseModel):
    """
    Returned by GET /jobs/{job_id}/status.

    summary is only populated when status is "completed".
    It contains high-level stats: row counts, anomaly count, and risk level.
    For the full transaction list and breakdown, use GET /jobs/{job_id}/results.
    """

    job_id: int
    status: str
    filename: str
    row_count_raw: Optional[int] = None
    row_count_clean: Optional[int] = None
    error_message: Optional[str] = None
    summary: Optional[dict[str, Any]] = None


class TransactionOut(BaseModel):
    """
    Serialized transaction row for API responses.

    llm_category: category assigned by LLM or fallback (may differ from category
                  if the original CSV had a category and LLM was not called)
    llm_failed:   True if the LLM call for this row's batch failed and fallback was used
    """

    id: int
    txn_id: Optional[str]
    date: Optional[date]
    merchant: str
    amount: float
    currency: str
    status: str
    category: str
    account_id: Optional[str]
    notes: Optional[str]
    is_anomaly: bool
    anomaly_reason: Optional[str]
    llm_category: Optional[str]
    llm_failed: bool

    model_config = ConfigDict(from_attributes=True)
