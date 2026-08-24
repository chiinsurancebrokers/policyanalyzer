"""
Free-form follow-up chat with HAL, grounded in the same structured
extractions and applicant profile used elsewhere in the app. Lets the
broker ask clarifying questions, sanity-check a detail, or explore an
angle HAL's structured recommendation didn't cover — without a full
re-run of the recommendation itself.

This is a plain-text conversation, not a structured-JSON call like
ai_analyzer/recommender — so there's no JSON parsing step, and nothing
here is cached (each turn's context is unique).
"""
from __future__ import annotations

import json
import logging

import anthropic

from .claude_utils import get_response_text
from .config import config

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """You are HAL, an AI assistant working for a licensed IPMI insurance broker at Ashlar \
Insurance. You're in a follow-up conversation with the broker about a specific client case. You have the \
applicant's stated needs, the full structured extraction for each candidate policy, and (if generated) your \
own prior recommendation.

Answer the broker's questions grounded strictly in this data — never invent figures or policy terms that \
aren't in the extractions provided. If something isn't covered by the data you have, say so plainly and \
suggest what document or detail would resolve it. Keep answers focused and practical — this is a working \
conversation with a professional, not a client-facing document.

If the broker mentions a new fact about the applicant (e.g. a medical condition, a changed budget), incorporate \
it into your reasoning for this answer, but note that it won't be reflected in a future "Get HAL's recommendation" \
re-run unless they also update the Applicant Needs form above. If the broker has just added a new document \
(you'll see a system note about it), factor its extraction into your answer.

--- APPLICANT NEEDS ---
{applicant_profile}

--- CANDIDATE POLICIES (structured extractions) ---
{policies_json}

--- HAL'S CURRENT RECOMMENDATION (if generated) ---
{hal_recommendation}
"""


def chat_with_hal(
    conversation_history: list[dict],
    applicant_profile: dict,
    records: list[dict],
    hal_recommendation: dict | None,
) -> str:
    """conversation_history: list of {"role": "user"|"assistant", "content": str},
    in order, ending with the newest user turn.

    Returns HAL's reply text. Never raises — errors are returned as a
    user-facing string starting with '⚠' so the caller can just display it."""
    if not config.has_ai:
        return "⚠ No ANTHROPIC_API_KEY configured — chat is unavailable."

    if not conversation_history:
        return "⚠ Nothing to respond to."

    policies_for_prompt = [
        {"provider": r["provider"], "extraction": r["analysis"]} for r in records
    ]
    hal_for_prompt = None
    if hal_recommendation and "error" not in hal_recommendation:
        hal_for_prompt = {k: v for k, v in hal_recommendation.items() if k != "method"}

    system = CHAT_SYSTEM_PROMPT.format(
        applicant_profile=json.dumps(applicant_profile, indent=2),
        policies_json=json.dumps(policies_for_prompt, indent=2)[:32000],
        hal_recommendation=json.dumps(hal_for_prompt, indent=2) if hal_for_prompt else "(not yet generated)",
    )

    messages = [{"role": m["role"], "content": m["content"]} for m in conversation_history]

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.HAL_MODEL,
            max_tokens=config.THINKING_BUDGET_TOKENS + 1536,
            thinking={"type": "enabled", "budget_tokens": config.THINKING_BUDGET_TOKENS},
            system=system,
            messages=messages,
        )
        try:
            return get_response_text(response)
        except ValueError:
            logger.warning(f"HAL chat had no text block (stop_reason={response.stop_reason})")
            return "⚠ HAL's response was cut off before producing an answer — try a shorter question, or try again."

    except anthropic.APIStatusError as e:
        logger.warning(f"HAL chat API error ({e.status_code})")
        return f"⚠ Claude API error ({e.status_code}) — try again."

    except Exception as e:
        logger.warning(f"Unexpected error in HAL chat: {e}")
        return f"⚠ Something went wrong: {e}"
