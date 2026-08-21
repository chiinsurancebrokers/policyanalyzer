"""
Turns a list of per-provider extraction results into a comparison table
and export formats (Excel, Markdown).
"""
from __future__ import annotations

import io

import pandas as pd

CATEGORY_ORDER = [
    "mental_health", "outpatient", "inpatient", "emergency",
    "dental", "maternity", "preventive",
]

CATEGORY_LABELS = {
    "mental_health": "Mental Health",
    "outpatient": "Outpatient",
    "inpatient": "Inpatient",
    "emergency": "Emergency",
    "dental": "Dental",
    "maternity": "Maternity",
    "preventive": "Preventive",
}


def build_dataframe(results: list[tuple[str, dict]]) -> pd.DataFrame:
    """results: list of (provider_name, extraction_dict)"""
    rows = []
    for provider, data in results:
        row = {
            "Provider": provider,
            "Deductible": data.get("deductible", "N/A"),
            "Annual Limit": data.get("annual_limit", "N/A"),
        }
        categories = data.get("categories", {})
        for key in CATEGORY_ORDER:
            row[CATEGORY_LABELS[key]] = categories.get(key, "N/A")
        row["Exclusions"] = data.get("exclusions_summary", "N/A")
        row["Confidence"] = data.get("confidence", "N/A")
        row["Method"] = data.get("method", "N/A")
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("Provider")
    return df


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Comparison")
    return buffer.getvalue()


def to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "No data to display"
    return df.to_markdown()
