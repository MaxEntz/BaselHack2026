"""Entry point for `uv run python -m gateway` (MCP server on :8001/mcp, JSON logs on stderr)."""

import uvicorn

from core.config import get_settings
from core.logs import use_json_logs
from gateway.server import create_app

# Loopback only: the proxy in front (Caddy, `deploy/Caddyfile`, or a tunnel agent) reaches it here.
HOST = "127.0.0.1"
PORT = 8001


def main() -> None:
    use_json_logs()
    # Only that local proxy reaches uvicorn: trust X-Forwarded-* from it alone.
    uvicorn.run(
        create_app(get_settings()),
        host=HOST,
        port=PORT,
        proxy_headers=True,
        forwarded_allow_ips=HOST,
        log_config=None,
    )


if __name__ == "__main__":
    main()
