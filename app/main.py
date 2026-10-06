"""FastAPI entry point: `uvicorn app.main:app`.

The texting assistant's web surface is deliberately tiny: a health check,
the inbound-message webhook (added with Twilio), and the one-time connect
page. There is no student dashboard; the product is the text conversation.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .web.routes import WEB_DIR
from .web.routes import router as web_router

SECURITY_HEADERS = {
    "Cache-Control": "no-store",              # one-time links and personal data
    "Referrer-Policy": "no-referrer",         # never leak /s/<token> to other sites
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
}


def create_app() -> FastAPI:
    app = FastAPI(title="Texting academic assistant", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(web_router)
    return app


app = create_app()
