"""Digest content generator."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from ..db import get_db

logger = logging.getLogger(__name__)


def _format_deadline(deadline: str | None) -> str:
    """Format deadline for display."""
    if not deadline:
        return "No deadline"

    try:
        dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        days_left = (dt - now).days

        if days_left < 0:
            return "EXPIRED"
        elif days_left == 0:
            return "TODAY!"
        elif days_left == 1:
            return "Tomorrow"
        elif days_left < 7:
            return f"{days_left} days left"
        elif days_left < 30:
            return f"{days_left // 7} weeks left"
        else:
            return dt.strftime("%b %d, %Y")
    except Exception:
        return deadline


def _format_stipend(opp: dict) -> str:
    """Format stipend information."""
    amount = opp.get("stipend_amount")
    if not amount:
        return ""

    currency = opp.get("stipend_currency", "USD")
    if currency == "USD":
        return f"${amount:,.0f}"
    return f"{currency} {amount:,.0f}"


def _urgency_emoji(deadline: str | None) -> str:
    """Get urgency indicator based on deadline."""
    if not deadline:
        return ""

    try:
        dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        days_left = (dt - now).days

        if days_left < 0:
            return "[EXPIRED]"
        elif days_left <= 3:
            return "[URGENT]"
        elif days_left <= 7:
            return "[THIS WEEK]"
        return ""
    except Exception:
        return ""


def generate_digest(max_items: int = 10) -> dict[str, Any] | None:
    """Generate digest content from unnotified opportunities.

    Returns a dict with:
    - subject: Email subject line
    - html: HTML email body
    - text: Plain text email body
    - opportunity_ids: List of IDs included
    - count: Number of opportunities
    """
    db = get_db()

    opportunities = db.get_unnotified_opportunities(limit=max_items)

    if not opportunities:
        logger.info("No new opportunities to include in digest")
        return None

    # Group by urgency
    urgent = []  # < 7 days
    this_month = []  # < 30 days
    coming_up = []  # >= 30 days or no deadline

    for opp in opportunities:
        deadline = opp.get("deadline")
        if deadline:
            try:
                dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                days_left = (dt - now).days

                if days_left < 7:
                    urgent.append(opp)
                elif days_left < 30:
                    this_month.append(opp)
                else:
                    coming_up.append(opp)
            except Exception:
                coming_up.append(opp)
        else:
            coming_up.append(opp)

    # Build email content
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"OpportunityBug: {len(opportunities)} new opportunities"
    if urgent:
        subject = f"[{len(urgent)} URGENT] {subject}"

    # Build HTML
    html_parts = [f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #1a1a1a; font-size: 24px; margin-bottom: 8px; }}
        h2 {{ color: #666; font-size: 18px; margin-top: 24px; margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
        .opp {{ background: #f9f9f9; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
        .opp-title {{ font-size: 16px; font-weight: 600; color: #1a1a1a; margin-bottom: 4px; }}
        .opp-org {{ color: #666; font-size: 14px; margin-bottom: 8px; }}
        .opp-meta {{ font-size: 13px; color: #888; margin-bottom: 8px; }}
        .opp-summary {{ font-size: 14px; margin-bottom: 12px; }}
        .opp-highlights {{ font-size: 13px; color: #555; }}
        .opp-highlights li {{ margin-bottom: 4px; }}
        .btn {{ display: inline-block; background: #2563eb; color: white; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-size: 14px; }}
        .urgent {{ border-left: 4px solid #ef4444; }}
        .score {{ font-size: 12px; color: #888; }}
        .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; font-size: 12px; color: #888; }}
    </style>
</head>
<body>
    <h1>🐛 OpportunityBug</h1>
    <p style="color: #666; margin-bottom: 24px;">{today} - {len(opportunities)} opportunities for you</p>
"""]

    def render_opportunity(opp: dict, urgent: bool = False) -> str:
        urgency = _urgency_emoji(opp.get("deadline"))
        deadline_str = _format_deadline(opp.get("deadline"))
        stipend_str = _format_stipend(opp)

        meta_parts = [opp.get("type", "").title()]
        if opp.get("location"):
            meta_parts.append(opp["location"])
        if stipend_str:
            meta_parts.append(stipend_str)
        if opp.get("travel_support") and opp["travel_support"] != "none":
            meta_parts.append(f"Travel: {opp['travel_support']}")

        highlights_html = ""
        if opp.get("highlights"):
            highlights_html = "<ul class='opp-highlights'>" + "".join(
                f"<li>{h}</li>" for h in opp["highlights"][:3]
            ) + "</ul>"

        url = opp.get("application_url") or opp.get("url", "#")
        relevance = opp.get("relevance_score", 0)

        return f"""
        <div class="opp {'urgent' if urgent else ''}">
            <div class="opp-title">{urgency} {opp.get('title', 'Untitled')}</div>
            <div class="opp-org">{opp.get('organization', 'Unknown organization')}</div>
            <div class="opp-meta">{' · '.join(meta_parts)} · Deadline: {deadline_str}</div>
            <div class="opp-summary">{opp.get('summary', '')}</div>
            {highlights_html}
            <a href="{url}" class="btn">View & Apply</a>
            <span class="score">Match: {relevance:.0%}</span>
        </div>
        """

    if urgent:
        html_parts.append("<h2>Urgent (< 7 days)</h2>")
        for opp in urgent:
            html_parts.append(render_opportunity(opp, urgent=True))

    if this_month:
        html_parts.append("<h2>This Month</h2>")
        for opp in this_month:
            html_parts.append(render_opportunity(opp))

    if coming_up:
        html_parts.append("<h2>Coming Up</h2>")
        for opp in coming_up:
            html_parts.append(render_opportunity(opp))

    html_parts.append("""
    <div class="footer">
        <p>Generated by OpportunityBug</p>
        <p>Reply to this email with feedback or to adjust your preferences.</p>
    </div>
</body>
</html>
""")

    html = "".join(html_parts)

    # Build plain text version
    text_parts = [
        f"OPPORTUNITY RADAR - {today}",
        f"{len(opportunities)} opportunities for you",
        "=" * 50,
        ""
    ]

    def render_text(opp: dict) -> str:
        urgency = _urgency_emoji(opp.get("deadline"))
        deadline_str = _format_deadline(opp.get("deadline"))
        stipend_str = _format_stipend(opp)

        lines = [
            f"{urgency} {opp.get('title', 'Untitled')}",
            f"   {opp.get('organization', '')}",
            f"   Type: {opp.get('type', 'N/A')} | Location: {opp.get('location', 'N/A')}",
            f"   Deadline: {deadline_str}"
        ]
        if stipend_str:
            lines.append(f"   Stipend: {stipend_str}")
        if opp.get("summary"):
            lines.append(f"   {opp['summary']}")
        lines.append(f"   Apply: {opp.get('application_url') or opp.get('url', 'N/A')}")
        lines.append("")
        return "\n".join(lines)

    if urgent:
        text_parts.append("\n--- URGENT (< 7 days) ---\n")
        for opp in urgent:
            text_parts.append(render_text(opp))

    if this_month:
        text_parts.append("\n--- THIS MONTH ---\n")
        for opp in this_month:
            text_parts.append(render_text(opp))

    if coming_up:
        text_parts.append("\n--- COMING UP ---\n")
        for opp in coming_up:
            text_parts.append(render_text(opp))

    text = "\n".join(text_parts)

    return {
        "subject": subject,
        "html": html,
        "text": text,
        "opportunity_ids": [opp["id"] for opp in opportunities],
        "count": len(opportunities)
    }


def generate_weekly_roundup(max_items: int = 15, min_relevance: float = 0.5) -> dict[str, Any] | None:
    """Generate weekly summary of the best opportunities from the past 7 days.

    Unlike the daily digest which shows unnotified items, this shows the TOP
    opportunities from the entire week regardless of notification status.

    Returns a dict with:
    - subject: Email subject line
    - html: HTML email body
    - text: Plain text email body
    - opportunity_ids: List of IDs included
    - count: Number of opportunities
    """
    db = get_db()

    opportunities = db.get_opportunities_since(
        days=7,
        min_relevance=min_relevance,
        limit=max_items
    )

    if not opportunities:
        logger.info("No opportunities from this week for weekly roundup")
        return None

    # Group by type for weekly summary
    by_type = defaultdict(list)
    for opp in opportunities:
        opp_type = opp.get("type", "other")
        by_type[opp_type].append(opp)

    # Build email content
    today = datetime.now().strftime("%B %d, %Y")
    week_start = (datetime.now() - timedelta(days=7)).strftime("%B %d")
    subject = f"Weekly Roundup: Top {len(opportunities)} Opportunities ({week_start} - {today})"

    # Build HTML
    html_parts = [f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #1a1a1a; font-size: 24px; margin-bottom: 8px; }}
        h2 {{ color: #666; font-size: 18px; margin-top: 24px; margin-bottom: 12px; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
        .opp {{ background: #f9f9f9; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
        .opp-title {{ font-size: 16px; font-weight: 600; color: #1a1a1a; margin-bottom: 4px; }}
        .opp-org {{ color: #666; font-size: 14px; margin-bottom: 8px; }}
        .opp-meta {{ font-size: 13px; color: #888; margin-bottom: 8px; }}
        .opp-summary {{ font-size: 14px; margin-bottom: 12px; }}
        .btn {{ display: inline-block; background: #2563eb; color: white; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-size: 14px; }}
        .score {{ font-size: 12px; color: #888; }}
        .type-badge {{ display: inline-block; background: #e0e7ff; color: #4338ca; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-right: 4px; }}
        .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; font-size: 12px; color: #888; }}
    </style>
</head>
<body>
    <h1>Weekly Roundup</h1>
    <p style="color: #666; margin-bottom: 24px;">Top {len(opportunities)} opportunities from {week_start} - {today}</p>
"""]

    def render_opportunity(opp: dict) -> str:
        deadline_str = _format_deadline(opp.get("deadline"))
        stipend_str = _format_stipend(opp)
        opp_type = opp.get("type", "other").title()

        meta_parts = []
        if opp.get("location"):
            meta_parts.append(opp["location"])
        if stipend_str:
            meta_parts.append(stipend_str)
        if opp.get("travel_support") and opp["travel_support"] != "none":
            meta_parts.append(f"Travel: {opp['travel_support']}")

        url = opp.get("application_url") or opp.get("url", "#")
        relevance = opp.get("relevance_score", 0)

        return f"""
        <div class="opp">
            <span class="type-badge">{opp_type}</span>
            <div class="opp-title">{opp.get('title', 'Untitled')}</div>
            <div class="opp-org">{opp.get('organization', 'Unknown organization')}</div>
            <div class="opp-meta">{' · '.join(meta_parts) if meta_parts else ''} · Deadline: {deadline_str}</div>
            <div class="opp-summary">{opp.get('summary', '')}</div>
            <a href="{url}" class="btn">View & Apply</a>
            <span class="score">Match: {relevance:.0%}</span>
        </div>
        """

    # Render by type
    type_order = ["fellowship", "internship", "residency", "hackathon", "job", "grant", "accelerator", "other"]
    type_labels = {
        "fellowship": "Fellowships & Research Programs",
        "internship": "Internships",
        "residency": "Residencies",
        "hackathon": "Hackathons & Competitions",
        "job": "Jobs",
        "grant": "Grants & Funding",
        "accelerator": "Accelerators",
        "other": "Other Opportunities"
    }

    for opp_type in type_order:
        if opp_type in by_type:
            html_parts.append(f"<h2>{type_labels.get(opp_type, opp_type.title())}</h2>")
            for opp in by_type[opp_type]:
                html_parts.append(render_opportunity(opp))

    html_parts.append("""
    <div class="footer">
        <p>Weekly Roundup by OpportunityBug</p>
        <p>These are the highest-scoring opportunities discovered this week.</p>
    </div>
</body>
</html>
""")

    html = "".join(html_parts)

    # Build plain text version
    text_parts = [
        f"WEEKLY ROUNDUP - {week_start} to {today}",
        f"Top {len(opportunities)} opportunities this week",
        "=" * 50,
        ""
    ]

    def render_text(opp: dict) -> str:
        deadline_str = _format_deadline(opp.get("deadline"))
        stipend_str = _format_stipend(opp)
        opp_type = opp.get("type", "other").upper()

        lines = [
            f"[{opp_type}] {opp.get('title', 'Untitled')}",
            f"   {opp.get('organization', '')}",
            f"   Location: {opp.get('location', 'N/A')} | Deadline: {deadline_str}"
        ]
        if stipend_str:
            lines.append(f"   Stipend: {stipend_str}")
        if opp.get("summary"):
            lines.append(f"   {opp['summary']}")
        lines.append(f"   Apply: {opp.get('application_url') or opp.get('url', 'N/A')}")
        lines.append(f"   Match: {opp.get('relevance_score', 0):.0%}")
        lines.append("")
        return "\n".join(lines)

    for opp_type in type_order:
        if opp_type in by_type:
            text_parts.append(f"\n=== {type_labels.get(opp_type, opp_type.upper())} ===\n")
            for opp in by_type[opp_type]:
                text_parts.append(render_text(opp))

    text = "\n".join(text_parts)

    return {
        "subject": subject,
        "html": html,
        "text": text,
        "opportunity_ids": [opp["id"] for opp in opportunities],
        "count": len(opportunities)
    }
