from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Job(Base):
    """
    Represents one CSV upload and its processing lifecycle.

    Status transitions:
        pending -> processing -> completed
                             -> failed

    row_count_raw:  total rows read from the uploaded CSV before cleaning
    row_count_clean: rows remaining after deduplication and cleaning
    file_path:      absolute path to the uploaded CSV on disk (inside the container)
    error_message:  populated only when status is "failed"
    """

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    status = Column(String(30), nullable=False, default="pending", index=True)
    row_count_raw = Column(Integer, nullable=True)
    row_count_clean = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    transactions = relationship("Transaction", back_populates="job", cascade="all, delete-orphan")
    summary = relationship("JobSummary", back_populates="job", uselist=False, cascade="all, delete-orphan")


class Transaction(Base):
    """
    One cleaned transaction row belonging to a Job.

    category:       final category used (original if present, LLM-assigned if originally missing)
    llm_category:   the category assigned by the LLM or fallback classifier
    llm_raw_response: raw JSON string returned by the LLM for this row's batch call
    llm_failed:     True if the LLM call failed for this row's batch and fallback was used
    is_anomaly:     True if any anomaly rule was triggered
    anomaly_reason: human-readable explanation of why the row was flagged
    raw_index:      original row position in the uploaded CSV (before deduplication)
    """

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    raw_index = Column(Integer, nullable=True)
    txn_id = Column(String(100), nullable=True, index=True)
    date = Column(Date, nullable=True)
    merchant = Column(String(255), nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String(10), nullable=False)
    status = Column(String(30), nullable=False)
    category = Column(String(100), nullable=False, default="Uncategorised")
    account_id = Column(String(100), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    is_anomaly = Column(Boolean, nullable=False, default=False)
    anomaly_reason = Column(Text, nullable=True)
    llm_category = Column(String(100), nullable=True)
    llm_raw_response = Column(Text, nullable=True)
    llm_failed = Column(Boolean, nullable=False, default=False)

    job = relationship("Job", back_populates="transactions")


class JobSummary(Base):
    """
    Aggregated summary for a completed Job.

    One row per job (unique constraint on job_id).

    top_merchants:      JSON list of top 3 merchants by total spend
    category_breakdown: JSON dict of category -> total amount spent
    narrative:          2-3 sentence LLM-generated or fallback summary of spending patterns
    risk_level:         one of: low, medium, high
    raw_llm_response:   full raw response from the LLM summary call
    """

    __tablename__ = "job_summaries"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    total_spend_inr = Column(Float, nullable=False, default=0.0)
    total_spend_usd = Column(Float, nullable=False, default=0.0)
    top_merchants = Column(JSON, nullable=False, default=list)
    category_breakdown = Column(JSON, nullable=False, default=dict)
    anomaly_count = Column(Integer, nullable=False, default=0)
    narrative = Column(Text, nullable=True)
    risk_level = Column(String(20), nullable=False, default="low")
    raw_llm_response = Column(JSON, nullable=True)

    job = relationship("Job", back_populates="summary")
