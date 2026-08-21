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
wording document. Extract the following as a single JSON object — return ONLY the JSON, no preamble, \
no markdown fences.

{
  "provider_confirmed": "<insurer name as stated in the document, or null>",
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
  "annual_limit": "<amount, or 'Not specified' / 'Unlimited'>",
  "exclusions_summary": "<the most consequential exclusions, briefly>",
  "confidence": "<'high' | 'medium' | 'low' — your confidence in this extraction>"
}

Base every field strictly on the document text. If something isn't in the document, say so — never invent figures."""


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
            max_tokens=1200,
            messages=[{
                "role": "user",
                "content": f"{EXTRACTION_SCHEMA_PROMPT}\n\n--- DOCUMENT TEXT ---\n{text[:15000]}",
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
