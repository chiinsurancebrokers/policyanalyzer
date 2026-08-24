"""
Lightweight cache so re-uploading the same policy document doesn't
trigger a fresh (paid) AI extraction call.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import config


def _connect() -> sqlite3.Connection:
    db_path = Path(config.CACHE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extraction_cache (
            content_hash TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return conn


def get_cached(content_hash: str, model: str) -> dict | None:
    if not config.CACHE_ENABLED:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT result_json FROM extraction_cache WHERE content_hash = ? AND model = ?",
            (content_hash, model),
        ).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def set_cached(content_hash: str, model: str, result: dict) -> None:
    if not config.CACHE_ENABLED:
        return
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO extraction_cache (content_hash, model, result_json) VALUES (?, ?, ?)",
            (content_hash, model, json.dumps(result)),
        )
        conn.commit()
    finally:
        conn.close()
