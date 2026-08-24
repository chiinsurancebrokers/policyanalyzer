"""
Small shared helper for parsing Anthropic Messages API responses.

Some models (and any model when extended thinking is involved) can return
content as multiple blocks — e.g. a ThinkingBlock followed by a TextBlock —
rather than a single text block at content[0]. Assuming content[0] is
always the text response breaks on those models. This scans for the actual
text block instead.
"""
from __future__ import annotations


def get_response_text(response) -> str:
    """Returns the stripped text from the first text-type content block.
    Raises ValueError if no text block is present (e.g. a tool-only response)."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text.strip()
    raise ValueError("No text content block found in Claude response")
