"""Preference learning module for the rating system."""

import json
import logging
from typing import Any

from ..db import Database
from ..config import get_config

logger = logging.getLogger(__name__)

# Configuration
EXAMPLE_CONFIG = {
    "max_total_tokens": 2000,       # Budget for all examples
    "min_examples_per_category": 1,  # At least 1 good, 1 bad
    "max_examples_per_category": 3,  # Cap per category
    "condensation_threshold": 0.8,   # Trigger at 80% budget
    "target_tokens_after_condense": 1200,  # Target after condensation
}

# Learning rate for signal weight updates
LEARNING_RATE = 0.1


def update_signal_weights_from_rating(db: Database, opportunity: dict, rating: int):
    """Update signal weights based on a user rating.

    Rating 4-5: Boost matched high signals, reduce penalty of matched low signals
    Rating 1-2: Penalize matched high signals, boost penalty of matched low signals
    Rating 3: Minor regression to mean (weight 1.0)
    """
    matched_high = opportunity.get("matched_high_signals", [])
    matched_low = opportunity.get("matched_low_signals", [])

    # Normalize rating to [-1, +1] scale
    # 5 -> +1, 4 -> +0.5, 3 -> 0, 2 -> -0.5, 1 -> -1
    rating_delta = (rating - 3) / 2

    # Update high value signals
    for signal in matched_high:
        if not signal:
            continue
        current = db.get_signal_weight(signal, "high_value")
        # Good rating + high signal = boost weight
        new_weight = current + (LEARNING_RATE * rating_delta)
        new_weight = max(0.1, min(2.0, new_weight))  # Clamp to [0.1, 2.0]
        db.update_signal_weight(signal, "high_value", new_weight)
        logger.debug(f"Signal '{signal}' weight: {current:.2f} -> {new_weight:.2f}")

    # Update low value signals
    for signal in matched_low:
        if not signal:
            continue
        current = db.get_signal_weight(signal, "low_value")
        # Good rating + low signal = reduce its penalty (paradoxical signal)
        new_weight = current - (LEARNING_RATE * rating_delta)
        new_weight = max(0.1, min(2.0, new_weight))
        db.update_signal_weight(signal, "low_value", new_weight)
        logger.debug(f"Signal '{signal}' (low) weight: {current:.2f} -> {new_weight:.2f}")

    logger.info(f"Updated {len(matched_high)} high and {len(matched_low)} low signal weights")


def add_rating_example(db: Database, opportunity: dict, rating: int):
    """Add a rated opportunity as a scoring example."""
    # Build example text
    example_text = _build_example_text(opportunity, rating)

    # Estimate token count (rough: 4 chars per token)
    token_count = len(example_text) // 4

    # Insert example
    db.insert_scoring_example({
        "opportunity_id": opportunity.get("id"),
        "example_text": example_text,
        "user_rating": rating,
        "token_count": token_count,
        "is_condensed": False,
        "priority": 1.0,
    })

    logger.info(f"Added scoring example ({token_count} tokens, rating {rating}/5)")


def _build_example_text(opportunity: dict, rating: int) -> str:
    """Build example text from an opportunity."""
    parts = [
        f"Title: {opportunity.get('title', 'Unknown')}",
        f"Organization: {opportunity.get('organization', 'Unknown')}",
        f"Type: {opportunity.get('type', 'Unknown')}",
    ]

    if opportunity.get("location"):
        parts.append(f"Location: {opportunity['location']}")

    if opportunity.get("stipend_amount"):
        currency = opportunity.get("stipend_currency", "USD")
        parts.append(f"Stipend: {currency} {opportunity['stipend_amount']}")

    if opportunity.get("travel_support") and opportunity["travel_support"] != "unknown":
        parts.append(f"Travel: {opportunity['travel_support']}")

    if opportunity.get("eligibility"):
        parts.append(f"Eligibility: {opportunity['eligibility'][:100]}")

    # Add matched signals
    high_signals = opportunity.get("matched_high_signals", [])
    low_signals = opportunity.get("matched_low_signals", [])

    if high_signals:
        parts.append(f"High signals: {', '.join(high_signals[:3])}")
    if low_signals:
        parts.append(f"Low signals: {', '.join(low_signals[:3])}")

    parts.append(f"User rating: {rating}/5")

    return " | ".join(parts)


async def maybe_condense_examples(db: Database) -> dict[str, Any]:
    """Check if examples exceed token budget and condense if needed."""
    budget = db.get_example_token_budget()
    total_tokens = budget.get("total", 0)
    max_tokens = EXAMPLE_CONFIG["max_total_tokens"]
    threshold = max_tokens * EXAMPLE_CONFIG["condensation_threshold"]

    if total_tokens < threshold:
        return {
            "condensed": False,
            "reason": f"Under budget ({total_tokens}/{max_tokens} tokens)",
            "current_tokens": total_tokens,
            "max_tokens": max_tokens,
        }

    logger.info(f"Examples at {total_tokens} tokens, triggering condensation")

    # Get all examples grouped by category
    good_examples = db.get_scoring_examples(category="good", limit=20)
    bad_examples = db.get_scoring_examples(category="bad", limit=20)
    neutral_examples = db.get_scoring_examples(category="neutral", limit=20)

    condensed_count = 0
    tokens_saved = 0

    # Condense each category if it has more than max examples
    for category, examples in [("good", good_examples), ("bad", bad_examples), ("neutral", neutral_examples)]:
        max_per_cat = EXAMPLE_CONFIG["max_examples_per_category"]
        if len(examples) <= max_per_cat:
            continue

        # Keep top examples by priority, condense the rest
        keep = examples[:max_per_cat]
        to_condense = examples[max_per_cat:]

        if not to_condense:
            continue

        # For now, just delete older examples instead of LLM condensation
        # LLM condensation can be added later
        delete_ids = [e["id"] for e in to_condense]
        tokens_before = sum(e.get("token_count", 0) for e in to_condense)

        db.delete_scoring_examples(delete_ids)
        condensed_count += len(delete_ids)
        tokens_saved += tokens_before

        logger.info(f"Removed {len(delete_ids)} {category} examples ({tokens_before} tokens)")

    # Log condensation
    new_budget = db.get_example_token_budget()
    db.log_condensation(
        examples_before=len(good_examples) + len(bad_examples) + len(neutral_examples),
        examples_after=new_budget.get("total", 0) // 100,  # Rough count
        tokens_before=total_tokens,
        tokens_after=new_budget.get("total", 0),
        model=None  # No LLM used yet
    )

    return {
        "condensed": True,
        "examples_removed": condensed_count,
        "tokens_saved": tokens_saved,
        "tokens_before": total_tokens,
        "tokens_after": new_budget.get("total", 0),
    }


def get_weighted_signals(db: Database, profile: dict) -> tuple[str, str]:
    """Get formatted signal lists with learned weights for the scoring prompt."""
    high_signals = profile.get("high_value_signals", [])
    low_signals = profile.get("low_value_signals", [])

    # Get weights
    weighted_high = []
    for signal in high_signals:
        weight = db.get_signal_weight(signal, "high_value")
        if weight != 1.0:
            weighted_high.append(f"- {signal} (weight: {weight:.1f})")
        else:
            weighted_high.append(f"- {signal}")

    weighted_low = []
    for signal in low_signals:
        weight = db.get_signal_weight(signal, "low_value")
        if weight != 1.0:
            weighted_low.append(f"- {signal} (weight: {weight:.1f})")
        else:
            weighted_low.append(f"- {signal}")

    return "\n".join(weighted_high), "\n".join(weighted_low)


def get_few_shot_section(db: Database) -> str:
    """Build the few-shot examples section for the scoring prompt."""
    good = db.get_scoring_examples(category="good", limit=2)
    bad = db.get_scoring_examples(category="bad", limit=2)
    neutral = db.get_scoring_examples(category="neutral", limit=1)

    if not good and not bad:
        return ""  # No examples yet

    parts = ["CALIBRATION EXAMPLES (learn from these user ratings):"]

    for i, ex in enumerate(good[:2], 1):
        parts.append(f"\nExample {i} (User rated: {ex['user_rating']}/5 - Good match):")
        parts.append(ex["example_text"])
        parts.append("-> This type should score HIGH (0.75-1.0 relevance)")

    for i, ex in enumerate(bad[:2], len(good) + 1):
        parts.append(f"\nExample {i} (User rated: {ex['user_rating']}/5 - Poor match):")
        parts.append(ex["example_text"])
        parts.append("-> This type should score LOW (0.0-0.35 relevance)")

    if neutral:
        ex = neutral[0]
        parts.append(f"\nExample (User rated: {ex['user_rating']}/5 - Moderate):")
        parts.append(ex["example_text"])
        parts.append("-> This type should score MEDIUM (0.4-0.6 relevance)")

    return "\n".join(parts)
