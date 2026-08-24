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
from .claude_utils import get_response_text
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
  "confidence": "<'high' | 'medium' | 'low' — your confidence in this extraction>",
  "tier_column_check": [
    {"field": "annual_limit", "value_used": "<the value you wrote above>", "tier_column_source": "<the exact tier/column header this value came from, e.g. 'Platinum'>"},
    {"field": "categories.outpatient", "value_used": "<...>", "tier_column_source": "<...>"},
    {"field": "categories.inpatient", "value_used": "<...>", "tier_column_source": "<...>"},
    {"field": "categories.dental", "value_used": "<...>", "tier_column_source": "<...>"},
    {"field": "categories.mental_health", "value_used": "<...>", "tier_column_source": "<...>"},
    {"field": "categories.maternity", "value_used": "<...>", "tier_column_source": "<...>"}
  ]
}

The "tier_column_check" array is a forced self-audit, only meaningful when a multi-tier comparison table is \
actually present in the document — if there's no such table (e.g. a single-tier policy), return it as an empty \
list. When a comparison table IS present, fill in all six rows: for each field, state the value you used above \
AND the specific tier/column header you read it from. This is not optional busywork — actually re-locate each \
cell in the table as you fill this in, one field at a time. If, while filling this in, you notice a value you \
wrote above came from the wrong column, fix the original field before finalizing your answer — the check must \
reflect the truth, not just restate whatever you already wrote.

For "critical_limitations": this is the most important field for a broker — it exists specifically to surface \
sub-limits, day caps, and conditional restrictions buried in the wording that materially change what a claim \
will actually pay out (for example, a 90-day cap on mechanical ventilation/life support despite an otherwise \
unlimited inpatient benefit). List every such limitation you find, even if categories above already mention \
the benefit in general terms. If none are found, return an empty list — never invent one.

CRITICAL — search the entire document, not just the opening sections: general exclusions tables and clause-level \
limitations are frequently found deep into a long policy wording document (e.g. 30+ pages in), not near the top. \
Do not stop scanning after the summary/benefits table sections. If a "General Exclusions" or "What is not covered" \
section exists anywhere in the provided text, you must read it in full and reflect its contents in both \
exclusions_summary and critical_limitations — a document that clearly contains such a section is never grounds \
to say limitations were "not detailed in the provided text".

CRITICAL — tier/column matching: quote documents often include a multi-tier comparison table (e.g. Silver / Gold \
/ Platinum) showing figures for several tiers side by side, alongside a separate personalized quote or breakdown \
stating which SINGLE tier this applicant actually selected. Identify the applicant's selected tier first (from \
the personalized quote/certificate, not the comparison table), then extract every figure — annual limits, \
sub-limits, deductibles, category benefits — only from that tier's column. Before writing each figure down, \
re-check which column header it came from. Never attribute one tier's figures to a different tier's plan_name \
— if the selected tier's own value is "Paid in full" or "Unlimited", extract exactly that, not a neighboring \
column's number.

A known failure mode to actively guard against: getting ONE field right (e.g. the inpatient/annual overall limit) \
while getting a DIFFERENT field wrong (e.g. the outpatient limit) by pulling it from a neighboring tier's column \
in the same table — in the same response. Getting one number right does not mean the others are right. Treat \
annual_limit and every numeric value inside "categories" as independent lookups: for each one, separately \
re-locate its specific cell in the selected tier's column before writing it down, even if you already found \
the correct column for an earlier field in the same table. If the applicant's own tier's cell reads "Paid in \
full" or "Unlimited" for a benefit that a neighboring tier expresses as a specific number, you must write "Paid \
in full" / "Unlimited" — writing the neighboring tier's number instead is exactly the error this check exists \
to catch.

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

Base every field strictly on the document text. If something isn't in any document provided, say so — never invent figures.

Keep your response itself efficient regardless of document length: list at most the 10 most consequential \
critical_limitations (prioritize by financial/clinical impact to the applicant, not by order of appearance), \
keep each "detail" to one concise sentence, and keep coverage_summary and exclusions_summary each to 1-3 \
sentences. The goal is a complete, well-prioritized extraction that fits comfortably within the response — \
never truncate mid-field to fit; instead be more concise throughout so the JSON always ends cleanly."""


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
            max_tokens=config.THINKING_BUDGET_TOKENS + 6144,
            thinking={"type": "enabled", "budget_tokens": config.THINKING_BUDGET_TOKENS},
            messages=[{
                "role": "user",
                "content": f"{EXTRACTION_SCHEMA_PROMPT}\n\n--- DOCUMENT TEXT ---\n{text[:config.MAX_EXTRACTION_CHARS]}",
            }],
        )
        raw = get_response_text(response)
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            snippet = raw[-300:] if raw else "(empty response)"
            logger.warning(
                f"Claude extraction returned unparsable JSON (stop_reason={response.stop_reason}): "
                f"{e}. Response tail: {snippet!r}"
            )
            result = analyze_fallback(text)
            reason = "response cut off (max_tokens hit)" if response.stop_reason == "max_tokens" else str(e)
            result["method"] = f"rule-based fallback (AI response unparsable — {reason})"
            return result

        result["method"] = "claude"
        cache.set_cached(content_hash, config.CLAUDE_MODEL, result)
        return result

    except anthropic.APIStatusError as e:
        logger.warning(f"Claude API error ({e.status_code}), falling back to rule-based analysis")
        result = analyze_fallback(text)
        result["method"] = f"rule-based fallback (Claude API error {e.status_code}: {e.message if hasattr(e, 'message') else str(e)})"
        return result

    except Exception as e:
        logger.warning(f"Unexpected error calling Claude: {e}, falling back to rule-based analysis")
        result = analyze_fallback(text)
        result["method"] = f"rule-based fallback (unexpected error: {e})"
        return result
