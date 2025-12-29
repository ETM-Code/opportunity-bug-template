"""Email/IMAP source connector."""

import email
import imaplib
import logging
import re
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header
from typing import Iterator

import html2text

from ..config import get_config
from ..db import get_db

logger = logging.getLogger(__name__)

# Configure html2text for clean markdown output
_h2t = html2text.HTML2Text()
_h2t.ignore_links = False
_h2t.ignore_images = True
_h2t.body_width = 0  # No wrapping
_h2t.skip_internal_links = True


@dataclass
class EmailMessage:
    """Parsed email message."""
    msg_id: str
    thread_id: str | None
    subject: str
    sender: str
    date: datetime | None
    body_text: str
    body_html: str
    links: list[str]

    def to_markdown(self) -> str:
        """Convert email to clean markdown for LLM processing."""
        if self.body_html:
            content = _h2t.handle(self.body_html)
        else:
            content = self.body_text

        # Clean up excessive whitespace
        content = re.sub(r'\n{3,}', '\n\n', content)

        return f"# {self.subject}\n\nFrom: {self.sender}\nDate: {self.date}\n\n{content}"

    def get_job_links(self) -> list[str]:
        """Filter links to only include likely job/opportunity links."""
        job_link_patterns = [
            r'careers\.',
            r'/careers/',
            r'/jobs/',
            r'/job/',
            r'greenhouse\.io',
            r'lever\.co',
            r'workday',
            r'ashbyhq\.com',
            r'icims\.com',
            r'jobs\.80000hours\.org',
            r'apply',
            r'/fellowship',
            r'/internship',
            r'/residency',
        ]

        exclude_patterns = [
            r'unsubscribe',
            r'preferences',
            r'mailto:',
            r'facebook\.com',
            r'twitter\.com',
            r'linkedin\.com/company',
            r'instagram\.com',
            r'view.*browser',
            r'email-tracking',
            r'click\.convertkit',
            r'list-manage\.com',
        ]

        job_links = []
        for link in self.links:
            link_lower = link.lower()

            # Skip excluded patterns
            if any(re.search(p, link_lower) for p in exclude_patterns):
                continue

            # Include if matches job patterns OR is a direct apply link
            if any(re.search(p, link_lower) for p in job_link_patterns):
                job_links.append(link)

        return list(set(job_links))


class EmailSource:
    """Fetches emails via IMAP."""

    def __init__(self):
        config = get_config()
        self.host = config.imap_host
        self.port = config.imap_port
        self.username = config.imap_username
        self.password = config.imap_password
        self._mail: imaplib.IMAP4_SSL | None = None

    def connect(self):
        """Connect to the IMAP server."""
        if self._mail:
            return

        context = ssl.create_default_context()
        self._mail = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=context)
        self._mail.login(self.username, self.password)
        logger.info(f"Connected to {self.host} as {self.username}")

    def disconnect(self):
        """Disconnect from the IMAP server."""
        if self._mail:
            try:
                self._mail.logout()
            except Exception:
                pass
            self._mail = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def _decode_header(self, header: str | None) -> str:
        """Decode an email header."""
        if not header:
            return ""
        decoded_parts = []
        for part, encoding in decode_header(header):
            if isinstance(part, bytes):
                decoded_parts.append(part.decode(encoding or "utf-8", errors="replace"))
            else:
                decoded_parts.append(part)
        return " ".join(decoded_parts)

    def _get_body(self, msg: email.message.Message) -> tuple[str, str]:
        """Extract text and HTML body from email."""
        text_body = ""
        html_body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    continue

                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        decoded = payload.decode(charset, errors="replace")

                        if content_type == "text/plain":
                            text_body = decoded
                        elif content_type == "text/html":
                            html_body = decoded
                except Exception as e:
                    logger.warning(f"Failed to decode email part: {e}")
        else:
            content_type = msg.get_content_type()
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    decoded = payload.decode(charset, errors="replace")

                    if content_type == "text/plain":
                        text_body = decoded
                    elif content_type == "text/html":
                        html_body = decoded
            except Exception as e:
                logger.warning(f"Failed to decode email body: {e}")

        return text_body, html_body

    def _extract_links(self, html: str, text: str) -> list[str]:
        """Extract URLs from email content."""
        links = set()

        # From HTML
        href_pattern = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
        links.update(href_pattern.findall(html))

        # From text
        url_pattern = re.compile(r'https?://[^\s<>"\']+')
        links.update(url_pattern.findall(text))
        links.update(url_pattern.findall(html))

        # Filter and clean
        clean_links = []
        for link in links:
            link = link.strip().rstrip(".,;:)")
            if link.startswith("http") and len(link) > 10:
                clean_links.append(link)

        return clean_links

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse email date header."""
        if not date_str:
            return None
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(date_str)
        except Exception:
            return None

    def fetch_emails(
        self,
        folder: str = "INBOX",
        since_days: int = 7,
        sender_patterns: list[str] | None = None,
        limit: int = 50
    ) -> Iterator[EmailMessage]:
        """Fetch emails from the specified folder.

        Args:
            folder: IMAP folder to search
            since_days: Only fetch emails from last N days
            sender_patterns: Optional list of sender patterns to filter by (supports *)
            limit: Maximum number of emails to fetch
        """
        self.connect()

        self._mail.select(folder)

        # Build search criteria
        from datetime import timedelta
        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        search_criteria = f'(SINCE {since_date})'

        status, message_ids = self._mail.search(None, search_criteria)
        if status != "OK":
            logger.error(f"Failed to search emails: {status}")
            return

        msg_ids = message_ids[0].split()
        if not msg_ids:
            logger.info("No emails found matching criteria")
            return

        # Take most recent
        msg_ids = msg_ids[-limit:]

        for msg_id in reversed(msg_ids):  # Most recent first
            try:
                status, msg_data = self._mail.fetch(msg_id, "(RFC822 X-GM-MSGID X-GM-THRID)")
                if status != "OK":
                    continue

                # Parse Gmail-specific IDs
                gmail_msg_id = ""
                gmail_thread_id = ""
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        # Extract Gmail IDs from response
                        header = response_part[0].decode() if isinstance(response_part[0], bytes) else str(response_part[0])
                        if "X-GM-MSGID" in header:
                            match = re.search(r'X-GM-MSGID\s+(\d+)', header)
                            if match:
                                gmail_msg_id = match.group(1)
                        if "X-GM-THRID" in header:
                            match = re.search(r'X-GM-THRID\s+(\d+)', header)
                            if match:
                                gmail_thread_id = match.group(1)

                        # Parse email
                        raw_email = response_part[1]
                        msg = email.message_from_bytes(raw_email)

                        subject = self._decode_header(msg.get("Subject"))
                        sender = self._decode_header(msg.get("From"))
                        date = self._parse_date(msg.get("Date"))

                        # Filter by sender if patterns provided
                        if sender_patterns:
                            sender_lower = sender.lower()
                            matched = False
                            for pattern in sender_patterns:
                                pattern = pattern.lower().replace("*", ".*")
                                if re.search(pattern, sender_lower):
                                    matched = True
                                    break
                            if not matched:
                                continue

                        text_body, html_body = self._get_body(msg)
                        links = self._extract_links(html_body, text_body)

                        yield EmailMessage(
                            msg_id=gmail_msg_id or msg_id.decode(),
                            thread_id=gmail_thread_id or None,
                            subject=subject,
                            sender=sender,
                            date=date,
                            body_text=text_body,
                            body_html=html_body,
                            links=links
                        )

            except Exception as e:
                logger.error(f"Failed to process email {msg_id}: {e}")
                continue

    def fetch_new_emails(
        self,
        source_id: str,
        sender_patterns: list[str] | None = None,
        since_days: int = 7
    ) -> Iterator[EmailMessage]:
        """Fetch only emails that haven't been processed yet."""
        db = get_db()

        for email_msg in self.fetch_emails(
            since_days=since_days,
            sender_patterns=sender_patterns
        ):
            # Check if already processed
            if db.email_seen(source_id, email_msg.msg_id):
                logger.debug(f"Skipping already processed email: {email_msg.subject}")
                continue

            # Record the email
            db.insert_raw_email({
                "source_id": source_id,
                "gmail_msg_id": email_msg.msg_id,
                "gmail_thread_id": email_msg.thread_id,
                "subject": email_msg.subject,
                "sender": email_msg.sender,
                "received_at": email_msg.date.isoformat() if email_msg.date else None,
                "status": "pending"
            })

            yield email_msg
