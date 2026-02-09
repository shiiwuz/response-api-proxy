from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class Capture:
    request_id: str
    dir: Path
    captured_at: Optional[datetime]
    request_body_path: Path
    request_norm_path: Path
    response_meta_path: Path


def _parse_dt(s: str) -> datetime:
    # Accept "YYYY-MM-DD HH:MM" or ISO.
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _try_parse_captured_at(p: Path) -> Optional[datetime]:
    if not p.exists():
        return None
    try:
        meta = _read_json(p)
        s = meta.get("captured_at")
        if not s:
            return None
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def find_captures(root: Path) -> list[Capture]:
    caps: list[Capture] = []
    if not root.exists():
        return []

    for day in sorted([p for p in root.iterdir() if p.is_dir()]):
        for req_dir in sorted([p for p in day.iterdir() if p.is_dir()]):
            rid = req_dir.name
            rb = req_dir / "request.body.json"
            rn = req_dir / "request.body.normalized.json"
            rm = req_dir / "response.meta.json"
            if not rb.exists() or not rn.exists() or not rm.exists():
                continue
            ca = _try_parse_captured_at(req_dir / "capture.meta.json") or _try_parse_captured_at(rm)
            caps.append(
                Capture(
                    request_id=rid,
                    dir=req_dir,
                    captured_at=ca,
                    request_body_path=rb,
                    request_norm_path=rn,
                    response_meta_path=rm,
                )
            )

    # If captured_at is missing, fall back to lexicographic order.
    caps.sort(key=lambda c: (c.captured_at or datetime.min, c.request_id))
    return caps


def _get_usage(meta: dict[str, Any]) -> dict[str, Any]:
    usage = meta.get("usage")
    if isinstance(usage, dict):
        return usage
    return {}


def _cached_tokens(usage: dict[str, Any]) -> int:
    details = usage.get("input_tokens_details")
    if isinstance(details, dict) and "cached_tokens" in details:
        try:
            return int(details.get("cached_tokens") or 0)
        except Exception:
            return 0
    # Fallback for older shapes.
    try:
        return int(usage.get("cached_tokens") or 0)
    except Exception:
        return 0


def _input_tokens(usage: dict[str, Any]) -> int:
    for k in ["input_tokens", "prompt_tokens"]:
        if k in usage:
            try:
                return int(usage.get(k) or 0)
            except Exception:
                return 0
    return 0


def summarize_cache(caps: list[Capture]) -> str:
    total = len(caps)
    if total == 0:
        return "No captures found."

    sum_in = 0
    sum_cached = 0
    sum_elapsed = 0
    n_elapsed = 0

    by_session: dict[str, list[Capture]] = {}

    for c in caps:
        meta = _read_json(c.response_meta_path)
        usage = _get_usage(meta)
        sum_in += _input_tokens(usage)
        sum_cached += _cached_tokens(usage)
        if isinstance(meta.get("elapsed_ms"), int):
            sum_elapsed += int(meta["elapsed_ms"])
            n_elapsed += 1

        # group by cache ident if present
        cap_meta_path = c.dir / "capture.meta.json"
        if cap_meta_path.exists():
            cm = _read_json(cap_meta_path)
            ci = cm.get("cache_ident") or {}
            if isinstance(ci, dict):
                sid = ci.get("prompt_cache_key") or ci.get("session_id") or ci.get("x-session-id")
                if sid:
                    by_session.setdefault(str(sid), []).append(c)

    hit_rate = (sum_cached / sum_in) if sum_in > 0 else 0.0
    avg_elapsed = (sum_elapsed / n_elapsed) if n_elapsed else 0

    lines = []
    lines.append(f"captures: {total}")
    lines.append(f"input_tokens: {sum_in}")
    lines.append(f"cached_tokens: {sum_cached}")
    lines.append(f"cache_hit_rate: {hit_rate:.3f}")
    if n_elapsed:
        lines.append(f"avg_elapsed_ms: {avg_elapsed:.0f}")

    if by_session:
        lines.append("")
        lines.append("cache_ident groups:")
        for sid, lst in sorted(by_session.items(), key=lambda kv: -len(kv[1]))[:20]:
            lines.append(f"- {sid}: {len(lst)} calls")

    return "\n".join(lines)


def diff_paths(root: Path, id1: str, id2: str) -> tuple[Path, Path]:
    def find_dir(rid: str) -> Path:
        # Search shallowly.
        for day in root.iterdir():
            if not day.is_dir():
                continue
            p = day / rid
            if p.exists() and p.is_dir():
                return p
        raise FileNotFoundError(rid)

    d1 = find_dir(id1)
    d2 = find_dir(id2)
    return d1 / "request.body.normalized.json", d2 / "request.body.normalized.json"


def cli() -> None:
    ap = argparse.ArgumentParser(description="Analyze response-api-proxy captures")
    ap.add_argument("--dir", default="./logs", help="Log dir (default: ./logs)")
    ap.add_argument("--since", default=None, help="Start time (YYYY-MM-DD HH:MM or ISO)")
    ap.add_argument("--until", default=None, help="End time (YYYY-MM-DD HH:MM or ISO)")
    ap.add_argument("--diff", nargs=2, metavar=("ID1", "ID2"), help="Print normalized JSON paths for git diff")

    args = ap.parse_args()
    root = Path(args.dir)

    if args.diff:
        p1, p2 = diff_paths(root, args.diff[0], args.diff[1])
        print("normalized request paths:")
        print(str(p1))
        print(str(p2))
        print("")
        print("git diff suggestion:")
        print(f"  git diff --no-index -- {p1} {p2}")
        return

    caps = find_captures(root)
    if args.since:
        dt = _parse_dt(args.since)
        caps = [c for c in caps if c.captured_at and c.captured_at >= dt]
    if args.until:
        dt = _parse_dt(args.until)
        caps = [c for c in caps if c.captured_at and c.captured_at <= dt]

    print(summarize_cache(caps))


if __name__ == "__main__":
    cli()
