"""HTML page routes for the web interface."""

from pathlib import Path

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ...db import get_db
from ..auth import verify_password, create_session_token, SESSION_COOKIE

router = APIRouter()
templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    """Login page."""
    return templates.TemplateResponse("pages/login.html", {
        "request": request,
        "error": error,
    })


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    """Handle login form submission."""
    if verify_password(password):
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=create_session_token(),
            httponly=True,
            secure=True,  # Only send over HTTPS
            samesite="lax",
            max_age=60 * 60 * 24 * 30,  # 30 days
        )
        return response
    else:
        return templates.TemplateResponse("pages/login.html", {
            "request": request,
            "error": "Invalid password",
        })


@router.get("/logout")
async def logout():
    """Log out and clear session."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=SESSION_COOKIE)
    return response


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard showing rating stats and recent opportunities."""
    db = get_db()
    stats = db.get_rating_stats()
    recent = db.get_unrated_opportunities(limit=5)

    return templates.TemplateResponse("pages/dashboard.html", {
        "request": request,
        "stats": stats,
        "recent_opportunities": recent,
    })


@router.get("/rate", response_class=HTMLResponse)
async def rate_page(request: Request):
    """Rating interface - card-based queue."""
    db = get_db()
    opportunities = db.get_unrated_opportunities(limit=20)
    stats = db.get_rating_stats()

    return templates.TemplateResponse("pages/rate.html", {
        "request": request,
        "opportunities": opportunities,
        "stats": stats,
    })


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """View rating history."""
    db = get_db()
    rated = db.get_rated_opportunities(limit=50)

    return templates.TemplateResponse("pages/history.html", {
        "request": request,
        "opportunities": rated,
    })


@router.get("/preferences", response_class=HTMLResponse)
async def preferences_page(request: Request):
    """View learned preferences and signal weights."""
    db = get_db()
    weights = db.get_signal_weights()
    examples = db.get_scoring_examples(limit=10)
    budget = db.get_example_token_budget()

    return templates.TemplateResponse("pages/preferences.html", {
        "request": request,
        "signal_weights": weights,
        "examples": examples,
        "token_budget": budget,
    })


@router.get("/opportunities", response_class=HTMLResponse)
async def opportunities_page(request: Request, sort: str = "ai_score", order: str = "desc"):
    """View all opportunities with filtering."""
    db = get_db()
    opportunities = db.get_all_opportunities(sort=sort, order=order)
    stats = db.get_rating_stats()

    return templates.TemplateResponse("pages/opportunities.html", {
        "request": request,
        "opportunities": opportunities,
        "stats": stats,
        "current_sort": sort,
        "current_order": order,
    })
