from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from app.config import settings

ALLOWED_CATEGORIES = [
    "Food",
    "Shopping",
    "Travel",
    "Transport",
    "Utilities",
    "Cash Withdrawal",
    "Entertainment",
    "Other",
]


class LLMService:
    """
    Handles all LLM interactions for category classification and summary generation.

    Supports Gemini (via REST API) with a deterministic fallback classifier.

    To enable Gemini:
        LLM_PROVIDER=gemini
        GEMINI_API_KEY=your_key_here
        GEMINI_MODEL=gemini-1.5-flash   (or any supported model)

    When LLM_PROVIDER is not "gemini" or GEMINI_API_KEY is blank,
    all methods use the deterministic fallback and set llm_failed=True.
    The job still completes — fallback results are stored normally.

    Retry behaviour:
        All Gemini API calls retry up to 3 times with exponential backoff (1s, 2s).
        If all retries fail, the fallback is used and llm_failed=True is set.
    """

    def __init__(self):
        self.provider = str(settings.llm_provider or "mock").lower()
        self.gemini_api_key = settings.gemini_api_key
        self.gemini_model = settings.gemini_model or "gemini-1.5-flash"

    def classify_categories(self, rows: list[dict[str, Any]]) -> tuple[dict[int, str], str, bool]:
        """
        Assign a category to each row in the input list.

        Parameters:
            rows: list of dicts, each with keys:
                  row_id, txn_id, merchant, amount, currency, status, notes

        Returns:
            mapping    - dict of row_id (int) -> category (str)
            raw        - raw JSON string of the LLM response or fallback result
            llm_failed - True if Gemini was not used or failed

        The mapping always contains every row_id from the input.
        If Gemini returns a partial response, missing row_ids are filled by the fallback.
        """
        if not rows:
            return {}, "[]", False

        fallback = self._fallback_classification(rows)

        if self.provider == "gemini" and self.gemini_api_key:
            try:
                prompt = self._build_classification_prompt(rows)
                text = self._call_gemini_with_retry(prompt)
                parsed = _extract_json(text)
                mapping = self._parse_category_response(parsed)

                missing_row_ids = sorted(set(fallback.keys()) - set(mapping.keys()))
                for row_id in missing_row_ids:
                    mapping[row_id] = fallback[row_id]

                raw = json.dumps(
                    {
                        "gemini_response": parsed,
                        "missing_row_ids_filled_by_fallback": missing_row_ids,
                    },
                    ensure_ascii=False,
                )
                return mapping, raw, False

            except Exception as exc:
                raw = json.dumps(
                    {
                        "llm_error": str(exc),
                        "fallback_used": True,
                        "fallback_categories": fallback,
                    },
                    ensure_ascii=False,
                )
                return fallback, raw, True

        raw = json.dumps(
            {
                "message": f"LLM_PROVIDER='{self.provider}' or GEMINI_API_KEY is blank. Used deterministic fallback.",
                "fallback_categories": fallback,
            },
            ensure_ascii=False,
        )
        return fallback, raw, True

    def summarize(self, stats: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """
        Generate a structured summary from the aggregated transaction stats.

        Parameters:
            stats: output from build_stats() containing total_spend_by_currency,
                   category_breakdown, top_merchants, anomaly_count, row_count_clean

        Returns:
            summary    - dict with keys: total_spend_by_currency, top_3_merchants,
                         anomaly_count, narrative, risk_level
            raw        - raw response dict from LLM or fallback
            llm_failed - True if Gemini was not used or failed
        """
        if self.provider == "gemini" and self.gemini_api_key:
            try:
                prompt = self._build_summary_prompt(stats)
                text = self._call_gemini_with_retry(prompt)
                parsed = _extract_json(text)
                summary = self._normalize_summary(parsed, stats)
                return summary, {"raw_text": text}, False
            except Exception as exc:
                summary = self._fallback_summary(stats)
                return summary, {"llm_error": str(exc), "fallback_used": True}, True

        summary = self._fallback_summary(stats)
        return summary, {
            "message": f"LLM_PROVIDER='{self.provider}' or GEMINI_API_KEY is blank. Used deterministic fallback.",
            "fallback_used": True,
        }, True

    def _call_gemini_with_retry(self, prompt: str) -> str:
        """
        POST a prompt to the Gemini generateContent endpoint.

        Retries up to 3 times with exponential backoff (1s then 2s between attempts).
        Raises RuntimeError if all attempts fail.
        """
        model = str(self.gemini_model or "gemini-1.5-flash").strip()
        if model.startswith("models/"):
            model = model.split("/", 1)[1]

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.gemini_api_key}"
        )

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }

        last_error: Exception | None = None

        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=30)

                if not response.ok:
                    raise RuntimeError(
                        f"Gemini HTTP {response.status_code}: {response.text[:500]}"
                    )

                data = response.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]

            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)

        raise RuntimeError(f"LLM call failed after 3 attempts: {last_error}")

    def _build_classification_prompt(self, rows: list[dict[str, Any]]) -> str:
        return f"""
You are classifying financial transactions.

For each transaction, assign exactly one category from this list:
{ALLOWED_CATEGORIES}

Rules:
- Return one item for every input row_id. Do not skip any row_id.
- Return only valid JSON array. No markdown.
- Each item must have exactly: row_id, category.

Transactions:
{json.dumps(rows, indent=2)}
""".strip()

    def _build_summary_prompt(self, stats: dict[str, Any]) -> str:
        return f"""
You are a financial transaction risk summarizer.

Generate a valid JSON object with exactly these keys:
- total_spend_by_currency
- top_3_merchants
- anomaly_count
- narrative
- risk_level

Rules:
- narrative must be 2-3 sentences.
- risk_level must be one of: low, medium, high.
- Return only valid JSON. No markdown.

Input stats:
{json.dumps(stats, indent=2)}
""".strip()

    def _parse_category_response(self, parsed: Any) -> dict[int, str]:
        """
        Convert the raw Gemini JSON array into a row_id -> category mapping.

        Any category not in ALLOWED_CATEGORIES is replaced with "Other".
        """
        if not isinstance(parsed, list):
            raise ValueError("LLM classification response must be a JSON array")

        mapping: dict[int, str] = {}

        for item in parsed:
            if not isinstance(item, dict) or "row_id" not in item:
                continue

            row_id = int(item["row_id"])
            category = str(item.get("category") or "").strip()

            if category not in ALLOWED_CATEGORIES:
                category = "Other"

            mapping[row_id] = category

        return mapping

    def _normalize_summary(self, parsed: Any, stats: dict[str, Any]) -> dict[str, Any]:
        """
        Validate and normalize the LLM summary response.

        Falls back to stats values for any missing keys.
        risk_level is forced to "low" if the LLM returns an unexpected value.
        """
        if not isinstance(parsed, dict):
            raise ValueError("LLM summary response must be a JSON object")

        risk_level = str(parsed.get("risk_level", "low")).lower()
        if risk_level not in {"low", "medium", "high"}:
            risk_level = "low"

        return {
            "total_spend_by_currency": parsed.get(
                "total_spend_by_currency", stats.get("total_spend_by_currency", {})
            ),
            "top_3_merchants": parsed.get("top_3_merchants", stats.get("top_merchants", [])),
            "anomaly_count": int(parsed.get("anomaly_count", stats.get("anomaly_count", 0))),
            "narrative": str(parsed.get("narrative", "")),
            "risk_level": risk_level,
        }

    def _fallback_classification(self, rows: list[dict[str, Any]]) -> dict[int, str]:
        """
        Deterministic category assignment based on merchant name keywords.

        Used when Gemini is disabled or fails.
        Matches are case-insensitive substring checks on the merchant name.
        Defaults to "Other" if no keyword matches.
        """
        mapping: dict[int, str] = {}

        for row in rows:
            row_id = int(row["row_id"])
            merchant = str(row.get("merchant") or "").lower()

            if any(w in merchant for w in ["swiggy", "zomato"]):
                category = "Food"
            elif any(w in merchant for w in ["amazon", "flipkart"]):
                category = "Shopping"
            elif any(w in merchant for w in ["irctc", "makemytrip"]):
                category = "Travel"
            elif "ola" in merchant:
                category = "Transport"
            elif "jio" in merchant:
                category = "Utilities"
            elif "hdfc atm" in merchant or "atm" in merchant:
                category = "Cash Withdrawal"
            elif "bookmyshow" in merchant:
                category = "Entertainment"
            else:
                category = "Other"

            mapping[row_id] = category

        return mapping

    def _fallback_summary(self, stats: dict[str, Any]) -> dict[str, Any]:
        """
        Generate a summary without the LLM using deterministic rules.

        risk_level is based on anomaly_count:
            0-1  -> low
            2-4  -> medium
            5+   -> high
        """
        anomaly_count = int(stats.get("anomaly_count", 0))

        if anomaly_count >= 5:
            risk_level = "high"
        elif anomaly_count >= 2:
            risk_level = "medium"
        else:
            risk_level = "low"

        top_merchants = stats.get("top_merchants", [])
        top_names = [
            m.get("merchant") if isinstance(m, dict) else str(m)
            for m in top_merchants[:3]
        ]

        category_breakdown = stats.get("category_breakdown", {})
        largest_category = None
        if category_breakdown:
            largest_category = max(category_breakdown.items(), key=lambda item: item[1])[0]

        narrative = (
            f"The transactions are mainly concentrated around "
            f"{largest_category or 'multiple categories'} with top merchants including "
            f"{', '.join(top_names) if top_names else 'N/A'}. "
            f"The system detected {anomaly_count} anomalous transaction(s), "
            f"so the overall risk level is {risk_level}."
        )

        return {
            "total_spend_by_currency": stats.get("total_spend_by_currency", {}),
            "top_3_merchants": top_merchants[:3],
            "anomaly_count": anomaly_count,
            "narrative": narrative,
            "risk_level": risk_level,
        }


def _extract_json(text: str) -> Any:
    """
    Extract a JSON value from an LLM response string.

    Handles the common case where the model wraps JSON in markdown code blocks.
    Falls back to scanning for the first [ or { if direct parsing fails.

    Raises ValueError if no valid JSON can be found.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```json", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^```", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    array_start = cleaned.find("[")
    array_end = cleaned.rfind("]")
    object_start = cleaned.find("{")
    object_end = cleaned.rfind("}")

    if array_start != -1 and array_end != -1 and array_start < array_end:
        return json.loads(cleaned[array_start: array_end + 1])

    if object_start != -1 and object_end != -1 and object_start < object_end:
        return json.loads(cleaned[object_start: object_end + 1])

    raise ValueError("Could not parse JSON from LLM response")
