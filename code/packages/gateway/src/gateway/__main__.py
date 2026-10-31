"""Entry point for `uv run python -m gateway` (MCP server on :8001/mcp)."""

import uvicorn

from core.config import get_settings
from gateway.server import create_app


def main() -> None:
    # Only a local proxy (Caddy, tunnel agent) reaches uvicorn: trust X-Forwarded-* from it alone.
    uvicorn.run(
        create_app(get_settings()),
        host="127.0.0.1",
        port=8001,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
