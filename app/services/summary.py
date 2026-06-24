from __future__ import annotations

import pandas as pd


def build_stats(df: pd.DataFrame) -> dict:
    """
    Compute aggregated statistics from the cleaned transaction DataFrame.

    Returns a dict with:
        total_spend_by_currency  - dict of currency -> total amount (e.g. {"INR": 12345.67})
        category_breakdown       - dict of category -> total amount, sorted descending
        top_merchants            - list of top 3 merchants by total spend, each with:
                                      merchant, total_amount, transaction_count
        anomaly_count            - total number of flagged anomaly rows
        row_count_clean          - total number of rows in the cleaned DataFrame

    This is computed deterministically before the LLM summary call.
    The LLM summary uses these stats as input and may enrich or reformat them.
    If the LLM fails, these stats are used directly.
    """
    total_spend_by_currency = (
        df.groupby("currency")["amount"].sum().round(2).to_dict()
        if not df.empty
        else {}
    )

    category_breakdown = (
        df.groupby("category")["amount"]
        .sum()
        .round(2)
        .sort_values(ascending=False)
        .to_dict()
        if not df.empty
        else {}
    )

    merchant_spend = (
        df.groupby("merchant")
        .agg(
            total_amount=("amount", "sum"),
            transaction_count=("merchant", "count"),
        )
        .reset_index()
        .sort_values("total_amount", ascending=False)
        .head(3)
    )

    top_merchants = [
        {
            "merchant": row["merchant"],
            "total_amount": round(float(row["total_amount"]), 2),
            "transaction_count": int(row["transaction_count"]),
        }
        for _, row in merchant_spend.iterrows()
    ]

    return {
        "total_spend_by_currency": {k: round(float(v), 2) for k, v in total_spend_by_currency.items()},
        "category_breakdown": {k: round(float(v), 2) for k, v in category_breakdown.items()},
        "top_merchants": top_merchants,
        "anomaly_count": int(df["is_anomaly"].sum()) if "is_anomaly" in df.columns else 0,
        "row_count_clean": int(len(df)),
    }
