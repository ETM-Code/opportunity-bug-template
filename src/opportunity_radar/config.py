"""Configuration management."""

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv
import yaml


@dataclass
class Config:
    # Supabase
    supabase_url: str
    supabase_key: str

    # Gmail IMAP
    imap_host: str
    imap_port: int
    imap_username: str
    imap_password: str

    # OpenAI
    openai_api_key: str

    # Resend
    resend_api_key: str
    digest_recipient: str

    # App
    log_level: str
    environment: str


def load_config() -> Config:
    """Load configuration from environment variables."""
    load_dotenv()

    return Config(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_SERVICE_KEY"],
        imap_host=os.environ.get("IMAP_HOST", "imap.gmail.com"),
        imap_port=int(os.environ.get("IMAP_PORT", "993")),
        imap_username=os.environ["IMAP_USERNAME"],
        imap_password=os.environ["IMAP_PASSWORD"],
        openai_api_key=os.environ["OPENAI_API_KEY"],
        resend_api_key=os.environ["RESEND_API_KEY"],
        digest_recipient=os.environ["DIGEST_RECIPIENT"],
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        environment=os.environ.get("ENVIRONMENT", "development"),
    )


def load_sources() -> dict:
    """Load source configuration from YAML."""
    sources_path = Path(__file__).parent.parent.parent / "data" / "sources.yaml"
    with open(sources_path) as f:
        return yaml.safe_load(f)


# Global config instance
_config: Config | None = None


def get_config() -> Config:
    """Get or create the global config instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
