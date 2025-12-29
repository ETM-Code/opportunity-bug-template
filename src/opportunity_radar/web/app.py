"""FastAPI application for Opportunity Radar web interface."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .routes import api, pages
from .auth import AuthMiddleware

logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="OpportunityBug",
    description="Discover, rate, and track opportunities - fellowships, internships, hackathons, and jobs",
    version="1.0.0",
)

# Add authentication middleware
app.add_middleware(AuthMiddleware)

# Static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=templates_dir)

# Include routers
app.include_router(api.router, prefix="/api/v1", tags=["api"])
app.include_router(pages.router, tags=["pages"])


@app.get("/health")
async def health_check():
    """Health check endpoint for Fly.io."""
    return {"status": "healthy", "service": "opportunitybug"}
