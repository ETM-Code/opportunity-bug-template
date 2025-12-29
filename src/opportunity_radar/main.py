"""Main entry point for Opportunity Radar."""

import argparse
import logging
import sys
from datetime import datetime

from .config import get_config, load_sources
from .db import get_db, Database
from .sources import PageSource, EmailSource
from .llm.pipeline import process_content
from .llm.batch import BatchPipeline
from .digest import generate_digest, generate_weekly_roundup, send_digest
from .digest.sender import send_test_email
from .pipeline_async import run_parallel_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def init_sources():
    """Initialize sources in the database from YAML config."""
    sources_config = load_sources()
    db = get_db()

    count = 0

    # Page sources
    for page in sources_config.get("page_sources", []):
        source = {
            "name": page["name"],
            "type": "page",
            "priority": page.get("priority", "medium"),
            "tags": page.get("tags", []),
            "config": {
                "url": page["url"],
                "check_frequency_hours": page.get("check_frequency_hours", 24),
                "notes": page.get("notes"),
                "secondary_urls": page.get("secondary_urls", []),
                "fallback_url": page.get("fallback_url"),
                # Browser automation config
                "use_browser": page.get("use_browser", False),
                "wait_for": page.get("wait_for"),
                "link_pattern": page.get("link_pattern"),
                "max_links": page.get("max_links", 10),
            },
            "active": True,
        }
        db.upsert_source(source)
        count += 1

    # Email sources
    for email_src in sources_config.get("email_sources", []):
        source = {
            "name": email_src["name"],
            "type": "email",
            "priority": email_src.get("priority", "medium"),
            "tags": email_src.get("tags", []),
            "config": {
                "sender_patterns": email_src.get("sender_patterns", []),
                "gmail_label": email_src.get("gmail_label"),
                "notes": email_src.get("notes"),
            },
            "active": True,
        }
        db.upsert_source(source)
        count += 1

    logger.info(f"Initialized {count} sources")


def run_page_sources():
    """Check all page sources for new opportunities."""
    db = get_db()
    page_source = PageSource()

    sources = db.get_active_sources(source_type="page")
    logger.info(f"Checking {len(sources)} page sources")

    for source in sources:
        try:
            config = source.get("config", {})
            url = config.get("url")
            if not url:
                continue

            logger.info(f"Checking: {source['name']} ({url})")

            # Check if this source needs browser automation
            use_browser = config.get("use_browser", False)

            if use_browser:
                # Use stealthy browser for JS-rendered pages
                wait_for = config.get("wait_for")
                link_pattern = config.get("link_pattern")
                max_links = config.get("max_links", 10)

                logger.info(f"Using browser for {source['name']} (link_pattern: {link_pattern})")

                contents = page_source.fetch_if_changed_with_browser(
                    url=url,
                    source_id=source["id"],
                    wait_for=wait_for,
                    link_pattern=link_pattern,
                    max_links=max_links
                )

                if not contents:
                    logger.debug(f"No changes for {source['name']}")
                    db.update_source_checked(source["id"])
                    continue

                logger.info(f"Fetched {len(contents)} pages for {source['name']}")

                # Process each page (main + followed links)
                for content in contents:
                    logger.info(f"Processing: {content.url}")
                    results = process_content(
                        content=content.text,
                        source_url=content.url,
                        source_id=source["id"]
                    )
                    for result in results:
                        if result.is_opportunity and result.extracted:
                            logger.info(f"Found opportunity: {result.extracted.get('title')}")
            else:
                # Use simple HTTP fetch for server-rendered pages
                content = page_source.fetch_if_changed(url, source_id=source["id"])
                if not content:
                    logger.debug(f"No changes for {source['name']}")
                    db.update_source_checked(source["id"])
                    continue

                # Process the main page content
                logger.info(f"New content found for {source['name']}, processing...")
                results = process_content(
                    content=content.text,
                    source_url=url,
                    source_id=source["id"]
                )

                for result in results:
                    if result.is_opportunity and result.extracted:
                        logger.info(f"Found opportunity: {result.extracted.get('title')}")

                # Also check for individual job listings on careers pages
                listings = page_source.extract_job_listings(content)
                for listing in listings[:10]:  # Limit to 10 per page
                    # Fetch and process each listing
                    listing_content = page_source.fetch(listing["url"])
                    if listing_content:
                        process_content(
                            content=listing_content.text,
                            source_url=listing["url"],
                            source_id=source["id"]
                        )

            db.update_source_checked(source["id"])

        except Exception as e:
            logger.error(f"Error processing {source['name']}: {e}")
            db.update_source_checked(source["id"], error=str(e))


def run_email_sources():
    """Check email sources for new opportunities."""
    db = get_db()

    sources = db.get_active_sources(source_type="email")
    logger.info(f"Checking {len(sources)} email sources")

    with EmailSource() as email_source:
        for source in sources:
            try:
                config = source.get("config", {})
                sender_patterns = config.get("sender_patterns", [])

                logger.info(f"Checking emails for: {source['name']}")

                for email_msg in email_source.fetch_new_emails(
                    source_id=source["id"],
                    sender_patterns=sender_patterns,
                    since_days=7
                ):
                    logger.info(f"Processing email: {email_msg.subject}")

                    # Convert email to clean markdown for LLM processing
                    content = email_msg.to_markdown()

                    # Get filtered job links for reference
                    job_links = email_msg.get_job_links()
                    logger.info(f"Found {len(job_links)} job links in email")

                    # Process the email content - LLM will extract all opportunities
                    results = process_content(
                        content=content,
                        source_url=f"email:{email_msg.msg_id}",
                        source_id=source["id"]
                    )

                    opp_count = 0
                    for result in results:
                        if result.is_opportunity and result.extracted:
                            opp_count += 1
                            title = result.extracted.get('title', 'Unknown')
                            score = result.relevance_score or 0
                            logger.info(f"  [{score}%] {title}")

                    logger.info(f"Extracted {opp_count} opportunities from email")

                db.update_source_checked(source["id"])

            except Exception as e:
                logger.error(f"Error processing {source['name']}: {e}")
                db.update_source_checked(source["id"], error=str(e))


def run_digest():
    """Generate and send the digest email."""
    digest = generate_digest(max_items=10)

    if not digest:
        logger.info("No opportunities to send in digest")
        return

    logger.info(f"Sending digest with {digest['count']} opportunities")

    success = send_digest(
        subject=digest["subject"],
        html=digest["html"],
        text=digest["text"],
        opportunity_ids=digest["opportunity_ids"]
    )

    if success:
        logger.info("Digest sent successfully!")
    else:
        logger.error("Failed to send digest")


def run_weekly_roundup():
    """Generate and send the weekly roundup email."""
    logger.info("Generating weekly roundup...")

    roundup = generate_weekly_roundup(max_items=15, min_relevance=0.5)

    if not roundup:
        logger.info("No opportunities for weekly roundup")
        return

    logger.info(f"Sending weekly roundup with {roundup['count']} opportunities")

    success = send_digest(
        subject=roundup["subject"],
        html=roundup["html"],
        text=roundup["text"],
        opportunity_ids=[]  # Don't mark as notified - this is a summary
    )

    if success:
        logger.info("Weekly roundup sent successfully!")
    else:
        logger.error("Failed to send weekly roundup")


def run_full_pipeline(use_batch: bool = False, use_async: bool = True):
    """Run the complete pipeline: sources -> LLM -> digest."""
    logger.info("=" * 50)
    mode = "batch" if use_batch else ("async" if use_async else "sync")
    logger.info(f"Starting Opportunity Radar pipeline ({mode} mode)")
    logger.info("=" * 50)

    if use_batch:
        # Collect all content and submit as batch
        run_batch_collect()
    elif use_async:
        # Use async parallel pipeline (faster)
        result = run_parallel_pipeline()
        logger.info(f"Async pipeline found {result['opportunities_found']} opportunities")

        # Check email sources (still sync for now)
        run_email_sources()

        # Generate and send digest
        run_digest()
    else:
        # Sync mode (old behavior)
        run_page_sources()
        run_email_sources()
        run_digest()

    logger.info("Pipeline complete!")


def run_batch_collect():
    """Collect content from all sources and submit as a batch job."""
    db = get_db()
    batch = BatchPipeline()
    page_source = PageSource()

    # Collect from page sources
    sources = db.get_active_sources(source_type="page")
    logger.info(f"Collecting from {len(sources)} page sources")

    for source in sources:
        try:
            config = source.get("config", {})
            url = config.get("url")
            if not url:
                continue

            content = page_source.fetch_if_changed(url, source_id=source["id"])
            if content:
                logger.info(f"Adding to batch: {source['name']}")
                batch.add_classify_request(
                    content=content.text,
                    source_url=url,
                    source_id=source["id"]
                )
            db.update_source_checked(source["id"])
        except Exception as e:
            logger.error(f"Error collecting from {source['name']}: {e}")

    # Collect from email sources
    with EmailSource() as email_source:
        sources = db.get_active_sources(source_type="email")
        logger.info(f"Collecting from {len(sources)} email sources")

        for source in sources:
            try:
                config = source.get("config", {})
                sender_patterns = config.get("sender_patterns", [])

                for email_msg in email_source.fetch_new_emails(
                    source_id=source["id"],
                    sender_patterns=sender_patterns,
                    since_days=7
                ):
                    logger.info(f"Adding email to batch: {email_msg.subject}")
                    content = email_msg.to_markdown()
                    batch.add_classify_request(
                        content=content,
                        source_url=f"email:{email_msg.msg_id}",
                        source_id=source["id"]
                    )

                db.update_source_checked(source["id"])
            except Exception as e:
                logger.error(f"Error collecting from {source['name']}: {e}")

    # Submit batch if we have requests
    if batch.pending_count() > 0:
        logger.info(f"Submitting batch with {batch.pending_count()} requests (50% cheaper!)")
        job = batch.submit_batch()
        logger.info(f"Batch submitted: {job.batch_id}")
        logger.info("Run 'batch status' to check progress, 'batch process' when complete")
    else:
        logger.info("No new content to process")


def run_batch_status():
    """Check status of pending batch jobs."""
    db = get_db()
    batch = BatchPipeline()

    pending = db.get_pending_batches()
    if not pending:
        logger.info("No pending batch jobs")
        return

    for job in pending:
        batch_id = job["batch_id"]
        status = batch.check_batch(batch_id)
        logger.info(f"Batch {batch_id}: {status['status']}")
        if status.get("request_counts"):
            counts = status["request_counts"]
            logger.info(f"  Completed: {counts.get('completed', 0)}/{counts.get('total', 0)}")


def run_batch_process():
    """Process completed batch jobs."""
    db = get_db()
    batch = BatchPipeline()

    pending = db.get_pending_batches()
    for job in pending:
        batch_id = job["batch_id"]
        status = batch.check_batch(batch_id)

        if status["status"] != "completed":
            logger.info(f"Batch {batch_id} not ready: {status['status']}")
            continue

        logger.info(f"Processing completed batch: {batch_id}")

        # Get the original requests
        requests_json = job.get("requests_json", "[]")
        import json
        requests = {r["custom_id"]: r for r in json.loads(requests_json)}

        # Process results
        for custom_id, response in batch.get_batch_results(batch_id):
            req = requests.get(custom_id, {})
            if not req:
                continue

            req_type = req.get("request_type")
            source_id = req.get("source_id")

            if req_type == "classify":
                # Parse classification result
                try:
                    result = json.loads(response)
                    if result.get("contains_opportunity") and result.get("confidence", 0) >= 0.5:
                        # Add extraction request for next batch
                        logger.info(f"Classified as opportunity, will extract: {custom_id}")
                        # For now, process synchronously (could batch extractions too)
                        content = req.get("metadata", {}).get("content_preview", "")
                        process_content(content, req.get("source_url"), source_id)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse classify response: {custom_id}")

        db.update_batch_status(batch_id, "processed")
        logger.info(f"Batch {batch_id} processed")

    # Generate digest if we processed anything
    run_digest()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Opportunity Radar - Find your next opportunity")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Init command
    subparsers.add_parser("init", help="Initialize sources in database")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run the pipeline")
    run_parser.add_argument("--pages-only", action="store_true", help="Only check page sources")
    run_parser.add_argument("--emails-only", action="store_true", help="Only check email sources")
    run_parser.add_argument("--digest-only", action="store_true", help="Only generate and send digest")
    run_parser.add_argument("--weekly", action="store_true", help="Send weekly roundup (best opportunities from past 7 days)")
    run_parser.add_argument("--sync", action="store_true", help="Use sync API (sequential, slower)")
    run_parser.add_argument("--batch", action="store_true", help="Use Batch API (async, 50% cheaper)")

    # Batch command
    batch_parser = subparsers.add_parser("batch", help="Manage batch jobs")
    batch_parser.add_argument("action", choices=["submit", "status", "process"],
                              help="submit=create batch, status=check pending, process=get results")

    # Test command
    test_parser = subparsers.add_parser("test", help="Test components")
    test_parser.add_argument("component", choices=["email", "page", "browser", "digest", "db"],
                            help="Component to test")
    test_parser.add_argument("--url", help="URL to test (for browser test)")

    args = parser.parse_args()

    if args.command == "init":
        init_sources()

    elif args.command == "run":
        if args.pages_only:
            run_page_sources()
        elif args.emails_only:
            run_email_sources()
        elif args.digest_only:
            run_digest()
        elif args.weekly:
            run_weekly_roundup()
        elif args.batch:
            run_full_pipeline(use_batch=True)
        elif args.sync:
            run_full_pipeline(use_batch=False, use_async=False)
        else:
            # Default to async parallel mode
            run_full_pipeline(use_batch=False, use_async=True)

    elif args.command == "batch":
        if args.action == "submit":
            run_batch_collect()
        elif args.action == "status":
            run_batch_status()
        elif args.action == "process":
            run_batch_process()

    elif args.command == "test":
        if args.component == "email":
            print("Testing email delivery...")
            success = send_test_email()
            print("SUCCESS" if success else "FAILED")

        elif args.component == "page":
            print("Testing page fetch...")
            page = PageSource()
            content = page.fetch("https://openai.com/careers/")
            if content:
                print(f"Fetched: {content.title}")
                print(f"Content length: {len(content.text)} chars")
                print(f"Links found: {len(content.links)}")
            else:
                print("FAILED to fetch page")

        elif args.component == "browser":
            print("Testing browser fetch...")
            test_url = args.url or "https://jobs.80000hours.org/"
            page = PageSource()
            contents = page.fetch_with_browser(
                url=test_url,
                wait_for="[class*='job'], .ais-Hits",
                link_pattern="/jobs/" if "80000hours" in test_url else None,
                max_links=5
            )
            if contents:
                print(f"Fetched {len(contents)} pages")
                for i, content in enumerate(contents):
                    print(f"\n[{i+1}] {content.title}")
                    print(f"    URL: {content.url}")
                    print(f"    Content: {len(content.text)} chars")
                    print(f"    Preview: {content.text[:200]}...")
            else:
                print("FAILED to fetch with browser")

        elif args.component == "digest":
            print("Testing digest generation...")
            digest = generate_digest()
            if digest:
                print(f"Subject: {digest['subject']}")
                print(f"Opportunities: {digest['count']}")
                print("\nText preview:")
                print(digest["text"][:500])
            else:
                print("No opportunities to include in digest")

        elif args.component == "db":
            print("Testing database connection...")
            db = get_db()
            profile = db.get_user_profile()
            if profile:
                print(f"Connected! User: {profile.get('name')}")
            else:
                print("Connected but no user profile found")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
