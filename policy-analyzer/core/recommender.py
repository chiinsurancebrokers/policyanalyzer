"""
HAL recommendation engine.

Takes the applicant's stated needs plus the structured extraction already
produced for each policy (core/ai_analyzer.py) and asks Claude to recommend
the best-fit plan, ranked, with plain-language reasoning a client can
actually read — not just "policy A wins" but *why*, and which critical
limitations (from ai_analyzer's critical_limitations field) matter for this
specific applicant's stated needs.

This never re-reads the source documents — it reasons only over the
structured extractions already produced, so it can't contradict them and
stays cheap regardless of document length.
"""
from __future__ import annotations

import hashlib
import json
import logging

import anthropic

from . import cache
from .config import config

logger = logging.getLogger(__name__)

RECOMMEND_PROMPT_TEMPLATE = """You are HAL, an AI assistant working for a licensed IPMI insurance broker. \
A client has described their needs, and you have structured extractions of {n_policies} candidate policies. \
Recommend the best-fit plan for THIS client and explain why, in language a non-expert client can follow.

Return ONLY a JSON object, no preamble, no markdown fences:

{{
  "recommended_provider": "<the Provider name of your top pick, matching one of the providers given>",
  "ranking": [
    {{"provider": "<name>", "fit_summary": "<one sentence on how well this fits the client, or why it doesn't>"}}
  ],
  "reasoning": "<a clear, client-facing paragraph explaining why the recommended plan is the best fit for THIS client's stated needs and budget — reference specific figures from the extractions>",
  "key_tradeoffs": ["<short bullet on a meaningful tradeoff of the recommended plan vs runner-up(s)>"],
  "flagged_caveats": ["<any critical_limitations from the extractions that are directly relevant to what this client said they need — e.g. if they mentioned a condition needing long-term ventilator care and a plan caps that at 90 days, flag it explicitly, even if that plan is otherwise a good fit>"],
  "confidence": "<'high' | 'medium' | 'low'>"
}}

Base the recommendation strictly on the applicant's stated needs and the structured data provided below — \
never invent figures, and never recommend a plan whose extraction confidence is 'low' without saying so \
in flagged_caveats. If two plans are close, say so honestly rather than forcing a confident pick.

--- APPLICANT NEEDS ---
{applicant_profile}

--- CANDIDATE POLICIES (structured extractions) ---
{policies_json}
"""


def _combined_hash(applicant_profile: dict, content_hashes: list[str]) -> str:
    payload = json.dumps(applicant_profile, sort_keys=True) + "|" + "|".join(sorted(content_hashes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def recommend_plan(applicant_profile: dict, records: list[dict]) -> dict:
    """records: list of dicts with at least 'provider', 'analysis' (extraction dict),
    and 'content_hash' keys, as produced in app.py's analysis_records.

    Returns a dict with the recommendation, or a dict with an 'error' key on failure.
    Never raises — callers should check for 'error'."""
    if not config.has_ai:
        return {"error": "No ANTHROPIC_API_KEY configured — HAL recommendation unavailable."}

    if not records:
        return {"error": "No analyzed policies to recommend from."}

    content_hashes = [r["content_hash"] for r in records]
    combo_hash = _combined_hash(applicant_profile, content_hashes)
    cache_key = f"{config.HAL_MODEL}-recommend"
    cached = cache.get_cached(combo_hash, cache_key)
    if cached is not None:
        return cached

    policies_for_prompt = [
        {"provider": r["provider"], "extraction": r["analysis"]} for r in records
    ]

    prompt = RECOMMEND_PROMPT_TEMPLATE.format(
        n_policies=len(records),
        applicant_profile=json.dumps(applicant_profile, indent=2),
        policies_json=json.dumps(policies_for_prompt, indent=2)[:18000],
    )

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.HAL_MODEL,
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)
        result["method"] = f"HAL ({config.HAL_MODEL})"
        cache.set_cached(combo_hash, cache_key, result)
        return result

    except json.JSONDecodeError:
        logger.warning("HAL recommendation returned non-JSON output")
        return {"error": "HAL returned an unparsable response — try again."}

    except anthropic.APIStatusError as e:
        logger.warning(f"HAL recommendation API error ({e.status_code})")
        return {"error": f"Claude API error ({e.status_code}) while generating recommendation."}

    except Exception as e:
        logger.warning(f"Unexpected error generating HAL recommendation: {e}")
        return {"error": f"Recommendation failed: {e}"}
