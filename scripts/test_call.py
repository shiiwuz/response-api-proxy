#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


def _pick_text(resp: dict[str, Any]) -> str:
    # Try common shapes.
    if isinstance(resp.get("output_text"), str) and resp["output_text"].strip():
        return resp["output_text"].strip()

    out = resp.get("output")
    if isinstance(out, list):
        parts: list[str] = []
        for item in out:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if isinstance(c, dict) and c.get("type") in {"output_text", "text"}:
                    t = c.get("text")
                    if isinstance(t, str) and t:
                        parts.append(t)
        if parts:
            return "\n".join(parts).strip()

    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Call Responses API through response-api-proxy")
    ap.add_argument("--base", default=os.getenv("RAP_TEST_BASE", "http://127.0.0.1:8080"))
    ap.add_argument("--path", default=os.getenv("RAP_TEST_PATH", "/openai/v1/response"))
    ap.add_argument("--model", default=os.getenv("RAP_TEST_MODEL", "gpt-4o-mini"))
    ap.add_argument("--text", default=os.getenv("RAP_TEST_TEXT", "Say a short hello, then list 3 cache-related keywords."))
    ap.add_argument("--max-output", type=int, default=int(os.getenv("RAP_TEST_MAX_OUTPUT", "120")))
    args = ap.parse_args()

    api_key = os.getenv("RAP_TEST_API_KEY")
    if not api_key:
        print("Missing env RAP_TEST_API_KEY (client key to send to proxy)", file=sys.stderr)
        return 2

    url = args.base.rstrip("/") + args.path

    headers = {
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }

    body = {
        "model": args.model,
        "input": args.text,
        "max_output_tokens": args.max_output,
        # keep non-streaming for simplest test
        "stream": False,
    }

    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, headers=headers, json=body)

    print(f"status: {r.status_code}")
    ct = r.headers.get("content-type", "")
    print(f"content-type: {ct}")

    try:
        data = r.json()
    except Exception:
        print("non-json response (first 500 bytes):")
        print(r.text[:500])
        return 0

    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict):
        print("usage:")
        print(json.dumps(usage, ensure_ascii=True, indent=2, sort_keys=True))

    text = _pick_text(data if isinstance(data, dict) else {})
    if text:
        print("output_text:")
        print(text[:800])
    else:
        print("response json keys:")
        if isinstance(data, dict):
            print(", ".join(sorted(list(data.keys()))))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
