"""DRISHTI application entry point.

Serves the JSON API, the raster layers, and the static front end from a single
process, so the whole system starts with one command and no build step.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Safe import whether invoked as 'backend.app.main' or 'app.main'
try:
    from .api.routes import router
except (ImportError, ValueError):
    from api.routes import router


# Resolve absolute path to the repository root and frontend folder
CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent.parent
FRONTEND_ENV = os.environ.get("DRISHTI_FRONTEND")
FRONTEND = Path(FRONTEND_ENV).resolve() if FRONTEND_ENV else (REPO_ROOT / "frontend")


def _run_national_warmup() -> None:
    try:
        try:
            from .core import national
        except (ImportError, ValueError):
            from core import national
        national.get(0.75, True)
    except Exception:
        pass


def _run_watch_warmup() -> None:
    try:
        try:
            from .core import watch
        except (ImportError, ValueError):
            from core import watch
        watch.get(with_river=True)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the live screens in background threads upon startup."""
    threading.Thread(target=_run_national_warmup, daemon=True).start()
    threading.Thread(target=_run_watch_warmup, daemon=True).start()
    yield


app = FastAPI(
    title="DRISHTI",
    version="1.0.0",
    description=(
        "District Risk Intelligence and Satellite Hazard Tracking Interface.\n\n"
        "Flood intelligence for a district emergency operations centre: "
        "inundation extent and depth, ranked worst-affected zones, and an "
        "NDMA-SOP-derived response plan.\n\n"
        "**Data provenance is reported on every response.**"
    ),
    lifespan=lifespan,
)

# Open CORS for API clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Attach backend API routes first
app.include_router(router)


@app.middleware("http")
async def _revalidate_assets(request, call_next):
    """Ensure front-end assets revalidate rather than stale in browser memory."""
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css", ".html")) or path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


# Root route fallback
@app.get("/", include_in_schema=False)
def index():
    index_file = FRONTEND / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse(
        "<h2>DRISHTI Backend Active</h2><p>Frontend assets are compiling or missing. Visit <a href='/docs'>/docs</a> for the API.</p>",
        status_code=200,
    )


# Mount static assets if the folder exists
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")