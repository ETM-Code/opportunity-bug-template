"""Async parallel pipeline for opportunity processing.

Architecture:
- Fetch pages concurrently (limited concurrency for browser)
- Queue content for LLM processing as it arrives
- Run LLM calls concurrently (separate pool)
- Don't block fetching on LLM responses
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from concurrent.futures import ThreadPoolExecutor

from openai import AsyncOpenAI

from .config import get_config, load_sources
from .db import get_db
from .sources.page import PageSource
from .sources.browser import StealthBrowser, BrowserContent
from .llm.prompts import CLASSIFY_PROMPT, EXTRACT_PROMPT, SCORE_PROMPT
import json

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result from fetching a source."""
    source_id: str
    source_name: str
    url: str
    content: str
    success: bool
    error: str | None = None


@dataclass
class ProcessResult:
    """Result from processing content through LLM."""
    source_id: str
    url: str
    opportunities: list[dict]
    error: str | None = None


class AsyncPipeline:
    """Parallel async pipeline for opportunity processing."""

    # Concurrency limits
    MAX_BROWSER_CONCURRENT = 2   # Browser is memory-heavy
    MAX_HTTP_CONCURRENT = 10     # Plain HTTP is cheap
    MAX_LLM_CONCURRENT = 5       # Balance cost vs speed

    # Models
    FAST_MODEL = "gpt-5-nano"    # For classification and extraction
    SMART_MODEL = "gpt-5-mini"   # For scoring with better reasoning

    def __init__(self):
        config = get_config()
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.db = get_db()
        self._user_profile: dict | None = None

        # Semaphores for concurrency control
        self._browser_sem = asyncio.Semaphore(self.MAX_BROWSER_CONCURRENT)
        self._http_sem = asyncio.Semaphore(self.MAX_HTTP_CONCURRENT)
        self._llm_sem = asyncio.Semaphore(self.MAX_LLM_CONCURRENT)

        # Thread pool for sync operations
        self._executor = ThreadPoolExecutor(max_workers=4)

    def _get_user_profile(self) -> dict:
        """Get cached user profile."""
        if self._user_profile is None:
            self._user_profile = self.db.get_user_profile()
            if not self._user_profile:
                sources = load_sources()
                self._user_profile = sources.get("user_profile", {})
        return self._user_profile

    async def _call_llm(self, prompt: str, model: str = None, reasoning_effort: str = "low") -> str | None:
        """Make an async LLM call with rate limiting."""
        model = model or self.FAST_MODEL

        async with self._llm_sem:
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_completion_tokens=4000,
                    reasoning_effort=reasoning_effort,
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                return None

    def _parse_json(self, text: str | None) -> dict | list | None:
        """Parse JSON from LLM response."""
        if not text:
            return None
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def fetch_source_http(self, source: dict) -> FetchResult:
        """Fetch a source using plain HTTP."""
        config = source.get("config", {})
        url = config.get("url", "")

        async with self._http_sem:
            try:
                # Run sync fetch in thread pool
                page_source = PageSource()
                loop = asyncio.get_running_loop()
                content = await loop.run_in_executor(
                    self._executor,
                    lambda: page_source.fetch_if_changed(url, source_id=source["id"])
                )

                if not content:
                    return FetchResult(
                        source_id=source["id"],
                        source_name=source["name"],
                        url=url,
                        content="",
                        success=False,
                        error="No changes or fetch failed"
                    )

                return FetchResult(
                    source_id=source["id"],
                    source_name=source["name"],
                    url=url,
                    content=content.text,
                    success=True
                )
            except Exception as e:
                logger.error(f"HTTP fetch failed for {source['name']}: {e}")
                return FetchResult(
                    source_id=source["id"],
                    source_name=source["name"],
                    url=url,
                    content="",
                    success=False,
                    error=str(e)
                )

    async def fetch_source_browser(self, source: dict) -> list[FetchResult]:
        """Fetch a source using browser (for JS-rendered pages)."""
        config = source.get("config", {})
        url = config.get("url", "")
        wait_for = config.get("wait_for")
        link_pattern = config.get("link_pattern")
        max_links = config.get("max_links", 10)

        async with self._browser_sem:
            try:
                async with StealthBrowser() as browser:
                    results = []

                    if link_pattern:
                        # Fetch main page and follow links
                        async for content in browser.fetch_with_links(
                            url=url,
                            link_pattern=link_pattern,
                            max_links=max_links,
                            wait_for=wait_for
                        ):
                            results.append(FetchResult(
                                source_id=source["id"],
                                source_name=source["name"],
                                url=content.url,
                                content=content.text,
                                success=True
                            ))
                    else:
                        # Just fetch the main page
                        content = await browser.fetch(url, wait_for=wait_for)
                        if content:
                            results.append(FetchResult(
                                source_id=source["id"],
                                source_name=source["name"],
                                url=content.url,
                                content=content.text,
                                success=True
                            ))

                    if not results:
                        results.append(FetchResult(
                            source_id=source["id"],
                            source_name=source["name"],
                            url=url,
                            content="",
                            success=False,
                            error="Browser fetch returned no content"
                        ))

                    return results

            except Exception as e:
                logger.error(f"Browser fetch failed for {source['name']}: {e}")
                return [FetchResult(
                    source_id=source["id"],
                    source_name=source["name"],
                    url=url,
                    content="",
                    success=False,
                    error=str(e)
                )]

    async def process_content(self, fetch_result: FetchResult) -> ProcessResult:
        """Process fetched content through LLM pipeline."""
        if not fetch_result.success or not fetch_result.content:
            return ProcessResult(
                source_id=fetch_result.source_id,
                url=fetch_result.url,
                opportunities=[],
                error=fetch_result.error
            )

        content = fetch_result.content
        if len(content) > 15000:
            content = content[:15000] + "\n...[truncated]..."

        # Step 1: Classify (is this an opportunity?)
        classify_prompt = CLASSIFY_PROMPT.format(content=content)
        classify_response = await self._call_llm(classify_prompt)
        classify_result = self._parse_json(classify_response)

        if not classify_result:
            return ProcessResult(
                source_id=fetch_result.source_id,
                url=fetch_result.url,
                opportunities=[],
                error="Classification failed"
            )

        if not classify_result.get("contains_opportunity") or classify_result.get("confidence", 0) < 0.5:
            return ProcessResult(
                source_id=fetch_result.source_id,
                url=fetch_result.url,
                opportunities=[]
            )

        # Step 2: Extract opportunities
        extract_prompt = EXTRACT_PROMPT.format(content=content)
        extract_response = await self._call_llm(extract_prompt)
        extracted = self._parse_json(extract_response)

        if not extracted:
            return ProcessResult(
                source_id=fetch_result.source_id,
                url=fetch_result.url,
                opportunities=[],
                error="Extraction failed"
            )

        # Normalize to list
        if isinstance(extracted, dict):
            if "opportunities" in extracted:
                opportunities = extracted["opportunities"]
            elif "title" in extracted:
                opportunities = [extracted]
            else:
                opportunities = []
        else:
            opportunities = extracted

        # Step 3: Score each opportunity concurrently
        scored_opportunities = []
        score_tasks = []
        scored_candidates = []  # Track which opportunities were actually queued for scoring

        for opp in opportunities:
            if not opp.get("title"):
                continue

            # Check for duplicates
            if opp.get("url") and self.db.opportunity_url_exists(opp["url"]):
                continue
            if opp.get("title") and opp.get("organization"):
                if self.db.opportunity_title_exists(opp["title"], opp["organization"]):
                    continue

            scored_candidates.append(opp)  # Track the actual candidate
            score_tasks.append(self._score_opportunity(opp))

        if score_tasks:
            scored_results = await asyncio.gather(*score_tasks, return_exceptions=True)

            for opp, score_result in zip(scored_candidates, scored_results):  # Use scored_candidates!
                if isinstance(score_result, Exception):
                    logger.error(f"Scoring failed: {score_result}")
                    continue

                if score_result and score_result.get("recommendation") != "skip":
                    opp.update(score_result)
                    opp["source_id"] = fetch_result.source_id
                    opp["raw_content"] = fetch_result.content[:5000]

                    # Store in database
                    try:
                        self.db.insert_opportunity(opp)
                        logger.info(f"Stored: {opp['title']} (relevance: {opp.get('relevance_score', 0):.2f})")
                        scored_opportunities.append(opp)
                    except Exception as e:
                        logger.error(f"Failed to store opportunity: {e}")

        return ProcessResult(
            source_id=fetch_result.source_id,
            url=fetch_result.url,
            opportunities=scored_opportunities
        )

    async def _score_opportunity(self, opportunity: dict) -> dict | None:
        """Score a single opportunity."""
        profile = self._get_user_profile()

        profile_text = f"""
Name: {profile.get('name', 'Unknown')}
Background: {profile.get('background', 'Not specified')}
Interests: {', '.join(profile.get('interests', []))}
Constraints: {json.dumps(profile.get('constraints', {}))}
"""

        high_signals = profile.get("high_value_signals", [])
        low_signals = profile.get("low_value_signals", [])

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

        response = await self._call_llm(prompt, model=self.SMART_MODEL)
        return self._parse_json(response)

    async def run_parallel(self, sources: list[dict]) -> dict:
        """Run the pipeline on all sources in parallel.

        Architecture:
        1. Start all fetches concurrently (with semaphores for rate limiting)
        2. As each fetch completes, immediately queue LLM processing
        3. LLM processing runs concurrently with remaining fetches
        4. Return summary when all complete
        """
        logger.info(f"Starting parallel pipeline for {len(sources)} sources")

        # Separate browser vs HTTP sources
        browser_sources = [s for s in sources if s.get("config", {}).get("use_browser")]
        http_sources = [s for s in sources if not s.get("config", {}).get("use_browser")]

        logger.info(f"  Browser sources: {len(browser_sources)}")
        logger.info(f"  HTTP sources: {len(http_sources)}")

        # Create fetch tasks
        fetch_tasks = []

        for source in http_sources:
            fetch_tasks.append(self.fetch_source_http(source))

        for source in browser_sources:
            fetch_tasks.append(self.fetch_source_browser(source))

        # Process results as they come in
        all_opportunities = []
        errors = []

        async def process_fetch_result(fetch_coro):
            """Fetch and immediately process."""
            result = await fetch_coro

            # Handle browser returning list of results
            if isinstance(result, list):
                process_tasks = [self.process_content(r) for r in result]
                process_results = await asyncio.gather(*process_tasks, return_exceptions=True)

                for pr in process_results:
                    if isinstance(pr, Exception):
                        errors.append(str(pr))
                    elif pr.opportunities:
                        all_opportunities.extend(pr.opportunities)
                    elif pr.error:
                        errors.append(pr.error)
            else:
                process_result = await self.process_content(result)
                if process_result.opportunities:
                    all_opportunities.extend(process_result.opportunities)
                elif process_result.error:
                    errors.append(process_result.error)

        # Run all fetch+process pipelines concurrently
        await asyncio.gather(
            *[process_fetch_result(task) for task in fetch_tasks],
            return_exceptions=True
        )

        # Update source check times
        for source in sources:
            try:
                self.db.update_source_checked(source["id"])
            except Exception:
                pass

        return {
            "total_sources": len(sources),
            "opportunities_found": len(all_opportunities),
            "errors": len(errors),
            "opportunities": all_opportunities
        }


async def run_async_pipeline():
    """Main entry point for async pipeline."""
    pipeline = AsyncPipeline()
    db = get_db()

    sources = db.get_active_sources(source_type="page")
    result = await pipeline.run_parallel(sources)

    logger.info("=" * 50)
    logger.info(f"Pipeline complete!")
    logger.info(f"  Sources checked: {result['total_sources']}")
    logger.info(f"  Opportunities found: {result['opportunities_found']}")
    logger.info(f"  Errors: {result['errors']}")
    logger.info("=" * 50)

    return result


def run_parallel_pipeline():
    """Sync wrapper for async pipeline."""
    return asyncio.run(run_async_pipeline())
