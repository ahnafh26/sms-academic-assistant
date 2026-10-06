"""FastAPI entry point: `uvicorn app.main:app`.

The texting assistant's web surface is deliberately tiny: a health check,
the inbound-message webhook, and the one-time connect page. There is no
student dashboard; the product is the text conversation.
"""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Texting academic assistant", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    return app


app = create_app()
