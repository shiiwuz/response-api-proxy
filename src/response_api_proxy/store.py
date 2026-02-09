from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import normalize_json, stable_json_dumps, utcnow_iso_z


@dataclass(frozen=True)
class StorePaths:
    base_dir: Path
    day_dir: Path
    req_dir: Path
    request_id: str


class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root)

    def new_request_dir(self) -> StorePaths:
        # A request_id meant to be stable and sortable by time.
        # Example: 20260209T180000Z_ab12cd34
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        rid = f"{ts}_{secrets.token_hex(4)}"
        day = time.strftime("%Y-%m-%d", time.gmtime())

        day_dir = self.root / day
        req_dir = day_dir / rid
        req_dir.mkdir(parents=True, exist_ok=False)
        return StorePaths(base_dir=self.root, day_dir=day_dir, req_dir=req_dir, request_id=rid)

    def write_json(self, path: Path, obj: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(stable_json_dumps(obj) + "\n", encoding="utf-8")
        tmp.replace(path)

    def write_text(self, path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def save_request(self, sp: StorePaths, headers: dict[str, str], body_obj: Any) -> None:
        self.write_json(sp.req_dir / "request.headers.json", headers)
        self.write_json(sp.req_dir / "request.body.json", body_obj)
        self.write_json(sp.req_dir / "request.body.normalized.json", normalize_json(body_obj))

    def save_response_meta(self, sp: StorePaths, meta: dict[str, Any]) -> None:
        self.write_json(sp.req_dir / "response.meta.json", meta)

    def save_response_body_json(self, sp: StorePaths, body_obj: Any) -> None:
        self.write_json(sp.req_dir / "response.body.json", body_obj)

    def save_response_sse(self, sp: StorePaths, sse_text: str) -> None:
        self.write_text(sp.req_dir / "response.sse.txt", sse_text)

    def save_meta(self, sp: StorePaths, meta: dict[str, Any]) -> None:
        # Extra meta about this capture.
        meta = dict(meta)
        meta.setdefault("captured_at", utcnow_iso_z())
        self.write_json(sp.req_dir / "capture.meta.json", meta)


def getenv_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}
