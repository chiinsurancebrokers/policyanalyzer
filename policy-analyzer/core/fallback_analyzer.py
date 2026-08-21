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
    limit_match = re.search(r"(annual|overall|maximum)\s+limit[^.\n]{0,60}", lower)
    exclusions_present = "exclusion" in lower

    return {
        "provider_confirmed": None,
        "coverage_summary": "Rule-based scan only — enable Claude for a full extraction",
        "categories": categories,
        "deductible": deductible_match.group(0).strip() if deductible_match else "Not found",
        "annual_limit": limit_match.group(0).strip() if limit_match else "Not found",
        "exclusions_summary": "Exclusions section present" if exclusions_present else "No exclusions section detected",
        "confidence": "low",
        "method": "rule-based fallback",
    }
