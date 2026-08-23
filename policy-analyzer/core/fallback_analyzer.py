"""
Keyword/regex fallback analyzer. Used only when Claude is unavailable
(no API key, or the call fails) so the app still returns something useful
instead of erroring out.
"""
from __future__ import annotations

import re

CATEGORY_KEYWORDS = {
    "mental_health": ["mental health", "psychiatr", "counsel", "therapy"],
    "outpatient": ["outpatient", "gp visit", "specialist consult"],
    "inpatient": ["inpatient", "hospital stay", "hospitalization", "surgery"],
    "emergency": ["emergency", "ambulance", "evacuation"],
    "dental": ["dental", "orthodont"],
    "maternity": ["maternity", "pregnancy", "childbirth"],
    "preventive": ["preventive", "screening", "vaccination", "check-up", "checkup"],
}


def analyze_fallback(text: str) -> dict:
    """Very rough keyword-presence scoring. This is NOT a substitute for the
    AI extraction — it exists so the app degrades gracefully rather than
    failing outright."""
    lower = text.lower()

    categories = {}
    for key, keywords in CATEGORY_KEYWORDS.items():
        found = any(kw in lower for kw in keywords)
        categories[key] = "Mentioned" if found else "Not found"

    deductible_match = re.search(r"deductible[^.\n]{0,60}", lower)
    excess_match = re.search(r"excess[^.\n]{0,60}", lower)
    limit_match = re.search(r"(annual|overall|maximum)\s+limit[^.\n]{0,60}", lower)
    area_match = re.search(r"(worldwide|europe|area of cover)[^.\n]{0,60}", lower)
    underwriting_match = re.search(r"(moratorium|full medical underwriting|continued medical exclusion)[^.\n]{0,60}", lower)
    exclusions_present = "exclusion" in lower

    return {
        "provider_confirmed": None,
        "plan_name": None,
        "coverage_summary": "Rule-based scan only — enable Claude for a full extraction",
        "categories": categories,
        "deductible": deductible_match.group(0).strip() if deductible_match else "Not found",
        "excess": excess_match.group(0).strip() if excess_match else "Not found",
        "annual_limit": limit_match.group(0).strip() if limit_match else "Not found",
        "area_of_cover": area_match.group(0).strip() if area_match else "Not found",
        "underwriting": {
            "basis": underwriting_match.group(0).strip() if underwriting_match else "Not specified",
            "pre_existing_conditions": "Not extracted — rule-based scan cannot assess this",
            "waiting_periods": "Not extracted — rule-based scan cannot assess this",
        },
        "critical_limitations": [],
        "exclusions_summary": "Exclusions section present" if exclusions_present else "No exclusions section detected",
        "confidence": "low",
        "method": "rule-based fallback",
    }
