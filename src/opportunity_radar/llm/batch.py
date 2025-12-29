"""Batch API for cost-effective async LLM processing (50% cheaper)."""

import json
import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from openai import OpenAI

from ..config import get_config
from ..db import get_db
from .prompts import CLASSIFY_PROMPT, EXTRACT_PROMPT, SCORE_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class BatchRequest:
    """A single request to be included in a batch."""
    custom_id: str  # Unique ID to match response back to request
    content: str
    source_url: str | None
    source_id: str | None
    request_type: str  # 'classify', 'extract', or 'score'
    metadata: dict = field(default_factory=dict)


@dataclass
class BatchJob:
    """Tracks a batch job."""
    batch_id: str
    input_file_id: str
    status: str
    created_at: datetime
    requests: list[BatchRequest]
    output_file_id: str | None = None
    error_file_id: str | None = None


class BatchPipeline:
    """Handles batch processing for cost-effective LLM calls."""

    FAST_MODEL = "gpt-5-nano"
    SMART_MODEL = "gpt-5-mini"

    def __init__(self):
        config = get_config()
        self.client = OpenAI(api_key=config.openai_api_key)
        self._pending_requests: list[BatchRequest] = []

    def add_classify_request(
        self,
        content: str,
        source_url: str | None = None,
        source_id: str | None = None,
    ) -> str:
        """Add a classification request to the batch."""
        custom_id = f"classify_{len(self._pending_requests)}_{int(time.time())}"

        self._pending_requests.append(BatchRequest(
            custom_id=custom_id,
            content=content,
            source_url=source_url,
            source_id=source_id,
            request_type="classify",
            metadata={"content_preview": content[:500]}
        ))

        return custom_id

    def add_extract_request(
        self,
        content: str,
        source_url: str | None = None,
        source_id: str | None = None,
    ) -> str:
        """Add an extraction request to the batch."""
        custom_id = f"extract_{len(self._pending_requests)}_{int(time.time())}"

        self._pending_requests.append(BatchRequest(
            custom_id=custom_id,
            content=content,
            source_url=source_url,
            source_id=source_id,
            request_type="extract",
            metadata={"source_url": source_url}
        ))

        return custom_id

    def add_score_request(
        self,
        opportunity: dict,
        user_profile: dict,
    ) -> str:
        """Add a scoring request to the batch."""
        custom_id = f"score_{len(self._pending_requests)}_{int(time.time())}"

        self._pending_requests.append(BatchRequest(
            custom_id=custom_id,
            content=json.dumps(opportunity),
            source_url=opportunity.get("url"),
            source_id=opportunity.get("source_id"),
            request_type="score",
            metadata={"opportunity": opportunity, "profile": user_profile}
        ))

        return custom_id

    def _build_prompt(self, request: BatchRequest) -> str:
        """Build the appropriate prompt for a request."""
        if request.request_type == "classify":
            return CLASSIFY_PROMPT.format(content=request.content[:10000])
        elif request.request_type == "extract":
            content = request.content
            if len(content) > 15000:
                content = content[:15000] + "\n...[truncated]..."
            return EXTRACT_PROMPT.format(content=content)
        elif request.request_type == "score":
            # Score requests have opportunity and profile in metadata
            opp = request.metadata["opportunity"]
            profile = request.metadata["profile"]

            profile_text = f"""
Name: {profile.get('name', 'Unknown')}
Background: {profile.get('background', 'Not specified')}
Interests: {', '.join(profile.get('interests', []))}
Constraints: {json.dumps(profile.get('constraints', {}))}
"""
            stipend = "Not specified"
            if opp.get("stipend_amount"):
                stipend = f"{opp.get('stipend_currency', 'USD')} {opp['stipend_amount']}"

            return SCORE_PROMPT.format(
                profile=profile_text,
                title=opp.get("title", "Unknown"),
                organization=opp.get("organization", "Unknown"),
                type=opp.get("type", "Unknown"),
                location=opp.get("location", "Unknown"),
                deadline=opp.get("deadline", "Unknown"),
                stipend=stipend,
                travel_support=opp.get("travel_support", "Unknown"),
                eligibility=opp.get("eligibility", "Unknown"),
                summary=opp.get("summary", "No summary"),
                high_signals=", ".join(profile.get("high_value_signals", [])),
                low_signals=", ".join(profile.get("low_value_signals", []))
            )
        else:
            raise ValueError(f"Unknown request type: {request.request_type}")

    def _get_model(self, request: BatchRequest) -> str:
        """Get the appropriate model for a request type."""
        if request.request_type == "score":
            return self.SMART_MODEL
        return self.FAST_MODEL

    def create_batch_file(self) -> tuple[str, list[BatchRequest]]:
        """Create a JSONL file for the batch and upload it.

        Returns: (file_id, requests)
        """
        if not self._pending_requests:
            raise ValueError("No requests to batch")

        # Build JSONL content
        lines = []
        for request in self._pending_requests:
            prompt = self._build_prompt(request)
            model = self._get_model(request)

            line = {
                "custom_id": request.custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 8000,
                    "reasoning_effort": "low" if request.request_type != "score" else "medium",
                }
            }
            lines.append(json.dumps(line))

        jsonl_content = "\n".join(lines)

        # Write to temp file and upload
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(jsonl_content)
            temp_path = f.name

        try:
            with open(temp_path, 'rb') as f:
                file_response = self.client.files.create(
                    file=f,
                    purpose="batch"
                )
            logger.info(f"Uploaded batch file: {file_response.id} ({len(self._pending_requests)} requests)")

            requests = self._pending_requests.copy()
            self._pending_requests.clear()

            return file_response.id, requests
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def submit_batch(self) -> BatchJob:
        """Submit the pending requests as a batch job."""
        file_id, requests = self.create_batch_file()

        batch = self.client.batches.create(
            input_file_id=file_id,
            endpoint="/v1/chat/completions",
            completion_window="24h"
        )

        logger.info(f"Created batch job: {batch.id} (status: {batch.status})")

        job = BatchJob(
            batch_id=batch.id,
            input_file_id=file_id,
            status=batch.status,
            created_at=datetime.utcnow(),
            requests=requests,
        )

        # Store batch info in database
        db = get_db()
        db.insert_batch_job({
            "batch_id": batch.id,
            "input_file_id": file_id,
            "status": batch.status,
            "request_count": len(requests),
            "requests_json": json.dumps([{
                "custom_id": r.custom_id,
                "request_type": r.request_type,
                "source_url": r.source_url,
                "source_id": r.source_id,
                "metadata": r.metadata,
            } for r in requests])
        })

        return job

    def check_batch(self, batch_id: str) -> dict:
        """Check the status of a batch job."""
        batch = self.client.batches.retrieve(batch_id)

        result = {
            "batch_id": batch.id,
            "status": batch.status,
            "created_at": batch.created_at,
            "completed_at": batch.completed_at,
            "failed_at": batch.failed_at,
            "output_file_id": batch.output_file_id,
            "error_file_id": batch.error_file_id,
            "request_counts": batch.request_counts,
        }

        # Update in database
        db = get_db()
        db.update_batch_status(batch_id, batch.status, batch.output_file_id)

        return result

    def get_batch_results(self, batch_id: str) -> Iterator[tuple[str, dict]]:
        """Get results from a completed batch.

        Yields: (custom_id, response_content)
        """
        batch = self.client.batches.retrieve(batch_id)

        if batch.status != "completed":
            raise ValueError(f"Batch not completed: {batch.status}")

        if not batch.output_file_id:
            raise ValueError("No output file for batch")

        # Download output file
        file_response = self.client.files.content(batch.output_file_id)
        content = file_response.text

        for line in content.strip().split("\n"):
            if not line:
                continue

            result = json.loads(line)
            custom_id = result["custom_id"]

            if result.get("error"):
                logger.warning(f"Batch request {custom_id} failed: {result['error']}")
                continue

            response = result.get("response", {})
            body = response.get("body", {})
            choices = body.get("choices", [])

            if choices:
                content = choices[0].get("message", {}).get("content", "")
                yield custom_id, content

    def pending_count(self) -> int:
        """Get number of pending requests."""
        return len(self._pending_requests)
