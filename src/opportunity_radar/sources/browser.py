"""Stealthy browser automation for JS-rendered pages."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, Browser
from playwright_stealth.stealth import Stealth

logger = logging.getLogger(__name__)

# Initialize stealth instance
_stealth = Stealth()

# FlareSolverr configuration
import os
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191/v1")
FLARESOLVERR_TIMEOUT = 60000  # 60 seconds

# Cloudflare detection patterns
CLOUDFLARE_PATTERNS = [
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "Please Wait... | Cloudflare",
    "cf-browser-verification",
    "cf_clearance",
    "_cf_chl_opt",
]


@dataclass
class BrowserContent:
    """Content fetched via browser."""
    url: str
    title: str
    text: str
    html: str
    links: list[dict]  # [{"url": "", "text": ""}]


class FlareSolverr:
    """Client for FlareSolverr Cloudflare bypass proxy."""

    def __init__(self, base_url: str = FLARESOLVERR_URL):
        self.base_url = base_url

    async def fetch(self, url: str, max_timeout: int = FLARESOLVERR_TIMEOUT) -> BrowserContent | None:
        """Fetch a URL through FlareSolverr.

        Returns BrowserContent or None if failed.
        """
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": max_timeout,
        }

        try:
            async with httpx.AsyncClient(timeout=max_timeout / 1000 + 30) as client:
                logger.info(f"FlareSolverr: Fetching {url}")
                response = await client.post(self.base_url, json=payload)
                data = response.json()

                if data.get("status") != "ok":
                    logger.error(f"FlareSolverr failed: {data.get('message')}")
                    return None

                solution = data.get("solution", {})
                html = solution.get("response", "")

                # Parse HTML to extract text and links
                soup = BeautifulSoup(html, "html.parser")

                # Get title
                title_tag = soup.find("title")
                title = title_tag.get_text().strip() if title_tag else ""

                # Get body text
                body = soup.find("body")
                text = body.get_text(separator="\n", strip=True) if body else ""

                # Extract links
                links = []
                for a in soup.find_all("a", href=True):
                    href = a.get("href", "")
                    link_text = a.get_text().strip()

                    if not href or href.startswith("#") or href.startswith("javascript:"):
                        continue

                    full_url = urljoin(url, href)
                    links.append({
                        "url": full_url,
                        "text": link_text[:200] if link_text else ""
                    })

                logger.info(f"FlareSolverr: Got {len(text)} chars, {len(links)} links from {url}")

                return BrowserContent(
                    url=solution.get("url", url),
                    title=title,
                    text=text,
                    html=html,
                    links=links,
                )

        except httpx.ConnectError:
            logger.warning("FlareSolverr not running - skipping Cloudflare bypass")
            return None
        except Exception as e:
            logger.error(f"FlareSolverr error: {e}")
            return None

    @staticmethod
    def is_available() -> bool:
        """Check if FlareSolverr is running."""
        try:
            import httpx
            response = httpx.get(FLARESOLVERR_URL.replace("/v1", ""), timeout=2)
            return response.status_code == 200
        except Exception:
            return False


def _is_cloudflare_blocked(content: BrowserContent | None) -> bool:
    """Check if content indicates Cloudflare blocking."""
    if not content:
        return False

    # Check title and text for Cloudflare patterns
    check_text = (content.title + " " + content.text[:2000]).lower()
    for pattern in CLOUDFLARE_PATTERNS:
        if pattern.lower() in check_text:
            return True

    # Very short content with Cloudflare-like title is suspicious
    if len(content.text) < 500 and "moment" in content.title.lower():
        return True

    return False


class StealthBrowser:
    """Stealthy browser for JS-rendered pages."""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    async def start(self):
        """Start the browser."""
        if self._browser:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
            ]
        )
        logger.info("Stealthy browser started")

    async def stop(self):
        """Stop the browser."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _create_stealth_page(self) -> Page:
        """Create a new stealth page."""
        context = await self._browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='en-US',
            timezone_id='America/New_York',
        )
        page = await context.new_page()
        await _stealth.apply_stealth_async(page)
        return page

    async def fetch(self, url: str, wait_for: str | None = None, timeout: int = 60000) -> BrowserContent | None:
        """Fetch a page with full JS rendering.

        Args:
            url: URL to fetch
            wait_for: Optional CSS selector to wait for
            timeout: Timeout in milliseconds

        Uses FlareSolverr as fallback if Cloudflare protection is detected.
        """
        page = await self._create_stealth_page()
        content = None
        try:
            logger.debug(f"Fetching: {url}")
            # Use 'domcontentloaded' instead of 'networkidle' - faster and works better
            # on heavy sites with analytics that never stop making requests
            await page.goto(url, wait_until='domcontentloaded', timeout=timeout)

            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=10000)
                except Exception:
                    logger.debug(f"Selector not found: {wait_for}")

            # Wait for JS to render content after DOM loads
            await asyncio.sleep(2)

            title = await page.title()
            html = await page.content()
            text = await page.inner_text('body')

            # Extract links
            links = await self._extract_links(page, url)

            content = BrowserContent(
                url=url,
                title=title,
                text=text,
                html=html,
                links=links
            )

            # Check for Cloudflare blocking
            if _is_cloudflare_blocked(content):
                logger.warning(f"Cloudflare detected on {url}, trying FlareSolverr...")
                flare_content = await FlareSolverr().fetch(url)
                if flare_content:
                    return flare_content
                logger.error(f"FlareSolverr also failed for {url}")
                return None

            return content

        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            # Try FlareSolverr as last resort on timeout/error
            logger.info(f"Trying FlareSolverr as fallback for {url}")
            flare_content = await FlareSolverr().fetch(url)
            if flare_content:
                return flare_content
            return None
        finally:
            await page.context.close()

    async def _extract_links(self, page: Page, base_url: str) -> list[dict]:
        """Extract all links from the page."""
        links = []
        elements = await page.query_selector_all('a[href]')

        for el in elements:
            try:
                href = await el.get_attribute('href')
                text = (await el.inner_text()).strip()

                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue

                # Make absolute
                full_url = urljoin(base_url, href)

                links.append({
                    'url': full_url,
                    'text': text[:200] if text else ''
                })
            except Exception:
                continue

        return links

    async def fetch_with_links(
        self,
        url: str,
        link_pattern: str | None = None,
        max_links: int = 20,
        wait_for: str | None = None,
    ) -> AsyncIterator[BrowserContent]:
        """Fetch a page and follow matching links.

        Args:
            url: Starting URL
            link_pattern: Regex pattern to filter links (e.g., r'/jobs/\d+')
            max_links: Maximum number of links to follow
            wait_for: CSS selector to wait for on each page
        """
        # Fetch main page
        main_content = await self.fetch(url, wait_for=wait_for)
        if not main_content:
            return

        yield main_content

        # Filter links if pattern provided
        links_to_follow = []
        for link in main_content.links:
            link_url = link['url']

            # Skip external links
            if urlparse(link_url).netloc != urlparse(url).netloc:
                continue

            # Apply pattern filter
            if link_pattern and not re.search(link_pattern, link_url):
                continue

            links_to_follow.append(link_url)

        # Deduplicate and limit
        links_to_follow = list(dict.fromkeys(links_to_follow))[:max_links]
        logger.info(f"Following {len(links_to_follow)} links from {url}")

        # Fetch each link
        for link_url in links_to_follow:
            content = await self.fetch(link_url, wait_for=wait_for)
            if content:
                yield content
            await asyncio.sleep(0.5)  # Be polite


# Convenience functions for sync code

def fetch_page(url: str, wait_for: str | None = None, timeout: int = 60000) -> BrowserContent | None:
    """Synchronously fetch a page with JS rendering."""
    async def _fetch():
        async with StealthBrowser() as browser:
            return await browser.fetch(url, wait_for=wait_for, timeout=timeout)
    return asyncio.run(_fetch())


def fetch_with_links(
    url: str,
    link_pattern: str | None = None,
    max_links: int = 20,
) -> list[BrowserContent]:
    """Synchronously fetch a page and follow links."""
    async def _fetch():
        results = []
        async with StealthBrowser() as browser:
            async for content in browser.fetch_with_links(url, link_pattern, max_links):
                results.append(content)
        return results
    return asyncio.run(_fetch())
