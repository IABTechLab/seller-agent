# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Durable fallback writer for audit-class events.

When the event bus fails while emitting an audit-class event (see
AUDIT_EVENT_TYPES in models.py), the event is appended to a local JSONL
file so the audit trail survives bus outages. Each write is flushed and
fsynced so the record is on disk before the calling transaction proceeds.

This is an interim safety net, not an event store: a later refactor of
the event architecture replaces it with durable bus semantics.
"""

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_FALLBACK_PATH = "data/audit_fallback.jsonl"


def get_audit_fallback_path() -> Path:
    """Resolve the fallback JSONL path from settings, with a sane default."""
    try:
        from ..config import get_settings

        path = getattr(get_settings(), "audit_fallback_path", DEFAULT_AUDIT_FALLBACK_PATH)
    except Exception:  # settings failure must not block the fallback write
        path = DEFAULT_AUDIT_FALLBACK_PATH
    return Path(path)


def write_audit_fallback(record: dict[str, Any]) -> None:
    """Append one event record to the fallback JSONL file, fsync-on-write.

    Raises on failure -- the caller (emit_event) treats a fallback-write
    failure as fatal for audit-class events (fail-closed).
    """
    path = get_audit_fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
