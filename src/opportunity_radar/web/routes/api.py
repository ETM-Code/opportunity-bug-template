"""API routes for the rating system."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...db import get_db
from ..learning import update_signal_weights_from_rating, add_rating_example

logger = logging.getLogger(__name__)
router = APIRouter()


class RatingRequest(BaseModel):
    """Request body for rating an opportunity."""
    rating: int
    feedback: Optional[str] = None


class RatingResponse(BaseModel):
    """Response after rating."""
    success: bool
    message: str
    opportunity_id: str
    rating: int


# --- Opportunity Endpoints ---

@router.get("/opportunities/unrated")
async def get_unrated_opportunities(limit: int = 20):
    """Get opportunities that haven't been rated yet."""
    db = get_db()
    opportunities = db.get_unrated_opportunities(limit=limit)
    return {"opportunities": opportunities, "count": len(opportunities)}


@router.get("/opportunities/rated")
async def get_rated_opportunities(limit: int = 50):
    """Get rated opportunities for review."""
    db = get_db()
    opportunities = db.get_rated_opportunities(limit=limit)
    return {"opportunities": opportunities, "count": len(opportunities)}


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(opportunity_id: str):
    """Get a single opportunity by ID."""
    db = get_db()
    # Query by ID
    result = db._request("GET", f"opportunities?id=eq.{opportunity_id}")
    if not result:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return result[0]


@router.post("/opportunities/{opportunity_id}/rate", response_model=RatingResponse)
async def rate_opportunity(opportunity_id: str, request: RatingRequest):
    """Submit a rating for an opportunity."""
    if not 1 <= request.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    db = get_db()

    # Check opportunity exists
    opp_result = db._request("GET", f"opportunities?id=eq.{opportunity_id}")
    if not opp_result:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    opportunity = opp_result[0]

    # Save the rating
    db.upsert_rating(opportunity_id, request.rating, request.feedback)

    # Update signal weights based on rating
    update_signal_weights_from_rating(db, opportunity, request.rating)

    # Add as scoring example
    add_rating_example(db, opportunity, request.rating)

    logger.info(f"Rated opportunity {opportunity_id}: {request.rating}/5")

    return RatingResponse(
        success=True,
        message=f"Rated {request.rating}/5",
        opportunity_id=opportunity_id,
        rating=request.rating
    )


# --- Statistics Endpoints ---

@router.get("/ratings/stats")
async def get_rating_stats():
    """Get rating statistics."""
    db = get_db()
    return db.get_rating_stats()


# --- Preference Endpoints ---

@router.get("/preferences/signals")
async def get_signal_weights():
    """Get current learned signal weights."""
    db = get_db()
    weights = db.get_signal_weights()
    return {"signals": weights, "count": len(weights)}


# --- Example Endpoints ---

@router.get("/examples")
async def get_scoring_examples():
    """Get current few-shot examples."""
    db = get_db()
    examples = db.get_scoring_examples(limit=20)
    budget = db.get_example_token_budget()
    return {
        "examples": examples,
        "count": len(examples),
        "token_budget": budget
    }


@router.get("/examples/budget")
async def get_example_budget():
    """Get token budget status for examples."""
    db = get_db()
    return db.get_example_token_budget()


@router.post("/examples/condense")
async def trigger_condensation():
    """Trigger example condensation (if over budget)."""
    # This will be implemented in the learning module
    from ..learning import maybe_condense_examples
    db = get_db()
    result = await maybe_condense_examples(db)
    return result
