from __future__ import annotations

import pandas as pd

# Merchants that only operate in India.
# A USD transaction from any of these is flagged as a currency anomaly.
DOMESTIC_ONLY_MERCHANTS = {"swiggy", "ola", "irctc"}

# Keywords in the notes field that indicate a suspicious transaction.
SUSPICIOUS_NOTE_KEYWORDS = {"suspicious", "duplicate", "fraud", "chargeback"}


def add_anomaly_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add is_anomaly and anomaly_reason columns to the DataFrame.

    Three anomaly rules are applied per row:

    1. Statistical outlier:
       Amount exceeds 3x the median amount for the same account_id.
       Median is computed per account across all rows in the job.

    2. Currency mismatch:
       Currency is USD but the merchant is in DOMESTIC_ONLY_MERCHANTS.

    3. Suspicious notes:
       The notes field contains a keyword from SUSPICIOUS_NOTE_KEYWORDS
       (case-insensitive).

    A row can be flagged by multiple rules simultaneously.
    All triggered reasons are concatenated with "; " in anomaly_reason.
    """
    df = df.copy()

    medians = df.groupby("account_id")["amount"].median().to_dict()

    anomaly_flags: list[bool] = []
    anomaly_reasons: list[str | None] = []

    for _, row in df.iterrows():
        reasons: list[str] = []

        account_id = row.get("account_id")
        amount = float(row.get("amount") or 0.0)
        account_median = float(medians.get(account_id, 0.0) or 0.0)

        if account_median > 0 and amount > (3 * account_median):
            reasons.append(
                f"Amount {amount:.2f} exceeds 3x account median {account_median:.2f}"
            )

        merchant = str(row.get("merchant") or "").strip().lower()
        currency = str(row.get("currency") or "").strip().upper()

        if currency == "USD" and merchant in DOMESTIC_ONLY_MERCHANTS:
            reasons.append(
                f"Domestic-only merchant {row.get('merchant')} has USD currency"
            )

        notes = str(row.get("notes") or "").strip().lower()
        if any(keyword in notes for keyword in SUSPICIOUS_NOTE_KEYWORDS):
            reasons.append(f"Suspicious note detected: {row.get('notes')}")

        anomaly_flags.append(bool(reasons))
        anomaly_reasons.append("; ".join(reasons) if reasons else None)

    df["is_anomaly"] = anomaly_flags
    df["anomaly_reason"] = anomaly_reasons

    return df
