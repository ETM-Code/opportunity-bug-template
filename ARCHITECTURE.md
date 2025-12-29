# Opportunity Radar - Architecture & Code Structure

## Overview

A background agent that monitors opportunity sources (company career pages, newsletters, Twitter/X accounts), filters them for relevance to your profile, and sends you a daily email digest of the top opportunities.

---

## System Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SCHEDULED JOB (Daily)                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              SOURCE CONNECTORS                               │
│                                                                              │
│   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐    │
│   │   Page      │   │   Email     │   │    RSS      │   │  Twitter    │    │
│   │  Monitor    │   │   (IMAP)    │   │   Feeds     │   │ (RSSHub)    │    │
│   │             │   │             │   │             │   │             │    │
│   │ - Careers   │   │ - Apart     │   │ - HN        │   │ - @OpenAI   │    │
│   │ - Events    │   │ - DTM       │   │ - Blogs     │   │ - @xai      │    │
│   │ - Programs  │   │ - 80k       │   │             │   │ - etc       │    │
│   └──────┬──────┘   └──────┬──────┘   └──────┬──────┘   └──────┬──────┘    │
│          │                 │                 │                 │           │
│          └─────────────────┴─────────────────┴─────────────────┘           │
│                                      │                                      │
│                                      ▼                                      │
│                              RawItem[]                                      │
│                    (url, title, raw_text, source_id)                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              LLM PIPELINE                                    │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ 1. DEDUPLICATION                                                     │   │
│   │    - Check content_hash against DB                                   │   │
│   │    - Skip if seen before                                             │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ 2. CLASSIFIER (gpt-5-nano, low thinking)                            │   │
│   │    - "Is this an opportunity?"                                       │   │
│   │    - Returns: yes/no + confidence                                    │   │
│   │    - Filter: confidence < 0.7 → skip                                │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ 3. EXTRACTOR (gpt-5-nano, low thinking)                             │   │
│   │    - Structured JSON extraction                                      │   │
│   │    - Returns: title, org, deadline, stipend, travel, type, url      │   │
│   │    - Can return multiple opportunities per item                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ 4. SCORER (gpt-5-mini, default thinking)                            │   │
│   │    - Given user profile + opportunity                                │   │
│   │    - Returns: relevance_score (0-1), prestige_score (0-1)           │   │
│   │    - Also: one-line summary, key highlights                         │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              SUPABASE (Postgres)                            │
│                                                                              │
│   opportunities                     sources                                  │
│   ├─ id                            ├─ id                                    │
│   ├─ source_id                     ├─ name                                  │
│   ├─ title                         ├─ type                                  │
│   ├─ organization                  ├─ config (jsonb)                        │
│   ├─ url                           ├─ priority                              │
│   ├─ deadline                      ├─ last_checked                          │
│   ├─ stipend_amount                └─ active                                │
│   ├─ travel_support                                                         │
│   ├─ type                          seen_items                               │
│   ├─ relevance_score               ├─ id                                    │
│   ├─ prestige_score                ├─ content_hash                          │
│   ├─ summary                       └─ seen_at                               │
│   ├─ notified_at                                                            │
│   └─ created_at                                                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DIGEST GENERATOR                               │
│                                                                              │
│   1. Query: new opportunities (notified_at IS NULL)                         │
│   2. Sort by: relevance_score DESC, deadline ASC                            │
│   3. Take top 10                                                            │
│   4. Group by urgency (< 7 days, < 30 days, later)                         │
│   5. Format email HTML/text                                                 │
│   6. Mark as notified                                                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EMAIL SENDER                                   │
│                                                                              │
│   Via: Resend API (free tier: 100 emails/day)                              │
│   To: your-email@example.com                                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Code Structure

```
opportunity-radar/
│
├── src/
│   ├── __init__.py
│   │
│   ├── config.py                    # Environment variables, settings
│   │   - OPENAI_API_KEY
│   │   - SUPABASE_URL, SUPABASE_KEY
│   │   - RESEND_API_KEY
│   │   - IMAP credentials
│   │   - USER_PROFILE (from yaml or env)
│   │
│   ├── models.py                    # Pydantic models
│   │   - RawItem
│   │   - Opportunity
│   │   - Source
│   │   - DigestEmail
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── client.py                # Supabase client wrapper
│   │   └── queries.py               # Common queries
│   │       - get_active_sources()
│   │       - insert_opportunity()
│   │       - mark_notified()
│   │       - is_seen(content_hash)
│   │       - get_unnotified_opportunities()
│   │
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py                  # Abstract BaseSource
│   │   │   class BaseSource:
│   │   │       async def fetch() -> list[RawItem]
│   │   │
│   │   ├── page.py                  # PageMonitorSource
│   │   │   - Fetches URL, detects changes
│   │   │   - Uses httpx + BeautifulSoup
│   │   │   - Extracts links/text from configured selectors
│   │   │
│   │   ├── email.py                 # EmailSource (IMAP)
│   │   │   - Connects via IMAP
│   │   │   - Filters by label/sender
│   │   │   - Parses HTML to text
│   │   │
│   │   └── rss.py                   # RSSSource
│   │       - Uses feedparser
│   │       - For HN, blogs, etc.
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py                # OpenAI API wrapper
│   │   │   - Handles retries, rate limits
│   │   │   - Structured output parsing
│   │   │
│   │   ├── prompts.py               # Prompt templates
│   │   │   - CLASSIFIER_PROMPT
│   │   │   - EXTRACTOR_PROMPT
│   │   │   - SCORER_PROMPT
│   │   │
│   │   ├── classifier.py            # is_opportunity(raw_item) -> bool
│   │   ├── extractor.py             # extract(raw_item) -> list[Opportunity]
│   │   └── scorer.py                # score(opportunity, profile) -> scores
│   │
│   ├── pipeline/
│   │   ├── __init__.py
│   │   └── ingest.py                # Main pipeline orchestration
│   │       async def run_pipeline():
│   │           for source in get_active_sources():
│   │               raw_items = await source.fetch()
│   │               for item in raw_items:
│   │                   if is_seen(item): continue
│   │                   if not await classify(item): continue
│   │                   opportunities = await extract(item)
│   │                   for opp in opportunities:
│   │                       opp = await score(opp)
│   │                       save(opp)
│   │
│   ├── digest/
│   │   ├── __init__.py
│   │   ├── generator.py             # Build digest content
│   │   │   - Query unnotified opportunities
│   │   │   - Sort by relevance + urgency
│   │   │   - Group into sections
│   │   │   - Format as HTML + plain text
│   │   │
│   │   └── sender.py                # Send via Resend
│   │       - Uses resend-python SDK
│   │       - Handles errors gracefully
│   │
│   └── main.py                      # Entry point
│       async def main():
│           await run_pipeline()
│           await send_digest()
│
├── data/
│   └── sources.yaml                 # Source configuration (already created)
│
├── tests/
│   ├── test_classifier.py
│   ├── test_extractor.py
│   ├── test_sources.py
│   └── fixtures/
│       └── sample_emails/
│
├── Dockerfile
├── fly.toml                         # Fly.io config
├── pyproject.toml                   # Dependencies (uv/poetry)
├── .env.example
└── README.md
```

---

## Key Design Decisions

### 1. Source Abstraction

Every source implements the same interface:

```python
class BaseSource(ABC):
    def __init__(self, config: SourceConfig):
        self.config = config

    @abstractmethod
    async def fetch(self) -> list[RawItem]:
        """Fetch new items from this source."""
        pass
```

This means adding a new source type is just implementing `fetch()`.

### 2. LLM Models & Thinking Effort

We use the GPT-5 model family with appropriate thinking effort for each task:

| Stage | Model | Thinking | Why |
|-------|-------|----------|-----|
| Classifier | `gpt-5-nano` | low | Fast yes/no, cheap |
| Extractor | `gpt-5-nano` | low | Structured JSON, cheap |
| Scorer | `gpt-5-mini` | default | Needs reasoning about relevance |
| Digest | `gpt-5-mini` | default | Composing summaries |

```python
from openai import OpenAI

client = OpenAI()

# Classifier/Extractor: nano with low thinking
response = client.responses.create(
    model="gpt-5-nano",
    reasoning={"effort": "low"},
    input=[{"role": "user", "content": prompt}],
)

# Scorer/Digest: mini with default thinking
response = client.responses.create(
    model="gpt-5-mini",
    reasoning={"effort": "default"},
    input=[{"role": "user", "content": prompt}],
)
```

### 3. Deduplication Strategy

```python
def content_hash(item: RawItem) -> str:
    """Generate a hash for deduplication."""
    # Normalize: lowercase, strip whitespace, remove URLs
    normalized = normalize_text(item.raw_text)
    # Hash the normalized content + URL
    return hashlib.sha256(
        f"{item.url}:{normalized[:1000]}".encode()
    ).hexdigest()[:32]
```

We store hashes in `seen_items` table and skip anything we've seen.

---

## Database Schema (Supabase)

```sql
-- Sources table
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT NOT NULL,  -- 'page', 'email', 'rss', 'twitter'
    priority TEXT NOT NULL DEFAULT 'medium',  -- 'critical', 'high', 'medium', 'low'
    config JSONB NOT NULL DEFAULT '{}',
    active BOOLEAN NOT NULL DEFAULT true,
    last_checked_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seen items (for deduplication)
CREATE TABLE seen_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash TEXT NOT NULL UNIQUE,
    source_id UUID REFERENCES sources(id),
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Opportunities
CREATE TABLE opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES sources(id),

    -- Core fields
    title TEXT NOT NULL,
    organization TEXT,
    url TEXT NOT NULL,
    application_url TEXT,

    -- Details
    type TEXT,  -- 'residency', 'hackathon', 'fellowship', 'job', 'grant'
    deadline TIMESTAMPTZ,
    stipend_amount NUMERIC,
    stipend_currency TEXT DEFAULT 'USD',
    travel_support TEXT,  -- 'none', 'partial', 'full', 'unknown'
    location TEXT,
    is_remote BOOLEAN,

    -- LLM-generated
    summary TEXT,
    relevance_score FLOAT,
    prestige_score FLOAT,
    highlights TEXT[],  -- Key selling points

    -- Metadata
    raw_content TEXT,
    content_hash TEXT,
    notified_at TIMESTAMPTZ,  -- When we emailed about this
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX idx_opportunities_deadline ON opportunities(deadline);
CREATE INDEX idx_opportunities_notified ON opportunities(notified_at);
CREATE INDEX idx_opportunities_relevance ON opportunities(relevance_score DESC);
CREATE INDEX idx_seen_items_hash ON seen_items(content_hash);
```

---

## LLM Prompts

### Classifier Prompt

```python
CLASSIFIER_PROMPT = """You are classifying whether a piece of content describes
a concrete opportunity (job, fellowship, hackathon, residency, grant, program)
that someone could apply to.

An opportunity MUST have:
- A way to apply or participate
- Some form of benefit (money, experience, job, prize, travel)

NOT opportunities:
- General news articles
- Event announcements without applications
- Product launches
- Generic job board promotions

Content:
{content}

Respond with JSON:
{
    "is_opportunity": true/false,
    "confidence": 0.0-1.0,
    "reason": "brief explanation"
}
"""
```

### Extractor Prompt

```python
EXTRACTOR_PROMPT = """Extract structured information about opportunities from
this content. There may be 0, 1, or multiple opportunities.

User context (use for relevance hints):
- Background: {user_background}
- Interests: {user_interests}

Content:
{content}

For EACH opportunity found, extract:
{
    "title": "name of opportunity",
    "organization": "company or org name",
    "url": "main URL",
    "application_url": "direct application link if different",
    "type": "residency|hackathon|fellowship|job|grant|internship|program",
    "deadline": "ISO date or null",
    "stipend_amount": number or null,
    "stipend_currency": "USD|EUR|GBP|CHF|etc",
    "travel_support": "none|partial|full|unknown",
    "location": "city, country or Remote",
    "is_remote": true/false/null,
    "eligibility": "who can apply",
    "raw_description": "key details preserved"
}

Return JSON array: { "opportunities": [...] }
"""
```

### Scorer Prompt

```python
SCORER_PROMPT = """Score this opportunity for the following user.

USER PROFILE:
{user_profile}

OPPORTUNITY:
{opportunity_json}

Consider:
1. RELEVANCE (0.0-1.0): How well does this match the user's interests and constraints?
   - Perfect fit for their background and goals: 0.9-1.0
   - Good match: 0.7-0.9
   - Tangentially relevant: 0.4-0.7
   - Not relevant: 0.0-0.4

2. PRESTIGE (0.0-1.0): How impressive/valuable is this opportunity?
   - Top AI lab, YC, elite university: 0.9-1.0
   - Well-known company/program: 0.7-0.9
   - Solid but not famous: 0.4-0.7
   - Unknown or low-tier: 0.0-0.4

3. CONSTRAINTS CHECK:
   - Requires travel support? Does it provide it?
   - Student-eligible?
   - Geographic restrictions?

Return JSON:
{
    "relevance_score": 0.0-1.0,
    "prestige_score": 0.0-1.0,
    "summary": "One compelling sentence about this opportunity",
    "highlights": ["key point 1", "key point 2"],
    "constraints_met": true/false,
    "constraint_issues": ["any issues"]
}
"""
```

---

## Deployment (Fly.io)

### fly.toml

```toml
app = "opportunity-radar"
primary_region = "lhr"  # London, close to Ireland

[build]

[env]
  LOG_LEVEL = "INFO"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 0

# Scheduled job - runs daily at 8am UTC
[[services]]
  internal_port = 8080
  protocol = "tcp"

# We'll use fly machines for the scheduled job instead of a long-running service
```

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install uv && uv pip install --system -e .

# Copy source
COPY src/ src/
COPY data/ data/

# Run the pipeline
CMD ["python", "-m", "src.main"]
```

### Scheduling

Two options for daily runs:

**Option A: Fly.io Machines (recommended)**
Use `fly machines run` with a schedule, or use the Machines API to trigger from an external cron.

**Option B: GitHub Actions (simpler for now)**
```yaml
# .github/workflows/daily-digest.yml
name: Daily Digest
on:
  schedule:
    - cron: '0 8 * * *'  # 8am UTC daily
  workflow_dispatch:  # Manual trigger

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install uv && uv pip install --system -e .
      - run: python -m src.main
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
          # ... other secrets
```

---

## Dependencies

```toml
# pyproject.toml
[project]
name = "opportunity-radar"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",           # HTTP client
    "beautifulsoup4>=4.12",  # HTML parsing
    "feedparser>=6.0",       # RSS parsing
    "openai>=1.0",           # LLM API
    "supabase>=2.0",         # Database
    "resend>=2.0",           # Email sending
    "pydantic>=2.0",         # Data validation
    "pyyaml>=6.0",           # Config loading
    "python-dotenv>=1.0",    # Env loading
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
```

---

## Next Steps

1. **Set up infrastructure**
   - Create Gmail account, enable IMAP
   - Create Supabase project, run schema
   - Create Fly.io app (or just use GitHub Actions for now)

2. **Build MVP**
   - Start with 3 page sources (OpenAI, xAI, Apart Research)
   - Implement basic pipeline
   - Test email digest

3. **Add sources incrementally**
   - Email sources
   - RSS sources
   - Twitter (if RSSHub works)

4. **Iterate on scoring**
   - Tune prompts based on actual results
   - Adjust relevance thresholds
