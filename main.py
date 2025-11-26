"""
AEGIS main FastAPI application entrypoint.

- Creates the FastAPI app
- Serves the HTML UI
- Configures CORS
- Includes API routes (/chat, /websearch, /fetch_url)
- Ensures DB tables exist on startup
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import router as api_router
from app.db.base import engine, init_db
import app.db.models  # noqa: F401  # ensure models are imported for SQLAlchemy

# --------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------
logger = logging.getLogger("aegis.main")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# --------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------
app = FastAPI(
    title="AEGIS Chat",
    version="0.1.0",
)

# --------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # ok for local dev / personal project
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"      # where index.html lives

# --------------------------------------------------------------------
# Static assets (images, favicon, etc.) at /ui
# --------------------------------------------------------------------
app.mount(
    "/ui",
    StaticFiles(directory=str(UI_DIR), html=False),
    name="ui-static",
)

# --------------------------------------------------------------------
# Root (/) -> serve index.html manually
#   This avoids mounting StaticFiles at "/" so it won't steal /chat
# --------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        logger.error("[main] index.html NOT FOUND at %s", index_path)
        return HTMLResponse("<h1>UI not found</h1>", status_code=500)

    return HTMLResponse(index_path.read_text(encoding="utf-8"))

# --------------------------------------------------------------------
# API routes
#   - POST /chat
#   - GET  /websearch
#   - POST /fetch_url
# --------------------------------------------------------------------
app.include_router(api_router)

# --------------------------------------------------------------------
# Startup: init DB
# --------------------------------------------------------------------
@app.on_event("startup")
def on_startup() -> None:
    logger.info("[main] Initializing DB...")
    init_db(engine)
    logger.info("[main] Database ready.")
