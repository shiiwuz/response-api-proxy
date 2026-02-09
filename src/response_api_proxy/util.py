from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any


SENSITIVE_HEADER_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
}


def utcnow_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, indent=2)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def normalize_json(obj: Any) -> Any:
    """Normalize a JSON payload for diffing.

    Strategy:
    - Sort dict keys.
    - Remove obviously transient fields that frequently break prefix caching.

    This is conservative: we keep most fields intact.
    """

    if isinstance(obj, dict):
        drop_keys = {
            # Commonly noisy / unrelated to prompt prefix caching
            "stream",
            "metadata",
            "user",
            "request_id",
            "traceparent",
            "tracestate",
        }
        out = {}
        for k in sorted(obj.keys()):
            if k in drop_keys:
                continue
            out[k] = normalize_json(obj[k])
        return out
    if isinstance(obj, list):
        return [normalize_json(x) for x in obj]
    return obj


def redact_headers(headers: dict[str, str], log_sensitive: bool) -> dict[str, str]:
    if log_sensitive:
        return dict(headers)

    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in SENSITIVE_HEADER_KEYS:
            out[k] = "[REDACTED]"
        else:
            out[k] = v
    return out
