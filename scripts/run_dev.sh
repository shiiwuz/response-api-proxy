#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

: "${RAP_UPSTREAM_BASE_URL:=https://api.openai.com}"
: "${RAP_LOG_DIR:=./logs}"

# RAP_UPSTREAM_API_KEY must be set in env.

exec uv run rap-proxy --host 127.0.0.1 --port 8080 --reload
