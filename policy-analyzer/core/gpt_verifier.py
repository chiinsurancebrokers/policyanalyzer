"""
Second-opinion verification of Claude's extraction, via GPT-4o-mini.

Same pattern used in Asklepios/KiraAIpet: the second model doesn't extract
independently from scratch — it's shown the source document AND Claude's
extraction, and asked to check it, flag anything wrong or missing, and
say whether it agrees. This catches extraction errors more reliably than
two independent extractions that might just disagree without either being
clearly "right".

This is user-triggered (a button per result), not automatic on every
upload — it's an extra paid call, so it should be opt-in per document,
same as the GPT-4o second-opinion button in Asklepios.
"""
from __future__ import annotations

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
        document_text=document_text[:15000],
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
