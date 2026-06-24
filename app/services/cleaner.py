from __future__ import annotations

from datetime import datetime

import pandas as pd

REQUIRED_COLUMNS = [
    "txn_id",
    "date",
    "merchant",
    "amount",
    "currency",
    "status",
    "category",
    "account_id",
    "notes",
]


def _strip_text(value) -> str:
    """Return a stripped string, or empty string for None/NaN values."""
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()


def _parse_date(value: str) -> str | None:
    """
    Parse a date string into ISO 8601 format (YYYY-MM-DD).

    Supports:
        DD-MM-YYYY   e.g. 05-02-2024
        YYYY/MM/DD   e.g. 2024/02/05
        YYYY-MM-DD   e.g. 2024-02-05

    Returns None if the value is empty or cannot be parsed.
    """
    value = _strip_text(value)
    if not value:
        return None

    formats = ["%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None

    return parsed.date().isoformat()


def _parse_amount(value) -> float:
    """
    Parse an amount string to float.

    Strips leading dollar signs and commas before converting.
    Returns 0.0 if the value is empty or cannot be converted.
    """
    text = _strip_text(value).replace("$", "").replace(",", "")
    if not text:
        return 0.0

    try:
        return float(text)
    except ValueError:
        return 0.0


def validate_columns(df: pd.DataFrame) -> None:
    """
    Raise ValueError if any required column is missing from the DataFrame.

    Called before cleaning to fail fast on invalid CSV uploads.
    """
    normalized_columns = {col.strip(): col for col in df.columns}
    missing = [col for col in REQUIRED_COLUMNS if col not in normalized_columns]

    if missing:
        raise ValueError(f"Invalid CSV. Missing required column(s): {', '.join(missing)}")


def clean_dataframe(raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean a raw transaction DataFrame.

    Transformations applied:
        - Strip whitespace from all column names and text values
        - Remove exact duplicate rows (checked before type conversion)
        - Preserve original row index as raw_index before deduplication
        - Parse dates to ISO 8601 (YYYY-MM-DD)
        - Strip $ and commas from amounts and convert to float
        - Uppercase currency and status values
        - Fill blank category with "Uncategorised" and set category_missing=True
        - Replace blank txn_id, account_id, notes with None

    Does NOT drop rows for missing optional fields (txn_id, category, notes).
    Only exact duplicate rows are removed.

    Returns a cleaned DataFrame with all REQUIRED_COLUMNS plus:
        raw_index        - original CSV row position before deduplication
        category_missing - True if the original category was blank
    """
    raw_df = raw_df.copy()
    raw_df.columns = [col.strip() for col in raw_df.columns]

    validate_columns(raw_df)

    df = raw_df[REQUIRED_COLUMNS].copy()
    df.insert(0, "raw_index", range(len(df)))

    for col in REQUIRED_COLUMNS:
        df[col] = df[col].apply(_strip_text)

    df = df.drop_duplicates(subset=REQUIRED_COLUMNS, keep="first").reset_index(drop=True)

    df["txn_id"] = df["txn_id"].apply(lambda x: x or None)
    df["date"] = df["date"].apply(_parse_date)
    df["merchant"] = df["merchant"].apply(lambda x: x or "Unknown Merchant")
    df["amount"] = df["amount"].apply(_parse_amount)
    df["currency"] = df["currency"].str.upper().replace("", "UNKNOWN")
    df["status"] = df["status"].str.upper().replace("", "UNKNOWN")

    df["category_missing"] = df["category"].apply(lambda x: not bool(_strip_text(x)))
    df["category"] = df["category"].apply(lambda x: _strip_text(x) or "Uncategorised")

    df["account_id"] = df["account_id"].apply(lambda x: x or None)
    df["notes"] = df["notes"].apply(lambda x: x or None)

    return df
