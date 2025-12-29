"""LLM pipeline for processing opportunities."""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from ..config import get_config, load_sources
from ..db import get_db
from .prompts import CLASSIFY_PROMPT, EXTRACT_PROMPT, SCORE_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class ProcessedOpportunity:
    """Result of processing content through the LLM pipeline."""
    is_opportunity: bool
    confidence: float
    extracted: dict | None
    relevance_score: float | None
    prestige_score: float | None
    recommendation: str | None


class LLMPipeline:
    """Orchestrates LLM calls for opportunity processing."""

    # Model configuration
    FAST_MODEL = "gpt-5-nano"  # For classification and extraction
    SMART_MODEL = "gpt-5-mini"  # For scoring with reasoning

    def __init__(self):
        config = get_config()
        self.client = OpenAI(api_key=config.openai_api_key)
        self._user_profile: dict | None = None

    def _get_user_profile(self) -> dict:
        """Get cached user profile."""
        if self._user_profile is None:
            db = get_db()
            self._user_profile = db.get_user_profile()
            if not self._user_profile:
                # Fallback to YAML config
                sources = load_sources()
                self._user_profile = sources.get("user_profile", {})
        return self._user_profile

    def _call_llm(self, prompt: str, model: str = None, reasoning_effort: str = "low") -> str:
        """Make an LLM call with appropriate settings.

        Args:
            prompt: The prompt to send
            model: Model ID (defaults to FAST_MODEL)
            reasoning_effort: One of 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'
        """
        model = model or self.FAST_MODEL

        try:
            # gpt-5 reasoning models use tokens for hidden reasoning + visible output
            # Need enough tokens for both (reasoning_tokens + output_tokens)
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=8000,  # Enough for reasoning + output
                reasoning_effort=reasoning_effort,
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

    def _parse_json(self, text: str | None) -> dict | None:
        """Parse JSON from LLM response, handling markdown code blocks."""
        if not text:
            return None

        text = text.strip()

        # Remove markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON: {e}")
            logger.debug(f"Raw response: {text}")
            return None

    def classify(self, content: str) -> tuple[bool, float, list[str]]:
        """Classify if content contains an opportunity.

        Returns: (is_opportunity, confidence, opportunity_types)
        """
        # Truncate very long content
        if len(content) > 15000:
            content = content[:15000] + "\n...[truncated]..."

        prompt = CLASSIFY_PROMPT.format(content=content)
        response = self._call_llm(prompt, model=self.FAST_MODEL)

        result = self._parse_json(response)
        if not result:
            return False, 0.0, []

        return (
            result.get("contains_opportunity", False),
            result.get("confidence", 0.0),
            result.get("opportunity_types", [])
        )

    def extract(self, content: str, source_url: str | None = None) -> list[dict]:
        """Extract opportunity details from content.

        Returns a list of opportunities (may be empty, one, or multiple).
        """
        if len(content) > 15000:
            content = content[:15000] + "\n...[truncated]..."

        prompt = EXTRACT_PROMPT.format(content=content)
        response = self._call_llm(prompt, model=self.FAST_MODEL)

        result = self._parse_json(response)
        if not result:
            return []

        # Handle various response formats:
        # - Raw array of opportunities: [{...}, {...}]
        # - Object with opportunities key: {"opportunities": [{...}]}
        # - Single opportunity object: {"title": "..."}
        opportunities = []
        if isinstance(result, list):
            opportunities = result
        elif "opportunities" in result:
            opportunities = result["opportunities"]
        elif "title" in result:
            opportunities = [result]
        else:
            return []

        # Fix relative URLs and add source URL
        for opp in opportunities:
            url = opp.get("url") or ""
            if url.startswith("./") and source_url:
                base = source_url.rsplit("/", 1)[0]
                opp["url"] = f"{base}/{url[2:]}"
            elif not url and source_url:
                opp["url"] = source_url

        # Filter out generic/low-quality extractions
        opportunities = self._filter_generic(opportunities)

        return opportunities

    def _filter_generic(self, opportunities: list[dict]) -> list[dict]:
        """Filter out generic job board pages that aren't specific opportunities."""
        generic_patterns = [
            r'^(find|search|browse|explore|view|see)\s+(your\s+)?(next\s+)?(job|career|role|position)',
            r'^(careers?|jobs?|positions?|openings?|opportunities?)\s+(at|@)\s+',
            r'^(open\s+)?(positions?|roles?)\s*$',
            r'^(join\s+)?(our\s+)?team',
            r'^(work|working)\s+(at|with)\s+',
            r'^(current\s+)?(job\s+)?openings?',
            r'^(we\'?re?\s+)?hiring',
            r'^(check\s+out\s+)?(all\s+)?(open\s+)?jobs?',
            r'^internships?\s+and\s+(early\s+)?talent',
            r'^emerging\s+talent$',
        ]

        filtered = []
        for opp in opportunities:
            title = opp.get("title", "").lower().strip()

            # Check if title matches generic patterns
            is_generic = False
            for pattern in generic_patterns:
                if re.match(pattern, title, re.IGNORECASE):
                    is_generic = True
                    logger.debug(f"Filtering generic opportunity: {opp.get('title')}")
                    break

            # Also filter if title is too short or vague
            if len(title) < 5:
                is_generic = True

            # Filter if no summary or very short summary
            summary = opp.get("summary", "")
            if not summary or len(summary) < 20:
                is_generic = True

            if not is_generic:
                filtered.append(opp)

        if len(opportunities) != len(filtered):
            logger.info(f"Filtered {len(opportunities) - len(filtered)} generic opportunities")

        return filtered

    def score(self, opportunity: dict) -> dict:
        """Score an opportunity for the user profile.

        Returns: {relevance_score, prestige_score, reasoning, recommendation}
        """
        profile = self._get_user_profile()

        # Build profile summary
        profile_text = f"""
Name: {profile.get('name', 'Unknown')}
Background: {profile.get('background', 'Not specified')}
Interests: {', '.join(profile.get('interests', []))}
Constraints: {json.dumps(profile.get('constraints', {}))}
"""

        high_signals = profile.get("high_value_signals", [])
        low_signals = profile.get("low_value_signals", [])

        # Format stipend
        stipend = "Not specified"
        if opportunity.get("stipend_amount"):
            stipend = f"{opportunity.get('stipend_currency', 'USD')} {opportunity['stipend_amount']}"

        prompt = SCORE_PROMPT.format(
            profile=profile_text,
            title=opportunity.get("title", "Unknown"),
            organization=opportunity.get("organization", "Unknown"),
            type=opportunity.get("type", "Unknown"),
            location=opportunity.get("location", "Unknown"),
            is_remote=opportunity.get("is_remote", "Unknown"),
            deadline=opportunity.get("deadline", "Not specified"),
            stipend=stipend,
            travel_support=opportunity.get("travel_support", "Unknown"),
            summary=opportunity.get("summary", "No summary"),
            eligibility=opportunity.get("eligibility", "Not specified"),
            high_value_signals="\n".join(f"- {s}" for s in high_signals),
            low_value_signals="\n".join(f"- {s}" for s in low_signals),
        )

        response = self._call_llm(prompt, model=self.SMART_MODEL, reasoning_effort="medium")
        result = self._parse_json(response)

        if not result:
            return {
                "relevance_score": 0.5,
                "prestige_score": 0.5,
                "reasoning": "Failed to score",
                "recommendation": "maybe"
            }

        return result


def process_content(
    content: str,
    source_url: str | None = None,
    source_id: str | None = None
) -> list[ProcessedOpportunity]:
    """Process content through the full LLM pipeline.

    1. Classify - is this an opportunity?
    2. Extract - pull out structured details (may be multiple)
    3. Score - how relevant is each to the user?
    4. Store - save to database if relevant

    Returns list of ProcessedOpportunity objects (may be empty).
    """
    pipeline = LLMPipeline()
    db = get_db()
    results = []

    # Step 1: Classify
    is_opportunity, confidence, types = pipeline.classify(content)

    if not is_opportunity or confidence < 0.5:
        logger.debug(f"Content not classified as opportunity (confidence: {confidence})")
        return [ProcessedOpportunity(
            is_opportunity=False,
            confidence=confidence,
            extracted=None,
            relevance_score=None,
            prestige_score=None,
            recommendation=None
        )]

    # Step 2: Extract (may return multiple opportunities)
    opportunities = pipeline.extract(content, source_url)
    if not opportunities:
        logger.warning("Failed to extract opportunity details")
        return []

    # Process each opportunity
    for extracted in opportunities:
        if not extracted.get("title"):
            continue

        # Check for duplicates (URL or title+org)
        if extracted.get("url") and db.opportunity_url_exists(extracted["url"]):
            logger.debug(f"Skipping duplicate (URL): {extracted['url']}")
            continue

        title = extracted.get("title", "")
        org = extracted.get("organization", "")
        if title and org and db.opportunity_title_exists(title, org):
            logger.debug(f"Skipping duplicate (title+org): {title} @ {org}")
            continue

        # Step 3: Score
        scores = pipeline.score(extracted)
        extracted["relevance_score"] = scores.get("relevance_score", 0.5)
        extracted["prestige_score"] = scores.get("prestige_score", 0.5)

        # Add metadata
        extracted["raw_content"] = content[:5000]  # Store truncated raw content
        extracted["content_hash"] = db.hash_content(f"{extracted.get('url', '')}{extracted.get('title', '')}")
        if source_id:
            extracted["source_id"] = source_id

        # Step 4: Store (only if recommendation is not "skip")
        recommendation = scores.get("recommendation", "maybe")
        if recommendation != "skip":
            try:
                db.insert_opportunity(extracted)
                logger.info(f"Stored opportunity: {extracted['title']} (relevance: {extracted['relevance_score']:.2f})")
            except Exception as e:
                logger.error(f"Failed to store opportunity: {e}")

        results.append(ProcessedOpportunity(
            is_opportunity=True,
            confidence=confidence,
            extracted=extracted,
            relevance_score=scores.get("relevance_score"),
            prestige_score=scores.get("prestige_score"),
            recommendation=recommendation
        ))

    return results
