"""
Second-opinion verification, via GPT-4o-mini, in two flavours:

1. verify_with_gpt — reviews Claude's per-document EXTRACTION against the
   source text (unchanged from the original design).
2. verify_recommendation_with_gpt — reviews HAL's PLAN RECOMMENDATION: given
   the same applicant needs and the same structured extractions HAL saw,
   GPT-4o-mini independently judges whether HAL's pick and reasoning hold
   up, and can propose an alternative if it disagrees.

Same pattern used in Asklepios/KiraAIpet in both cases: the second model
isn't extracting or recommending from scratch in isolation — it's shown
the original material AND the first model's output, and asked to check it.
This catches errors more reliably than two blind attempts that might just
disagree without either being clearly "right".

Both are user-triggered (a button), not automatic — each is an extra paid
call, so both stay opt-in.
"""
from __future__ import annotations

import hashlib
import json
import logging

from openai import OpenAI

from . import cache
from .config import config

logger = logging.getLogger(__name__)

VERIFY_PROMPT_TEMPLATE = """You are reviewing a structured extraction of an insurance policy document, \
done by another AI. Check it against the source text and return ONLY a JSON object, no preamble, no markdown fences:

{{
  "agreement": "<'agree' | 'partial' | 'disagree'>",
  "discrepancies": ["<specific field/value that looks wrong or unsupported by the document, if any>"],
  "missing": ["<anything material the extraction missed, if any>"],
  "notes": "<one or two sentence overall verdict>"
}}

--- ORIGINAL EXTRACTION (to verify) ---
{extraction_json}

--- SOURCE DOCUMENT TEXT ---
{document_text}
"""


def verify_with_gpt(document_text: str, claude_result: dict, content_hash: str) -> dict:
    """Returns a verification dict, or a dict with an 'error' key on failure.
    Never raises — callers should check for 'error'."""
    if not config.has_gpt_verification:
        return {"error": "No OPENAI_API_KEY configured — second-opinion verification unavailable."}

    cache_key = f"{config.GPT_MODEL}-verify"
    cached = cache.get_cached(content_hash, cache_key)
    if cached is not None:
        return cached

    # Strip our own bookkeeping fields before showing the extraction to GPT
    extraction_for_review = {k: v for k, v in claude_result.items() if k not in ("method",)}

    prompt = VERIFY_PROMPT_TEMPLATE.format(
        extraction_json=json.dumps(extraction_for_review, indent=2),
        document_text=document_text[:26000],
    )

    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=config.GPT_MODEL,
            messages=[
                {"role": "system", "content": "You are a meticulous insurance policy reviewer. Be specific and only flag things you can point to in the source text."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)
        cache.set_cached(content_hash, cache_key, result)
        return result

    except json.JSONDecodeError:
        logger.warning("GPT verification returned non-JSON output")
        return {"error": "GPT-4o-mini returned an unparsable response."}

    except Exception as e:
        logger.warning(f"GPT verification call failed: {e}")
        return {"error": f"Verification call failed: {e}"}


RECOMMENDATION_VERIFY_PROMPT_TEMPLATE = """You are a second, independent insurance advisor reviewing a plan \
recommendation made by another AI (HAL) for a client. You're shown the same applicant needs and the same \
structured policy data HAL saw, plus HAL's recommendation. Judge it on its merits — don't just defer to it.

Return ONLY a JSON object, no preamble, no markdown fences:

{{
  "agreement": "<'agree' | 'partial' | 'disagree'>",
  "alternative_provider": "<if you would recommend a different provider than HAL, name it here, else null>",
  "reasoning": "<your own assessment of the best fit for this client, referencing the same structured data>",
  "concerns": ["<anything HAL's recommendation overlooked, understated, or got wrong, if any>"],
  "notes": "<one or two sentence overall verdict on HAL's recommendation>"
}}

--- APPLICANT NEEDS ---
{applicant_profile}

--- CANDIDATE POLICIES (structured extractions) ---
{policies_json}

--- HAL'S RECOMMENDATION (to review) ---
{hal_recommendation}
"""


def verify_recommendation_with_gpt(applicant_profile: dict, records: list[dict], hal_recommendation: dict) -> dict:
    """Independent GPT-4o-mini review of HAL's plan recommendation.
    Never raises — callers should check for 'error'."""
    if not config.has_gpt_verification:
        return {"error": "No OPENAI_API_KEY configured — second-opinion verification unavailable."}

    content_hashes = sorted(r["content_hash"] for r in records)
    payload = json.dumps(applicant_profile, sort_keys=True) + "|" + "|".join(content_hashes)
    combo_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    cache_key = f"{config.GPT_MODEL}-verify-recommendation"
    cached = cache.get_cached(combo_hash, cache_key)
    if cached is not None:
        return cached

    policies_for_prompt = [
        {"provider": r["provider"], "extraction": r["analysis"]} for r in records
    ]
    hal_for_review = {k: v for k, v in hal_recommendation.items() if k != "method"}

    prompt = RECOMMENDATION_VERIFY_PROMPT_TEMPLATE.format(
        applicant_profile=json.dumps(applicant_profile, indent=2),
        policies_json=json.dumps(policies_for_prompt, indent=2)[:16000],
        hal_recommendation=json.dumps(hal_for_review, indent=2),
    )

    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=config.GPT_MODEL,
            messages=[
                {"role": "system", "content": "You are a meticulous, independent insurance advisor. Be specific, reference figures from the data, and don't simply agree with the other AI by default."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)
        cache.set_cached(combo_hash, cache_key, result)
        return result

    except json.JSONDecodeError:
        logger.warning("GPT recommendation review returned non-JSON output")
        return {"error": "GPT-4o-mini returned an unparsable response."}

    except Exception as e:
        logger.warning(f"GPT recommendation review call failed: {e}")
        return {"error": f"Second opinion call failed: {e}"}
