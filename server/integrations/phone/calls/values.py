"""Tuning values of the call pipeline (server-wide env) and small helpers."""
from __future__ import annotations

import json
import os
from typing import Any

AUDIO_RETENTION_DAYS = int(os.getenv("CALL_AUDIO_RETENTION_DAYS", "90"))
MIN_SUGGESTION_CONFIDENCE = float(os.getenv("CALL_SUGGESTION_MIN_CONFIDENCE", "0.6"))
MAX_TRANSCRIPT_CHARS = 60000
# Calls processed at once, for the whole server: model calls are slow and the pod is small.
PROCESSING_CONCURRENCY = int(os.getenv("CALL_PROCESSING_CONCURRENCY", "2"))

FAILED_TRANSCRIPT_PREFIX = "[Transcription failed"


def loads(raw: Any, default: Any) -> Any:
    """JSON text → value (``default`` when empty or invalid; dicts / lists as they are)."""
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def text(v: Any) -> str:
    """``v`` stripped when it is a string, else ""."""
    return v.strip() if isinstance(v, str) else ""


def transcript_usable(transcript: str | None) -> bool:
    t = (transcript or "").strip()
    return len(t) >= 15 and not t.startswith(FAILED_TRANSCRIPT_PREFIX)
