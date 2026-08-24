"""
HAL recommendation engine.

Takes the applicant's stated needs plus the structured extraction already
produced for each policy (core/ai_analyzer.py) and asks Claude to recommend
the best-fit plan, ranked, with plain-language reasoning a client can
actually read — not just "policy A wins" but *why*, and which critical
limitations (from ai_analyzer's critical_limitations field) matter for this
specific applicant's stated needs.

Also takes the broker's chat conversation (core/chat.py) so far, if any.
Extraction is automated and can miss or misread a detail (e.g. an excess
figure buried in wording the model didn't weight correctly) — when the
broker catches and states that correction in chat, the final recommendation
needs to reflect it, not just answer the one chat question in isolation.
Broker-stated facts in chat are treated as more trustworthy than the
extraction where they conflict, since they come from a person who has
actually reviewed the source document.

This never re-reads the source documents — it reasons only over the
structured extractions already produced (plus the chat transcript), so it
can't contradict the extraction on its own and stays cheap regardless of
document length.
"""
from __future__ import annotations

import hashlib
import json
import logging

import anthropic

from . import cache
from .config import config
from .claude_utils import get_response_text

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

Base the recommendation on the applicant's stated needs and the structured data provided below. Where the \
broker's chat clarifications (below, if any) state a fact that conflicts with the structured extraction — \
for example, correcting an excess/deductible figure the extraction got wrong or left as "not specified" — \
trust the broker's clarification and use it instead, since it comes from a person who reviewed the actual \
source document. Reflect any such corrections directly in your reasoning (e.g. "Bupa has a nil excess, which \
the broker confirmed") rather than repeating the old, superseded extraction value. Never invent figures beyond \
what's in the extraction or the broker's own clarifications, and never recommend a plan whose extraction \
confidence is 'low' without saying so in flagged_caveats. If two plans are close, say so honestly rather than \
forcing a confident pick.

Keep your response efficient: reasoning should be one focused paragraph (not several), key_tradeoffs and \
flagged_caveats should each list at most 6 items prioritized by importance to this applicant, and each item \
should be one concise sentence. Finish the JSON cleanly rather than running long.

--- APPLICANT NEEDS ---
{applicant_profile}

--- CANDIDATE POLICIES (structured extractions) ---
{policies_json}

--- BROKER CLARIFICATIONS FROM CHAT (if any — trust these over the extraction above where they conflict) ---
{chat_transcript}
"""


def _format_chat_transcript(conversation_history: list[dict] | None, max_chars: int = 8000) -> str:
    if not conversation_history:
        return "(no chat clarifications yet)"
    lines = []
    for msg in conversation_history:
        speaker = "Broker" if msg.get("role") == "user" else "HAL"
        lines.append(f"{speaker}: {msg.get('content', '')}")
    transcript = "\n".join(lines)
    # Keep the most recent context if the conversation has grown long
    return transcript[-max_chars:]


def _combined_hash(applicant_profile: dict, content_hashes: list[str], chat_transcript: str = "") -> str:
    payload = (
        json.dumps(applicant_profile, sort_keys=True)
        + "|" + "|".join(sorted(content_hashes))
        + "|" + hashlib.sha256(chat_transcript.encode("utf-8")).hexdigest()
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def recommend_plan(applicant_profile: dict, records: list[dict], conversation_history: list[dict] | None = None) -> dict:
    """records: list of dicts with at least 'provider', 'analysis' (extraction dict),
    and 'content_hash' keys, as produced in app.py's analysis_records.

    conversation_history: the broker's chat with HAL so far (core/chat.py format),
    if any — factored into the recommendation so a clarification made in chat
    (e.g. correcting a figure the extraction got wrong) actually changes the
    final recommendation, not just the one chat answer.

    Returns a dict with the recommendation, or a dict with an 'error' key on failure.
    Never raises — callers should check for 'error'."""
    if not config.has_ai:
        return {"error": "No ANTHROPIC_API_KEY configured — HAL recommendation unavailable."}

    if not records:
        return {"error": "No analyzed policies to recommend from."}

    chat_transcript = _format_chat_transcript(conversation_history)
    content_hashes = [r["content_hash"] for r in records]
    combo_hash = _combined_hash(applicant_profile, content_hashes, chat_transcript)
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
        policies_json=json.dumps(policies_for_prompt, indent=2)[:32000],
        chat_transcript=chat_transcript,
    )

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.HAL_MODEL,
            max_tokens=config.THINKING_BUDGET_TOKENS + 6144,
            thinking={"type": "enabled", "budget_tokens": config.THINKING_BUDGET_TOKENS},
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            raw = get_response_text(response)
        except ValueError:
            logger.warning(
                f"HAL recommendation had no text block (stop_reason={response.stop_reason}) — "
                f"likely ran out of tokens before producing output."
            )
            reason = "response cut off before any output (max_tokens hit)" if response.stop_reason == "max_tokens" else "no text in response"
            return {"error": f"HAL recommendation failed: {reason} — try again."}

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
