from __future__ import annotations

import json
import os
import time
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import Request, Response
from starlette.responses import StreamingResponse

from .store import LocalStore, StorePaths, getenv_bool
from .util import redact_headers, utcnow_iso_z


def _lower_keys(d: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in d.items()}


def _pick_request_cache_ident(request_headers: dict[str, str], body_obj: Any) -> dict[str, Any]:
    # Best-effort extraction of stickiness/cache identifiers.
    hdrs = _lower_keys(request_headers)

    out: dict[str, Any] = {
        "session_id": hdrs.get("session_id"),
        "x-session-id": hdrs.get("x-session-id"),
        "prompt_cache_key": None,
    }

    if isinstance(body_obj, dict):
        # Some SDKs pass sessionId through this field.
        out["prompt_cache_key"] = body_obj.get("prompt_cache_key") or body_obj.get("promptCacheKey")

    return {k: v for k, v in out.items() if v}


def _looks_like_sse_response(headers: dict[str, str]) -> bool:
    ct = headers.get("content-type", "")
    return "text/event-stream" in ct.lower()


class ProxyServer:
    def __init__(self) -> None:
        self.upstream_base_url = os.getenv("RAP_UPSTREAM_BASE_URL", "https://api.openai.com").rstrip("/")
        # Where this proxy should send Responses API calls upstream.
        # This lets you proxy non-standard upstream routes (eg /openai/response).
        self.upstream_responses_path = os.getenv("RAP_UPSTREAM_RESPONSES_PATH", "/v1/responses")
        self.upstream_api_key = os.getenv("RAP_UPSTREAM_API_KEY")
        self.log_dir = os.getenv("RAP_LOG_DIR", "./logs")
        self.log_sensitive_headers = getenv_bool("RAP_LOG_SENSITIVE_HEADERS", False)
        self.capture_response_body = getenv_bool("RAP_CAPTURE_RESPONSE_BODY", True)
        self.capture_sse_text = getenv_bool("RAP_CAPTURE_SSE_TEXT", True)
        self.max_capture_bytes = int(os.getenv("RAP_MAX_CAPTURE_BYTES", "5000000"))

        self.store = LocalStore(self.log_dir)

        if not self.upstream_api_key:
            # We allow passing Authorization from client directly, but this env is the preferred mode.
            pass

    def _upstream_url(self, req: Request) -> str:
        # Allow an opinionated "proxy namespace" path while still calling the
        # real upstream Responses endpoint.
        in_path = req.url.path.rstrip("/")
        # Opinionated inbound path(s) -> upstream Responses endpoint.
        if in_path in {"/openai/v1/response", "/openai/v1/responses", "/v1/responses"}:
            out_path = self.upstream_responses_path
        else:
            out_path = req.url.path

        q = ("?" + str(req.url.query)) if req.url.query else ""
        return f"{self.upstream_base_url}{out_path}{q}"

    def _build_upstream_headers(self, req: Request) -> dict[str, str]:
        # Start with inbound headers and then enforce Authorization if configured.
        headers = dict(req.headers)

        # Remove hop-by-hop headers.
        for k in ["host", "content-length", "connection", "accept-encoding"]:
            headers.pop(k, None)

        if self.upstream_api_key:
            headers["authorization"] = f"Bearer {self.upstream_api_key}"

        return headers

    async def _forward_non_stream(
        self,
        client: httpx.AsyncClient,
        sp: StorePaths,
        method: str,
        url: str,
        headers: dict[str, str],
        body_bytes: bytes,
        started_at: float,
    ) -> Response:
        r = await client.request(method, url, headers=headers, content=body_bytes)

        elapsed_ms = int((time.time() - started_at) * 1000)
        meta: dict[str, Any] = {
            "upstream_url": url,
            "status_code": r.status_code,
            "elapsed_ms": elapsed_ms,
            "captured_at": utcnow_iso_z(),
        }

        # Try to parse JSON response.
        body_obj: Optional[Any] = None
        if self.capture_response_body:
            try:
                body_obj = r.json()
                self.store.save_response_body_json(sp, body_obj)
            except Exception:
                body_obj = None

        if isinstance(body_obj, dict) and "usage" in body_obj:
            meta["usage"] = body_obj.get("usage")

        self.store.save_response_meta(sp, meta)

        # Return raw bytes to preserve upstream behavior.
        out_headers = {k: v for k, v in r.headers.items() if k.lower() not in {"content-encoding", "transfer-encoding"}}
        return Response(content=r.content, status_code=r.status_code, headers=out_headers)

    async def _stream_bytes_with_capture(
        self,
        stream: httpx.Response,
        capture: bytearray,
    ) -> AsyncIterator[bytes]:
        async for chunk in stream.aiter_bytes():
            if len(capture) < self.max_capture_bytes:
                take = min(len(chunk), self.max_capture_bytes - len(capture))
                capture.extend(chunk[:take])
            yield chunk

    def _parse_usage_from_sse(self, sse_text: str) -> Optional[dict[str, Any]]:
        # Responses streaming typically emits JSON events per line.
        # We'll search for a JSON object containing "usage".
        for line in sse_text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            if isinstance(obj, dict) and "usage" in obj and isinstance(obj["usage"], dict):
                return obj["usage"]
        return None

    async def _forward_stream(
        self,
        client: httpx.AsyncClient,
        sp: StorePaths,
        method: str,
        url: str,
        headers: dict[str, str],
        body_bytes: bytes,
        started_at: float,
    ) -> Response:
        # IMPORTANT: keep the upstream response open until the downstream client
        # finishes consuming it. Do not use `async with client.stream(...)` here,
        # because it would close the upstream before StreamingResponse iterates.
        capture = bytearray()

        req = client.build_request(method, url, headers=headers, content=body_bytes)
        r = await client.send(req, stream=True)

        out_headers = {
            k: v
            for k, v in r.headers.items()
            if k.lower() not in {"content-encoding", "transfer-encoding"}
        }

        async def gen() -> AsyncIterator[bytes]:
            try:
                async for chunk in r.aiter_bytes():
                    if len(capture) < self.max_capture_bytes:
                        take = min(len(chunk), self.max_capture_bytes - len(capture))
                        capture.extend(chunk[:take])
                    yield chunk
            except Exception:
                # Common cases:
                # - downstream client disconnects (cancels the response)
                # - upstream closes abruptly mid-stream
                # Either way, we still want to persist the partial capture.
                pass
            finally:
                await r.aclose()

                elapsed_ms = int((time.time() - started_at) * 1000)
                meta: dict[str, Any] = {
                    "upstream_url": url,
                    "status_code": r.status_code,
                    "elapsed_ms": elapsed_ms,
                    "captured_at": utcnow_iso_z(),
                    "streaming": True,
                    "capture_truncated": len(capture) >= self.max_capture_bytes,
                }

                if self.capture_sse_text:
                    try:
                        sse_text = capture.decode("utf-8", errors="replace")
                        self.store.save_response_sse(sp, sse_text)
                        usage = self._parse_usage_from_sse(sse_text)
                        if usage:
                            meta["usage"] = usage
                    except Exception:
                        pass

                self.store.save_response_meta(sp, meta)

        return StreamingResponse(gen(), status_code=r.status_code, headers=out_headers)

    async def handle(self, req: Request) -> Response:
        sp = self.store.new_request_dir()
        started_at = time.time()

        # Read request body (JSON expected for /v1/responses).
        body_bytes = await req.body()
        body_obj: Any
        try:
            body_obj = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            body_obj = {"_raw": body_bytes.decode("utf-8", errors="replace")}

        # Persist request capture.
        raw_headers = dict(req.headers)
        self.store.save_request(sp, redact_headers(raw_headers, self.log_sensitive_headers), body_obj)

        cache_ident = _pick_request_cache_ident(raw_headers, body_obj)
        self.store.save_meta(
            sp,
            {
                "method": req.method,
                "path": req.url.path,
                "query": str(req.url.query),
                **({"cache_ident": cache_ident} if cache_ident else {}),
            },
        )

        url = self._upstream_url(req)
        headers = self._build_upstream_headers(req)
        method = req.method

        # Detect streaming intent.
        wants_stream = False
        if isinstance(body_obj, dict) and body_obj.get("stream") is True:
            wants_stream = True
        if "text/event-stream" in (req.headers.get("accept", "").lower()):
            wants_stream = True

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
            if wants_stream:
                return await self._forward_stream(client, sp, method, url, headers, body_bytes, started_at)
            return await self._forward_non_stream(client, sp, method, url, headers, body_bytes, started_at)
