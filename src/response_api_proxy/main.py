from __future__ import annotations

import argparse
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .proxy import ProxyServer


def create_app() -> FastAPI:
    app = FastAPI(title="response-api-proxy", version="0.1.0")
    proxy = ProxyServer()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"ok": "true"}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def catchall(path: str, request: Request):
        # We only meaningfully support JSON requests (eg /v1/responses),
        # but forwarding is generic.
        try:
            return await proxy.handle(request)
        except Exception as e:
            # Keep failures visible in client.
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "type": "proxy_error",
                        "message": str(e),
                        "hint": "Check RAP_UPSTREAM_BASE_URL / RAP_UPSTREAM_API_KEY and upstream availability.",
                    }
                },
            )

    return app


def cli() -> None:
    parser = argparse.ArgumentParser(description="Run a local logging proxy for Responses API")
    parser.add_argument("--host", default=os.getenv("RAP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("RAP_PORT", "8080")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "response_api_proxy.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    cli()
