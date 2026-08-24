"""
Groups multiple uploaded files into a single policy before extraction.

Brokers routinely have TWO documents for the same policy: a general
wording / member guide / policy rules book (long, generic terms, often
lists example figures for several tiers), and a personalized quote /
renewal document (short, specific to one applicant — selected tier,
premium, sums insured). Treated as separate uploads, the old flow
analyzed each file independently and produced two incomplete, inconsistent
rows for what is actually one policy. This module lets the caller group
files by a shared label and merges their text into one combined document
before extraction runs once per group.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .config import config


@dataclass
class GroupDoc:
    filename: str
    text: str
    doc_type: str = "document"  # 'quotation' | 'wording' | 'supporting' | 'document'


DOC_TYPE_LABELS = {
    "quotation": "Quotation/Certificate",
    "wording": "Policy Wording",
    "supporting": "Supporting Document",
    "document": "Document",
}

_DOC_TYPE_PRIORITY = {"quotation": 0, "wording": 1, "supporting": 2, "document": 1}


def normalize_label(label: str) -> str:
    """Case/whitespace-insensitive key used to decide which files merge."""
    return " ".join(label.strip().lower().split())


def combine_documents(docs: list[GroupDoc], total_budget: int | None = None) -> str:
    """Merges multiple documents' text into one string with filename + role
    headers, using a water-filling allocation. Quotation/certificate documents
    (short, applicant-specific figures) are prioritized first, then policy
    wording, then supporting documents — so any truncation falls on the
    longest, lowest-priority material rather than cutting off the figures
    that matter most for this specific applicant."""
    if total_budget is None:
        total_budget = config.MAX_EXTRACTION_CHARS
    if not docs:
        return ""
    if len(docs) == 1:
        text = docs[0].text[:total_budget]
        label = DOC_TYPE_LABELS.get(docs[0].doc_type, "Document")
        return f"--- Document: {docs[0].filename} ({label}) ---\n{text}"

    ordered = sorted(docs, key=lambda d: (_DOC_TYPE_PRIORITY.get(d.doc_type, 1), len(d.text)))
    remaining = total_budget
    parts = []
    for i, doc in enumerate(ordered):
        docs_left = len(ordered) - i
        fair_share = max(remaining // docs_left, 0)
        take = doc.text if len(doc.text) <= fair_share else doc.text[:fair_share]
        remaining -= len(take)
        label = DOC_TYPE_LABELS.get(doc.doc_type, "Document")
        parts.append(f"--- Document: {doc.filename} ({label}) ---\n{take}")

    return "\n\n".join(parts)


def combined_hash(docs: list[GroupDoc]) -> str:
    """Hash of the actual per-document content (not the truncated combined
    text) so caching stays correct even if total_budget changes later."""
    payload = "|".join(f"{d.filename}:{hashlib.sha256(d.text.encode('utf-8', errors='ignore')).hexdigest()}" for d in docs)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def group_files_by_label(items: list[tuple], labels: list[str]) -> dict[str, list]:
    """items: list of arbitrary per-file objects (e.g. Streamlit UploadedFile).
    labels: parallel list of user-assigned group labels.
    Returns an ordered dict-like mapping of normalized label -> list of
    (display_label, item) preserving first-seen order and first-seen casing."""
    groups: dict[str, dict] = {}
    for item, label in zip(items, labels):
        key = normalize_label(label) or normalize_label(getattr(item, "name", "policy"))
        if key not in groups:
            groups[key] = {"display_label": label.strip() or key, "items": []}
        groups[key]["items"].append(item)
    return groups
