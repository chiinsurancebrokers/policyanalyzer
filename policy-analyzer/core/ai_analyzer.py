"""
Structured policy extraction using Claude. Replaces the original project's
3-field extraction (Coverage/Deductible/Exclusions) with a fuller schema
covering the categories a broker actually compares across providers.
"""
from __future__ import annotations

import json
import logging

import anthropic

from . import cache
from .config import config
from .fallback_analyzer import analyze_fallback

logger = logging.getLogger(__name__)

EXTRACTION_SCHEMA_PROMPT = """You are analyzing an international private medical insurance (IPMI) policy \
wording document, as a senior insurance broker preparing it for client comparison. Extract the following \
as a single JSON object — return ONLY the JSON, no preamble, no markdown fences.

{
  "provider_confirmed": "<insurer name as stated in the document, or null>",
  "plan_name": "<specific plan/tier name if stated, or null>",
  "coverage_summary": "<one or two sentence plain-language summary>",
  "categories": {
    "mental_health": "<what's covered, or 'Not covered' / 'Not mentioned'>",
    "outpatient": "<...>",
    "inpatient": "<...>",
    "emergency": "<...>",
    "dental": "<...>",
    "maternity": "<...>",
    "preventive": "<...>"
  },
  "deductible": "<amount/structure, or 'Not specified'>",
  "excess": "<per-claim or per-condition excess/co-payment amount and how it applies, or 'Not specified'>",
  "annual_limit": "<amount, or 'Not specified' / 'Unlimited'>",
  "area_of_cover": "<geographic scope as stated, e.g. 'Worldwide excluding USA/Canada', 'Europe only', plus any home-country restriction>",
  "underwriting": {
    "basis": "<underwriting basis as stated, e.g. 'Moratorium', 'Full medical underwriting', 'Continued medical exclusion', or 'Not specified'>",
    "pre_existing_conditions": "<how pre-existing conditions are treated under this basis>",
    "waiting_periods": "<any waiting periods before specific benefits activate, or 'None specified'>"
  },
  "critical_limitations": [
    {
      "topic": "<short label, e.g. 'Long-term ventilator/life support', 'Cancer treatment', 'Psychiatric inpatient days'>",
      "detail": "<the specific cap, day limit, sub-limit, or condition that a client could easily miss — quote the figure exactly as stated, e.g. '90 days maximum for mechanical ventilation/life support'>"
    }
  ],
  "exclusions_summary": "<the most consequential exclusions, briefly>",
  "confidence": "<'high' | 'medium' | 'low' — your confidence in this extraction>"
}

For "critical_limitations": this is the most important field for a broker — it exists specifically to surface \
sub-limits, day caps, and conditional restrictions buried in the wording that materially change what a claim \
will actually pay out (for example, a 90-day cap on mechanical ventilation/life support despite an otherwise \
unlimited inpatient benefit). List every such limitation you find, even if categories above already mention \
the benefit in general terms. If none are found, return an empty list — never invent one.

You may be given more than one document for the SAME policy, marked with "--- Document: <filename> (<role>) ---" \
separators, where <role> is one of: Quotation/Certificate, Policy Wording, or Supporting Document.
- **Quotation/Certificate**: short, applicant-specific — selected tier, premium, sums insured, deductible/excess
  actually chosen. Prefer this document's figures whenever it states them.
- **Policy Wording**: long, generic — the full terms and conditions. Use this for anything not restated in the
  Quotation/Certificate — exclusions, underwriting basis, area of cover, waiting periods, category-level benefit
  descriptions, and critical limitations are usually only here.
- **Supporting Document**: anything else provided (riders, endorsements, benefit tables, correspondence) — use
  it to fill gaps or corroborate the other two, but prefer the Quotation/Certificate and Policy Wording when they
  conflict with it.
If documents disagree on a figure, trust the Quotation/Certificate over the Policy Wording, and note the
discrepancy briefly in coverage_summary.

Base every field strictly on the document text. If something isn't in any document provided, say so — never invent figures."""


def analyze_with_ai(text: str, content_hash: str) -> dict:
    """Extract structured benefits data via Claude. Falls back to the
    rule-based analyzer if no API key is configured or the call fails."""
    if not config.has_ai:
        result = analyze_fallback(text)
        result["method"] = "rule-based fallback (no ANTHROPIC_API_KEY set)"
        return result

    cached = cache.get_cached(content_hash, config.CLAUDE_MODEL)
    if cached is not None:
        cached["method"] = cached.get("method", "claude") + " (cached)"
        return cached

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2200,
            messages=[{
                "role": "user",
                "content": f"{EXTRACTION_SCHEMA_PROMPT}\n\n--- DOCUMENT TEXT ---\n{text[:26000]}",
            }],
        )
        raw = response.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)
        result["method"] = "claude"
        cache.set_cached(content_hash, config.CLAUDE_MODEL, result)
        return result

    except json.JSONDecodeError:
        logger.warning("Claude returned non-JSON output, falling back to rule-based analysis")
        result = analyze_fallback(text)
        result["method"] = "rule-based fallback (AI response unparsable)"
        return result

    except anthropic.APIStatusError as e:
        logger.warning(f"Claude API error ({e.status_code}), falling back to rule-based analysis")
        result = analyze_fallback(text)
        result["method"] = f"rule-based fallback (Claude API error {e.status_code})"
        return result

    except Exception as e:
        logger.warning(f"Unexpected error calling Claude: {e}, falling back to rule-based analysis")
        result = analyze_fallback(text)
        result["method"] = "rule-based fallback (unexpected error)"
        return result
