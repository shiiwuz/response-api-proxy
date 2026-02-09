# response-api-proxy

Local proxy for the OpenAI **Responses API** (or OpenAI-compatible upstream).

Goals:
- Forward requests to upstream.
- Log **full request JSON** + **request headers** (with safe redaction by default).
- Log response metadata (status + usage when available).
- Analyze cache usage over time (eg `cached_tokens`).
- Make it easy to use `git diff` to verify prompt prefix stability.

## Quickstart (uv)

```bash
cd projects/response-api-proxy

# If you use uv:
uv sync

export RAP_UPSTREAM_BASE_URL="https://api.openai.com"
# Option A (recommended): set upstream key in proxy env
export RAP_UPSTREAM_API_KEY="..."
# Option B (transparent pass-through): DO NOT set RAP_UPSTREAM_API_KEY,
# and let clients send `Authorization: Bearer ...` to the proxy.

# Log dir (default: ./logs)
export RAP_LOG_DIR="./logs"

# Run proxy on :8080
uv run rap-proxy --host 0.0.0.0 --port 8080
```

Then point your client at the proxy:
- Base URL: `http://127.0.0.1:8080`
- Endpoint (preferred): `POST /openai/responses` (rewritten upstream; default `/v1/responses`)
- Endpoint (legacy alias): `POST /openai/v1/response`

If your upstream uses a custom path, set:

```bash
export RAP_UPSTREAM_RESPONSES_PATH="/openai/response"
```

## What gets logged

Each call writes a directory like:

`logs/2026-02-09/20260209T180000Z_abcdef12/`
- `request.headers.json` (redacted by default)
- `request.body.json` (raw JSON as received)
- `request.body.normalized.json` (stable JSON for diffing)
- `response.meta.json` (status, latency, usage)
- `response.body.json` (for non-streaming responses only; optional)
- `response.sse.txt` (for streaming responses only; optional)

### Sensitive headers
By default we redact:
- `authorization`
- `cookie`
- `set-cookie`

If you really want to store them unredacted (NOT recommended), set:

```bash
export RAP_LOG_SENSITIVE_HEADERS=1
```

## Analyze caching

Show cache stats over a time range:

```bash
uv run rap-analyze --dir ./logs --since "2026-02-09 00:00" --until "2026-02-10 00:00"
```

Compare two captured requests for prefix stability:

```bash
uv run rap-analyze --dir ./logs --diff <request_id_1> <request_id_2>
# It will print paths you can git-diff.
```

## Docker

```bash
docker build -f docker/Dockerfile -t response-api-proxy:local .

# Example: upstream has a non-standard base path (eg /openai) and uses
# a singular endpoint like /v1/response.

docker run --rm -p 8080:8080 \
  -e RAP_UPSTREAM_BASE_URL=https://crs.uuid.im/openai \
  -e RAP_UPSTREAM_RESPONSES_PATH=/v1/response \
  # Option A: set upstream key in proxy env
  -e RAP_UPSTREAM_API_KEY=... \
  -v "$PWD/logs:/app/logs" \
  response-api-proxy:local

# Option B (transparent pass-through): omit RAP_UPSTREAM_API_KEY and let clients
# send Authorization to the proxy.
```

### Smoke test

Run a quick call through the proxy:

```bash
export RAP_TEST_BASE="http://127.0.0.1:8080"
# The test script accepts RAP_TEST_API_KEY, OPENAI_API_KEY, or OEPNAI_API_KEY.
export RAP_TEST_API_KEY="..."  # client key
uv run python3 scripts/test_call.py --model gpt-4o-mini
```
