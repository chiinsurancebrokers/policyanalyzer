"""
Text extraction from uploaded policy documents (PDF or TXT).
Kept intentionally simple and dependency-light (PyMuPDF only).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from .config import config

SUPPORTED_ENCODINGS = ("utf-8", "utf-8-sig", "latin-1", "iso-8859-1")


@dataclass
class ExtractionResult:
    ok: bool
    text: str = ""
    error: str = ""
    content_hash: str = ""
    char_count: int = 0


def extract_text(file_path: str, original_filename: str = "") -> ExtractionResult:
    """Extract text from a PDF or TXT file on disk. Returns a result object,
    never raises — callers should check `.ok`."""
    path = Path(file_path)

    if not path.exists():
        return ExtractionResult(ok=False, error="File not found")

    size = path.stat().st_size
    if size > config.max_file_size_bytes:
        return ExtractionResult(ok=False, error=f"File exceeds {config.MAX_FILE_SIZE_MB}MB limit")
    if size == 0:
        return ExtractionResult(ok=False, error="File is empty")

    suffix = (path.suffix or Path(original_filename).suffix).lower()

    try:
        if suffix == ".pdf":
            text = _extract_pdf(path)
        elif suffix == ".txt":
            text = _extract_txt(path)
        else:
            return ExtractionResult(ok=False, error=f"Unsupported file type: {suffix or 'unknown'}")
    except Exception as e:
        return ExtractionResult(ok=False, error=f"Extraction failed: {e}")

    text = text.strip()
    if len(text) < 50:
        return ExtractionResult(ok=False, error="Extracted text too short — file may be scanned/image-only")

    content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return ExtractionResult(ok=True, text=text, content_hash=content_hash, char_count=len(text))


def _extract_pdf(path: Path) -> str:
    try:
        doc = fitz.open(str(path))
        text = "".join(page.get_text() for page in doc)
        doc.close()
        return text
    except fitz.FileDataError as e:
        raise RuntimeError("Corrupted or unreadable PDF") from e


def _extract_txt(path: Path) -> str:
    for encoding in SUPPORTED_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("Could not decode text file with any supported encoding")


def infer_provider_name(text: str, filename: str = "") -> str:
    """Best-effort provider detection from filename first, then document text."""
    haystacks = [filename.lower(), text[:3000].lower()]
    for haystack in haystacks:
        for provider in config.KNOWN_PROVIDERS:
            if provider.lower() in haystack:
                return provider
    return "Unknown"
