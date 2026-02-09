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


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Call Responses API through response-api-proxy")
    ap.add_argument("--base", default=os.getenv("RAP_TEST_BASE", "http://127.0.0.1:8080"))
    ap.add_argument("--path", default=os.getenv("RAP_TEST_PATH", "/openai/v1/response"))
    ap.add_argument("--model", default=os.getenv("RAP_TEST_MODEL", "gpt-4o-mini"))
    ap.add_argument("--text", default=os.getenv("RAP_TEST_TEXT", "Say a short hello, then list 3 cache-related keywords."))
    ap.add_argument("--max-output", type=int, default=int(os.getenv("RAP_TEST_MAX_OUTPUT", "120")))
    ap.add_argument("--stream", action="store_true", default=_env_bool("RAP_TEST_STREAM", False))
    args = ap.parse_args()

    # Prefer the explicit test var, but allow common key envs so you can
    # `source ~/.openclaw/.env` and run without re-exporting.
    api_key = (
        os.getenv("RAP_TEST_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        # Back-compat for a common typo seen in some local env files.
        or os.getenv("OEPNAI_API_KEY")
    )
    if not api_key:
        print(
            "Missing API key env. Set RAP_TEST_API_KEY, or OPENAI_API_KEY (or OEPNAI_API_KEY).",
            file=sys.stderr,
        )
        return 2

    url = args.base.rstrip("/") + args.path

    headers = {
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }

    # Responses API accepts a string in some implementations, but many
    # OpenAI-compatible proxies expect a structured list.
    body = {
        "model": args.model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": args.text,
                    }
                ],
            }
        ],
        "max_output_tokens": args.max_output,
        "stream": bool(args.stream),
    }

    if args.stream:
        data_events: list[str] = []
        sample_lines: list[str] = []

        with httpx.Client(timeout=60.0) as client:
            with client.stream("POST", url, headers=headers, json=body) as r:
                print(f"status: {r.status_code}")
                ct = r.headers.get("content-type", "")
                print(f"content-type: {ct}")

                # Consume bytes and parse SSE ourselves (more robust than iter_lines
                # across proxies).
                pending = ""
                max_lines = 4000
                max_sample_lines = 20

                try:
                    for chunk in r.iter_bytes():
                        pending += chunk.decode("utf-8", errors="replace")
                        while "\n" in pending:
                            line, pending = pending.split("\n", 1)
                            line = line.rstrip("\r")
                            if line and len(sample_lines) < max_sample_lines:
                                sample_lines.append(line)

                            if not line.startswith("data:"):
                                continue

                            payload = line[5:].strip()
                            data_events.append(payload)

                            if payload == "[DONE]":
                                pending = ""
                                break

                            # Stop if the stream indicates completion.
                            try:
                                obj = json.loads(payload)
                            except Exception:
                                obj = None
                            if isinstance(obj, dict):
                                if obj.get("type") in {"response.completed", "response.complete"}:
                                    pending = ""
                                    break
                                if isinstance(obj.get("response"), dict) and obj["response"].get("status") in {
                                    "completed",
                                    "complete",
                                }:
                                    pending = ""
                                    break

                            if len(sample_lines) >= max_lines:
                                pending = ""
                                break

                        if not pending and (data_events and (data_events[-1] == "[DONE]")):
                            break
                except httpx.RemoteProtocolError:
                    # Some upstreams terminate chunked SSE streams abruptly.
                    pass

        print("sse_sample_lines:")
        for l in sample_lines:
            print(l[:400])

        # Try to parse a usage object from captured events.
        for x in reversed(data_events):
            if x == "[DONE]":
                continue
            try:
                obj = json.loads(x)
            except Exception:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                print("usage:")
                print(json.dumps(obj["usage"], ensure_ascii=True, indent=2, sort_keys=True))
                break

        return 0

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
