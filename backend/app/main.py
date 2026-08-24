"""DRISHTI application entry point.

Serves the JSON API, the raster layers and the static front end from a single
process, so the whole system starts with one command and no build step. That is
a deployment decision as much as a convenience: the target is a district
emergency operations centre, which may be a laptop on a generator with no
internet and no package manager.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router

# A frozen desktop build unpacks its data elsewhere, so the location is taken
# from the environment when the packager has set it.
FRONTEND = os.environ.get("DRISHTI_FRONTEND") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))

app = FastAPI(
    title="DRISHTI",
    version="1.0.0",
    description=(
        "District Risk Intelligence and Satellite Hazard Tracking Interface.\n\n"
        "Flood intelligence for a district emergency operations centre: "
        "inundation extent and depth, ranked worst-affected zones, and an "
        "NDMA-SOP-derived response plan.\n\n"
        "**Data provenance is reported on every response.** In demo mode the "
        "input rasters are modelled rather than observed; the algorithms are "
        "the published methods listed at `/api/methods` in either mode."
    ),
)

# Wide open by design: this is a read-only public-good API with no credentials
# and no mutating endpoints.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)

app.include_router(router)


@app.middleware("http")
async def _revalidate_assets(request, call_next):
    """Make the browser revalidate front-end assets instead of trusting memory.

    Starlette already sends ETags, but a browser is free to serve a module from
    its in-memory cache without asking. That is how a rebuilt file silently does
    not take effect — and finding that out during a live demonstration, with a
    half-old half-new interface on screen, is not a debugging session anyone
    wants. ``no-cache`` still permits a 304, so this costs a conditional request
    and nothing more.
    """
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css", ".html")) or path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.on_event("startup")
def _prewarm() -> None:
    """Warm the live screens in the background.

    A live national sweep is three batched HTTP requests and takes the better
    part of a minute. Doing it lazily on the first page load means the opening
    screen of a demonstration is a spinner, so it is started at boot and served
    from cache thereafter. Failure is silent and harmless: the screen falls back
    to modelled scoring and says so.
    """
    import threading

    def run_national() -> None:
        try:
            from .core import national
            national.get(0.75, True)
        except Exception:
            pass

    def run_watch() -> None:
        """Warm the live board, which is now the screen the app opens on.

        It is the most expensive thing here - twenty-two coordinates of weather,
        the district assessments behind the exposure figures, and a hydrological
        simulation for anything that fires - and it is the first thing anybody
        sees. Left lazy, the opening screen of a demonstration is a spinner for
        several minutes. Replay boards are read from disk and need no warming.
        """
        try:
            from .core import watch
            watch.get(with_river=True)
        except Exception:
            pass

    threading.Thread(target=run_national, daemon=True).start()
    threading.Thread(target=run_watch, daemon=True).start()


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(os.path.join(FRONTEND, "index.html"))


if os.path.isdir(FRONTEND):
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
