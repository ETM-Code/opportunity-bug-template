"""Supabase database client."""

import hashlib
from datetime import datetime
from typing import Any
import httpx

from .config import get_config


class Database:
    """Supabase REST API client."""

    def __init__(self):
        config = get_config()
        self.base_url = f"{config.supabase_url}/rest/v1"
        self.headers = {
            "apikey": config.supabase_key,
            "Authorization": f"Bearer {config.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self._client = httpx.Client(headers=self.headers, timeout=30.0)

    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """Make a request to the Supabase REST API."""
        url = f"{self.base_url}/{endpoint}"
        response = self._client.request(method, url, **kwargs)
        response.raise_for_status()
        if response.text:
            return response.json()
        return None

    # --- User Profile ---

    def get_user_profile(self) -> dict | None:
        """Get the user profile."""
        result = self._request("GET", "user_profile?limit=1")
        return result[0] if result else None

    # --- Sources ---

    def get_active_sources(self, source_type: str | None = None) -> list[dict]:
        """Get active sources, optionally filtered by type."""
        endpoint = "sources?active=eq.true&order=priority.asc"
        if source_type:
            endpoint += f"&type=eq.{source_type}"
        return self._request("GET", endpoint) or []

    def update_source_checked(self, source_id: str, error: str | None = None):
        """Update the last_checked_at timestamp for a source."""
        data = {"last_checked_at": datetime.utcnow().isoformat()}
        if error:
            data["last_error"] = error
        else:
            data["last_error"] = None
        self._request("PATCH", f"sources?id=eq.{source_id}", json=data)

    def upsert_source(self, source: dict) -> dict:
        """Insert or update a source."""
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"}
        url = f"{self.base_url}/sources"
        response = self._client.post(url, json=source, headers=headers)
        response.raise_for_status()
        return response.json()[0]

    # --- Seen Items (deduplication) ---

    def is_seen(self, content_hash: str) -> bool:
        """Check if content has been seen before."""
        result = self._request("GET", f"seen_items?content_hash=eq.{content_hash}&select=id")
        return len(result) > 0 if result else False

    def mark_seen(self, content_hash: str, source_id: str | None = None, url: str | None = None):
        """Mark content as seen."""
        data = {"content_hash": content_hash}
        if source_id:
            data["source_id"] = source_id
        if url:
            data["url"] = url
        self._request("POST", "seen_items", json=data)

    @staticmethod
    def hash_content(content: str) -> str:
        """Generate a hash for content deduplication."""
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    # --- Opportunities ---

    def insert_opportunity(self, opportunity: dict) -> dict:
        """Insert a new opportunity."""
        result = self._request("POST", "opportunities", json=opportunity)
        return result[0] if result else opportunity

    def get_unnotified_opportunities(self, limit: int = 10) -> list[dict]:
        """Get opportunities that haven't been included in a digest yet."""
        endpoint = (
            "opportunities?"
            "notified_at=is.null&"
            "order=relevance_score.desc.nullslast,created_at.desc&"
            f"limit={limit}"
        )
        return self._request("GET", endpoint) or []

    def get_opportunities_since(
        self,
        days: int = 7,
        min_relevance: float = 0.0,
        limit: int = 20
    ) -> list[dict]:
        """Get opportunities created in the last N days, sorted by relevance."""
        from datetime import timedelta
        since_date = (datetime.utcnow() - timedelta(days=days)).isoformat()

        endpoint = (
            f"opportunities?"
            f"created_at=gte.{since_date}&"
            f"relevance_score=gte.{min_relevance}&"
            f"order=relevance_score.desc.nullslast,created_at.desc&"
            f"limit={limit}"
        )
        return self._request("GET", endpoint) or []

    def mark_opportunities_notified(self, opportunity_ids: list[str]):
        """Mark opportunities as notified."""
        now = datetime.utcnow().isoformat()
        for opp_id in opportunity_ids:
            self._request("PATCH", f"opportunities?id=eq.{opp_id}", json={"notified_at": now})

    def opportunity_url_exists(self, url: str) -> bool:
        """Check if an opportunity with this URL already exists."""
        from urllib.parse import quote, urlparse, parse_qs, urlencode

        # Normalize URL: remove tracking parameters
        try:
            parsed = urlparse(url)
            # Remove common tracking params
            tracking_params = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
                               'source', 'ref', 'referrer', 'fbclid', 'gclid', 'e', 'uni_id', 'email'}
            params = parse_qs(parsed.query)
            clean_params = {k: v for k, v in params.items() if k.lower() not in tracking_params}
            clean_query = urlencode(clean_params, doseq=True)
            normalized = parsed._replace(query=clean_query).geturl()
        except Exception:
            normalized = url

        encoded_url = quote(normalized, safe='')
        result = self._request("GET", f"opportunities?url=eq.{encoded_url}&select=id")
        return len(result) > 0 if result else False

    def opportunity_title_exists(self, title: str, organization: str) -> bool:
        """Check if an opportunity with this title+org already exists."""
        from urllib.parse import quote
        encoded_title = quote(title, safe='')
        encoded_org = quote(organization, safe='')
        result = self._request(
            "GET",
            f"opportunities?title=eq.{encoded_title}&organization=eq.{encoded_org}&select=id"
        )
        return len(result) > 0 if result else False

    # --- Raw Emails ---

    def email_seen(self, source_id: str, gmail_msg_id: str) -> bool:
        """Check if an email has been processed."""
        result = self._request(
            "GET",
            f"raw_emails?source_id=eq.{source_id}&gmail_msg_id=eq.{gmail_msg_id}&select=id"
        )
        return len(result) > 0 if result else False

    def insert_raw_email(self, email_data: dict) -> dict:
        """Insert a raw email record."""
        result = self._request("POST", "raw_emails", json=email_data)
        return result[0] if result else email_data

    def update_email_status(self, email_id: str, status: str, error: str | None = None):
        """Update email processing status."""
        data = {"status": status, "processed_at": datetime.utcnow().isoformat()}
        if error:
            data["error_message"] = error
        self._request("PATCH", f"raw_emails?id=eq.{email_id}", json=data)

    # --- Digest Log ---

    def log_digest(self, opportunity_ids: list[str], subject: str, status: str = "sent", error: str | None = None):
        """Log a digest that was sent."""
        data = {
            "opportunity_count": len(opportunity_ids),
            "opportunity_ids": opportunity_ids,
            "email_subject": subject,
            "status": status,
        }
        if error:
            data["error_message"] = error
        self._request("POST", "digest_log", json=data)

    # --- Batch Jobs ---

    def insert_batch_job(self, batch_data: dict) -> dict:
        """Insert a batch job record."""
        result = self._request("POST", "batch_jobs", json=batch_data)
        return result[0] if result else batch_data

    def update_batch_status(self, batch_id: str, status: str, output_file_id: str | None = None):
        """Update batch job status."""
        data = {"status": status, "updated_at": datetime.utcnow().isoformat()}
        if output_file_id:
            data["output_file_id"] = output_file_id
        if status == "completed":
            data["completed_at"] = datetime.utcnow().isoformat()
        self._request("PATCH", f"batch_jobs?batch_id=eq.{batch_id}", json=data)

    def get_pending_batches(self) -> list[dict]:
        """Get batch jobs that haven't completed yet."""
        result = self._request(
            "GET",
            "batch_jobs?status=neq.completed&status=neq.failed&order=created_at.asc"
        )
        return result or []

    def get_batch_job(self, batch_id: str) -> dict | None:
        """Get a batch job by ID."""
        result = self._request("GET", f"batch_jobs?batch_id=eq.{batch_id}")
        return result[0] if result else None

    # --- Ratings ---

    def get_opportunity_rating(self, opportunity_id: str) -> dict | None:
        """Get the rating for an opportunity."""
        result = self._request("GET", f"opportunity_ratings?opportunity_id=eq.{opportunity_id}")
        return result[0] if result else None

    def upsert_rating(self, opportunity_id: str, rating: int, feedback: str | None = None) -> dict:
        """Insert or update a rating for an opportunity."""
        data = {
            "opportunity_id": opportunity_id,
            "rating": rating,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if feedback:
            data["feedback"] = feedback

        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"}
        url = f"{self.base_url}/opportunity_ratings"
        response = self._client.post(url, json=data, headers=headers)
        response.raise_for_status()

        # Also update the opportunity's user_rating field
        self._request("PATCH", f"opportunities?id=eq.{opportunity_id}", json={"user_rating": rating})

        return response.json()[0] if response.json() else data

    def get_unrated_opportunities(self, limit: int = 20) -> list[dict]:
        """Get opportunities that haven't been rated yet."""
        endpoint = (
            "opportunities?"
            "user_rating=is.null&"
            "order=relevance_score.desc.nullslast,created_at.desc&"
            f"limit={limit}"
        )
        return self._request("GET", endpoint) or []

    def get_rated_opportunities(self, limit: int = 50) -> list[dict]:
        """Get rated opportunities."""
        endpoint = (
            "opportunities?"
            "user_rating=not.is.null&"
            "order=updated_at.desc&"
            f"limit={limit}"
        )
        return self._request("GET", endpoint) or []

    def get_all_opportunities(self, sort: str = "ai_score", order: str = "desc", limit: int = 100) -> list[dict]:
        """Get all opportunities with sorting options."""
        # Map sort options to database columns
        sort_map = {
            "ai_score": "relevance_score",
            "user_rating": "user_rating",
            "date": "created_at",
            "deadline": "deadline",
        }
        sort_col = sort_map.get(sort, "relevance_score")
        order_dir = "desc" if order == "desc" else "asc"

        endpoint = (
            "opportunities?"
            f"order={sort_col}.{order_dir}.nullslast&"
            f"limit={limit}"
        )
        return self._request("GET", endpoint) or []

    def get_rating_stats(self) -> dict:
        """Get rating statistics."""
        # Get all opportunities with ratings
        rated = self._request("GET", "opportunities?select=id,user_rating&user_rating=not.is.null")
        unrated = self._request("GET", "opportunities?select=id&user_rating=is.null")

        rated_list = rated or []
        unrated_list = unrated or []

        total_rated = len(rated_list)
        total_unrated = len(unrated_list)

        # Calculate stats
        if total_rated > 0:
            ratings = [r["user_rating"] for r in rated_list if r.get("user_rating")]
            avg_rating = sum(ratings) / len(ratings) if ratings else 0
            five_star = sum(1 for r in ratings if r == 5)

            # Distribution
            distribution = {}
            for r in ratings:
                distribution[r] = distribution.get(r, 0) + 1
        else:
            avg_rating = 0
            five_star = 0
            distribution = {}

        return {
            "total_rated": total_rated,
            "unrated": total_unrated,
            "avg_rating": avg_rating,
            "five_star": five_star,
            "distribution": distribution,
        }

    # --- Signal Weights ---

    def get_signal_weights(self) -> list[dict]:
        """Get all learned signal weights."""
        return self._request("GET", "learned_signal_weights?order=signal_name") or []

    def get_signal_weight(self, signal_name: str, signal_type: str) -> float:
        """Get weight for a specific signal."""
        from urllib.parse import quote
        encoded_name = quote(signal_name, safe='')
        result = self._request(
            "GET",
            f"learned_signal_weights?signal_name=eq.{encoded_name}&signal_type=eq.{signal_type}"
        )
        return result[0]["weight"] if result else 1.0

    def update_signal_weight(self, signal_name: str, signal_type: str, weight: float, increment_count: bool = True):
        """Update or insert a signal weight."""
        from urllib.parse import quote
        encoded_name = quote(signal_name, safe='')

        # Check if exists
        existing = self._request(
            "GET",
            f"learned_signal_weights?signal_name=eq.{encoded_name}&signal_type=eq.{signal_type}"
        )

        if existing:
            # Update
            data = {
                "weight": weight,
                "updated_at": datetime.utcnow().isoformat(),
            }
            if increment_count:
                data["sample_count"] = existing[0].get("sample_count", 0) + 1
            self._request(
                "PATCH",
                f"learned_signal_weights?signal_name=eq.{encoded_name}&signal_type=eq.{signal_type}",
                json=data
            )
        else:
            # Insert
            self._request("POST", "learned_signal_weights", json={
                "signal_name": signal_name,
                "signal_type": signal_type,
                "weight": weight,
                "sample_count": 1,
            })

    # --- Scoring Examples ---

    def get_scoring_examples(self, category: str | None = None, limit: int = 10) -> list[dict]:
        """Get scoring examples, optionally filtered by category."""
        endpoint = "scoring_examples?order=priority.desc,created_at.desc"
        if category:
            endpoint += f"&category=eq.{category}"
        endpoint += f"&limit={limit}"
        return self._request("GET", endpoint) or []

    def insert_scoring_example(self, example: dict) -> dict:
        """Insert a new scoring example."""
        result = self._request("POST", "scoring_examples", json=example)
        return result[0] if result else example

    def delete_scoring_examples(self, example_ids: list[str]):
        """Delete scoring examples by ID."""
        for eid in example_ids:
            self._request("DELETE", f"scoring_examples?id=eq.{eid}")

    def get_example_token_budget(self) -> dict:
        """Get total tokens used by examples."""
        examples = self._request("GET", "scoring_examples?select=token_count,category")
        if not examples:
            return {"total": 0, "by_category": {}}

        total = sum(e.get("token_count", 0) for e in examples)
        by_category = {}
        for e in examples:
            cat = e.get("category", "other")
            by_category[cat] = by_category.get(cat, 0) + e.get("token_count", 0)

        return {"total": total, "by_category": by_category}

    def log_condensation(self, examples_before: int, examples_after: int,
                         tokens_before: int, tokens_after: int, model: str = None):
        """Log a condensation event."""
        self._request("POST", "example_condensation_log", json={
            "examples_before": examples_before,
            "examples_after": examples_after,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "llm_model": model,
        })


# Global database instance
_db: Database | None = None


def get_db() -> Database:
    """Get or create the global database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db
