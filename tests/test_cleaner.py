import pandas as pd

from app.services.cleaner import clean_dataframe
from app.services.anomaly import add_anomaly_flags


def test_cleaner_normalizes_values():
    df = pd.DataFrame([
        {
            "txn_id": "TXN1",
            "date": "2024/02/05",
            "merchant": "Swiggy",
            "amount": "$100.50",
            "currency": "inr",
            "status": "success",
            "category": "",
            "account_id": "ACC1",
            "notes": "",
        }
    ])

    cleaned = clean_dataframe(df)

    assert cleaned.iloc[0]["date"] == "2024-02-05"
    assert cleaned.iloc[0]["amount"] == 100.50
    assert cleaned.iloc[0]["currency"] == "INR"
    assert cleaned.iloc[0]["status"] == "SUCCESS"
    assert cleaned.iloc[0]["category"] == "Uncategorised"
    assert bool(cleaned.iloc[0]["category_missing"]) is True


def test_anomaly_amount_rule():
    df = pd.DataFrame([
        {"account_id": "ACC1", "amount": 100, "merchant": "Amazon", "currency": "INR"},
        {"account_id": "ACC1", "amount": 120, "merchant": "Amazon", "currency": "INR"},
        {"account_id": "ACC1", "amount": 1000, "merchant": "Amazon", "currency": "INR"},
    ])

    result = add_anomaly_flags(df)

    assert bool(result.iloc[2]["is_anomaly"]) is True
