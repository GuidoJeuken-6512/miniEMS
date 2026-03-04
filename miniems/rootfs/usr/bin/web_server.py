"""FastAPI ingress dashboard for miniEMS.

HTML lives in templates/dashboard.html, CSS in static/style.css.
"""
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from const import VERSION

_LOGGER = logging.getLogger(__name__)

_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))


def create_app(status_store: dict[str, Any]) -> FastAPI:
    """Create FastAPI app; status_store is the shared dict updated by EMS loop."""
    app = FastAPI(title="miniEMS", docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            "dashboard.html", {"request": request, "version": VERSION}
        )

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return status_store

    return app
