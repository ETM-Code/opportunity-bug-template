"""Email sending via Gmail SMTP."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import get_config
from ..db import get_db

logger = logging.getLogger(__name__)


def send_digest(
    subject: str,
    html: str,
    text: str,
    opportunity_ids: list[str]
) -> bool:
    """Send the digest email via Gmail SMTP.

    Returns True if successful, False otherwise.
    """
    config = get_config()
    db = get_db()

    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"OpportunityBug <{config.imap_username}>"
        msg["To"] = config.digest_recipient

        # Attach text and HTML parts
        part1 = MIMEText(text, "plain")
        part2 = MIMEText(html, "html")
        msg.attach(part1)
        msg.attach(part2)

        # Send via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.imap_username, config.imap_password)
            server.sendmail(
                config.imap_username,
                config.digest_recipient,
                msg.as_string()
            )

        logger.info(f"Digest sent successfully to {config.digest_recipient}")

        # Mark opportunities as notified
        db.mark_opportunities_notified(opportunity_ids)

        # Log the digest
        db.log_digest(
            opportunity_ids=opportunity_ids,
            subject=subject,
            status="sent"
        )

        return True

    except Exception as e:
        logger.error(f"Failed to send digest: {e}")

        # Log the failed attempt
        db.log_digest(
            opportunity_ids=opportunity_ids,
            subject=subject,
            status="failed",
            error=str(e)
        )

        return False


def send_test_email() -> bool:
    """Send a test email to verify Gmail SMTP is configured correctly."""
    config = get_config()

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "OpportunityBug - Test Email"
        msg["From"] = f"OpportunityBug <{config.imap_username}>"
        msg["To"] = config.digest_recipient

        text = "Test Email\n\nIf you're seeing this, your OpportunityBug email delivery is working correctly!"
        html = """
            <h1>Test Email</h1>
            <p>If you're seeing this, your OpportunityBug email delivery is working correctly!</p>
        """

        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.imap_username, config.imap_password)
            server.sendmail(
                config.imap_username,
                config.digest_recipient,
                msg.as_string()
            )

        logger.info(f"Test email sent to {config.digest_recipient}")
        return True

    except Exception as e:
        logger.error(f"Failed to send test email: {e}")
        return False
