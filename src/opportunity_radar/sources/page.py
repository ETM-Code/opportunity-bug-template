"""Web page source connector."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Iterator
import httpx
from bs4 import BeautifulSoup
import html2text

from ..db import get_db
from .browser import StealthBrowser, BrowserContent

logger = logging.getLogger(__name__)


@dataclass
class PageContent:
    """Extracted content from a web page."""
    url: str
    title: str
    text: str
    html: str
    links: list[dict]  # [{url, text}]


class PageSource:
    """Fetches and extracts content from web pages."""

    def __init__(self):
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.body_width = 0  # No wrapping

    def fetch(self, url: str) -> PageContent | None:
        """Fetch and parse a web page."""
        try:
            response = self.client.get(url)
            response.raise_for_status()
            html = response.text

            soup = BeautifulSoup(html, "html.parser")

            # Remove script and style elements
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            # Get title
            title = ""
            if soup.title:
                title = soup.title.string or ""

            # Convert to markdown-like text
            text = self.html_converter.handle(str(soup))

            # Extract links (for job/opportunity pages)
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                link_text = a.get_text(strip=True)
                if href.startswith("/"):
                    # Make relative URLs absolute
                    from urllib.parse import urljoin
                    href = urljoin(url, href)
                if href.startswith("http") and link_text:
                    links.append({"url": href, "text": link_text})

            return PageContent(
                url=url,
                title=title.strip(),
                text=text.strip(),
                html=html,
                links=links
            )

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def fetch_if_changed(self, url: str, source_id: str | None = None) -> PageContent | None:
        """Fetch page only if content has changed since last check."""
        content = self.fetch(url)
        if not content:
            return None

        db = get_db()
        content_hash = db.hash_content(content.text)

        if db.is_seen(content_hash):
            logger.debug(f"Content unchanged for {url}")
            return None

        # Mark as seen
        db.mark_seen(content_hash, source_id=source_id, url=url)
        return content

    def extract_job_listings(self, content: PageContent) -> list[dict]:
        """Extract individual job/opportunity listings from a careers page.

        Returns list of dicts with 'title', 'url', 'snippet'.
        """
        listings = []

        # Common patterns for job listings
        soup = BeautifulSoup(content.html, "html.parser")

        # Look for common job listing containers
        job_selectors = [
            "a[href*='job']",
            "a[href*='career']",
            "a[href*='position']",
            "a[href*='role']",
            "a[href*='apply']",
            ".job-listing a",
            ".career-listing a",
            "[class*='job'] a",
            "[class*='position'] a",
        ]

        seen_urls = set()
        for selector in job_selectors:
            for element in soup.select(selector):
                href = element.get("href", "")
                if not href or href in seen_urls:
                    continue

                # Make absolute URL
                if href.startswith("/"):
                    from urllib.parse import urljoin
                    href = urljoin(content.url, href)

                if not href.startswith("http"):
                    continue

                title = element.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                # Get surrounding context
                parent = element.parent
                snippet = parent.get_text(strip=True)[:200] if parent else title

                seen_urls.add(href)
                listings.append({
                    "title": title,
                    "url": href,
                    "snippet": snippet
                })

        return listings

    def fetch_with_browser(
        self,
        url: str,
        wait_for: str | None = None,
        link_pattern: str | None = None,
        max_links: int = 10
    ) -> list[PageContent]:
        """Fetch a page using stealthy browser automation (for JS-rendered pages).

        Args:
            url: URL to fetch
            wait_for: CSS selector to wait for before extracting content
            link_pattern: Regex pattern to filter links to follow (e.g., r'/jobs/\\d+')
            max_links: Maximum number of links to follow

        Returns list of PageContent (main page + any followed links).
        """
        async def _fetch():
            results = []
            async with StealthBrowser() as browser:
                if link_pattern:
                    # Fetch main page and follow matching links
                    async for content in browser.fetch_with_links(
                        url,
                        link_pattern=link_pattern,
                        max_links=max_links,
                        wait_for=wait_for
                    ):
                        results.append(self._browser_to_page_content(content))
                else:
                    # Just fetch the main page
                    content = await browser.fetch(url, wait_for=wait_for)
                    if content:
                        results.append(self._browser_to_page_content(content))
            return results

        return asyncio.run(_fetch())

    def _browser_to_page_content(self, browser_content: BrowserContent) -> PageContent:
        """Convert BrowserContent to PageContent."""
        return PageContent(
            url=browser_content.url,
            title=browser_content.title,
            text=browser_content.text,
            html=browser_content.html,
            links=browser_content.links
        )

    def fetch_if_changed_with_browser(
        self,
        url: str,
        source_id: str | None = None,
        wait_for: str | None = None,
        link_pattern: str | None = None,
        max_links: int = 10
    ) -> list[PageContent]:
        """Fetch with browser only if main page content has changed.

        Returns list of PageContent (main page + followed links) if changed,
        empty list if unchanged.
        """
        # First fetch just the main page to check if it changed
        async def _fetch_main():
            async with StealthBrowser() as browser:
                return await browser.fetch(url, wait_for=wait_for)

        main_content = asyncio.run(_fetch_main())
        if not main_content:
            return []

        db = get_db()
        content_hash = db.hash_content(main_content.text)

        if db.is_seen(content_hash):
            logger.debug(f"Content unchanged for {url}")
            return []

        # Mark as seen
        db.mark_seen(content_hash, source_id=source_id, url=url)

        # Now fetch with link following if configured
        if link_pattern:
            return self.fetch_with_browser(
                url,
                wait_for=wait_for,
                link_pattern=link_pattern,
                max_links=max_links
            )
        else:
            return [self._browser_to_page_content(main_content)]
