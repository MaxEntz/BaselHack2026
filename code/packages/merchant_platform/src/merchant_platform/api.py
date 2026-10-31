"""FastAPI app: `/chat` (SSE), `/events` (SSE) and `/health`."""

from fastapi import FastAPI

app = FastAPI(title="Merchant Agents platform")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
