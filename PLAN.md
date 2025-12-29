# Opportunities Radar - Design & Implementation Plan

## 1. What We're Building

**One-liner:** A personal agent that continuously scans high-signal sources for fellowships, residencies, hackathons, and "crazy jobs", normalises them into a structured database, and helps you discover the best ones at the right time while reusing your past application answers.

### Core Value Proposition
- Never miss things like xAI hackathons, ETHZ SSRF, YC Summer Fellows
- See the best opportunities early, ranked by your fit (AI, hardware, founder, Europe, stipend, etc.)
- Reuse your application writing instead of rewriting "tell us about yourself" for the 48th time

### Target Opportunities
- AI residencies (OpenAI, Google, Meta, Anthropic, etc.)
- Research fellowships (ETH Zurich, university programmes)
- Hackathons with serious prizes/sponsors
- Founder programmes (YC, Antler, 776 Fellowship)
- Deep-tech grants and funding calls
- "Crazy job" offers with stipends and travel support

---

## 2. System Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        VPS Deployment                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │
│  │   Frontend   │────▶│   Backend    │────▶│   Worker     │   │
│  │   (Next.js)  │     │   (FastAPI)  │     │  (Periodic)  │   │
│  └──────────────┘     └──────────────┘     └──────────────┘   │
│         │                    │                    │            │
│         │                    ▼                    │            │
│         │             ┌──────────────┐           │            │
│         └────────────▶│  PostgreSQL  │◀──────────┘            │
│                       │  + pgvector  │                        │
│                       └──────────────┘                        │
│                              │                                 │
│                              ▼                                 │
│                       ┌──────────────┐                        │
│                       │  LLM APIs    │                        │
│                       │  (OpenAI)    │                        │
│                       └──────────────┘                        │
│                                                                │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Purpose |
|-----------|---------|
| **Source Connectors** | Email newsletters, curated websites, meta-lists. All exposed through a common `Source` interface. |
| **Normalisation Pipeline** | Turn messy emails/pages into clean, structured `Opportunity` objects with deadlines, stipend, travel support, type, tags, prestige, relevance scores. |
| **Storage & Retrieval** | Postgres (+ pgvector) database of opportunities, sources, forms, and saved answers. Query layer combines filters, vector search, and ranking. |
| **UX Layer** | Web dashboard + chat interface for queries; form parser for pasting applications and saving reusable answer blocks. |

---

## 3. Repository Structure

```
opps-radar/
├── common/
│   ├── llm/
│   │   └── client.py          # Unified LLM wrapper (nano/mini/full)
│   ├── models/
│   │   ├── opportunities.py   # Pydantic models
│   │   └── forms.py           # Form & answer models
│   └── config.py
│
├── backend/                    # FastAPI
│   ├── api/
│   │   ├── opportunities.py   # REST listing, filters
│   │   ├── chat.py            # Chat endpoint
│   │   ├── forms.py           # Paste form, autofill
│   │   └── admin_sources.py   # Manage sources
│   ├── services/
│   │   ├── opportunity_query.py
│   │   └── forms.py
│   ├── sources/
│   │   ├── base.py
│   │   ├── email_newsletter.py
│   │   ├── static_site.py
│   │   └── meta_list.py
│   └── db/
│       ├── schema.sql
│       └── repositories.py
│
├── worker/
│   ├── pipelines/
│   │   └── ingest.py          # Main ingestion pipeline
│   ├── jobs/
│   │   └── run_sources.py     # Called by scheduler
│   └── main.py
│
├── frontend/                   # Next.js app
│   ├── app/
│   │   ├── dashboard/
│   │   ├── chat/
│   │   ├── opportunities/
│   │   └── forms/
│   └── lib/
│       └── api.ts
│
├── infra/
│   ├── docker-compose.yml
│   ├── nginx.conf
│   └── deploy.sh
│
└── data/
    └── sources.seed.yaml      # Initial source definitions
```

---

## 4. Database Schema

### Core Tables

```sql
-- Sources of opportunities
CREATE TABLE opportunity_sources (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL,  -- 'email_newsletter', 'static_site', 'meta_list'
    priority        TEXT NOT NULL,  -- 'A', 'B', 'C'
    tags            TEXT[] NOT NULL DEFAULT '{}',
    config          JSONB NOT NULL, -- Source-specific settings
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    last_fetched_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The actual opportunities
CREATE TABLE opportunities (
    id                  SERIAL PRIMARY KEY,
    source_id           INT REFERENCES opportunity_sources(id),
    title               TEXT NOT NULL,
    organisation        TEXT,
    url                 TEXT NOT NULL UNIQUE,
    application_url     TEXT,
    raw_content         TEXT,
    summary             TEXT,
    type                TEXT,  -- 'hackathon', 'fellowship', 'residency', 'job', 'grant'
    domain_tags         TEXT[] DEFAULT '{}',
    location            TEXT,
    is_remote           BOOLEAN,
    deadline            TIMESTAMPTZ,
    start_date          TIMESTAMPTZ,
    end_date            TIMESTAMPTZ,
    stipend_amount      NUMERIC,
    stipend_currency    TEXT,
    travel_support      TEXT,  -- 'none', 'partial', 'full', 'unknown'
    prize_pool          NUMERIC,
    eligibility         TEXT,
    prestige_score      FLOAT,  -- 0-1
    relevance_score     FLOAT,  -- 0-1
    content_hash        TEXT,   -- For deduplication
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Embeddings for semantic search
CREATE TABLE opportunity_embeddings (
    id              SERIAL PRIMARY KEY,
    opportunity_id  INT REFERENCES opportunities(id) ON DELETE CASCADE,
    embedding       vector(1536),
    model_name      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Raw emails for idempotency tracking
CREATE TABLE raw_emails (
    id              SERIAL PRIMARY KEY,
    source_id       INT REFERENCES opportunity_sources(id),
    gmail_msg_id    TEXT NOT NULL,
    gmail_thread_id TEXT,
    received_at     TIMESTAMPTZ,
    processed_at    TIMESTAMPTZ,
    status          TEXT NOT NULL,  -- 'pending', 'processed', 'failed'
    error_message   TEXT,
    UNIQUE(source_id, gmail_msg_id)
);

-- Application forms structure
CREATE TABLE application_forms (
    id                  SERIAL PRIMARY KEY,
    opportunity_id      INT REFERENCES opportunities(id),
    raw_form_text       TEXT NOT NULL,
    normalised_schema   JSONB,  -- Array of fields with types & labels
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reusable answers
CREATE TABLE user_answers (
    id              SERIAL PRIMARY KEY,
    form_id         INT REFERENCES application_forms(id),
    question_key    TEXT,           -- Canonical key like "motivation"
    question_text   TEXT NOT NULL,
    answer_text     TEXT NOT NULL,
    tags            TEXT[] DEFAULT '{}',
    embedding       vector(1536),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Indexes
CREATE INDEX idx_opportunities_deadline ON opportunities(deadline);
CREATE INDEX idx_opportunities_type ON opportunities(type);
CREATE INDEX idx_opportunities_relevance ON opportunities(relevance_score DESC);
CREATE INDEX idx_opportunity_embeddings_vector ON opportunity_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

---

## 5. LLM Strategy

### Model Roles

| Model | Thinking | Use Cases |
|-------|----------|-----------|
| **gpt-5-nano** | Low effort | Email classification, structured extraction, quick yes/no filters, multi-label tagging |
| **gpt-5-mini** | Default | Summarisation, relevance scoring, prestige estimation, drafting tailored answers from past answers |
| **gpt-5** | Default | Chat endpoint, composing explanations, heavy RAG prompts for application essays |

### LLM Client Abstraction

All LLM usage goes through a single wrapper so model changes are a one-file fix:

```python
# common/llm/client.py
from typing import Literal

Model = Literal["nano", "mini", "full"]

class LLMClient:
    def _resolve(self, logical: Model) -> str:
        return {
            "nano": "gpt-5.1-nano",
            "mini": "gpt-5.1-mini",
            "full": "gpt-5.1"
        }[logical]

    async def classify(self, text: str, task: str, model: Model = "nano") -> dict:
        ...

    async def extract_json(self, text: str, schema: dict, model: Model = "nano") -> dict:
        ...

    async def enrich_opportunity(self, opp: dict, user_profile: str, model: Model = "mini") -> dict:
        ...

    async def chat(self, messages: list[dict], model: Model = "full") -> str:
        ...
```

### Cost Control Tactics

1. **Text pre-processing**: Strip boilerplate, truncate to ~6000 chars before sending
2. **Model choice enforcement**: Classification/extraction = nano only; enrichment = mini only; chat = full only
3. **Batching**: Batch embeddings and classifications where possible
4. **Caching**: Content-hash based deduplication prevents reprocessing

---

## 6. Data Sources

### Email Strategy

**Dedicated Gmail account**: `yourname.opps@gmail.com`
- 2FA enabled, App Password for IMAP access
- Gmail labels per source (e.g., `src/deeptech`, `src/scoutlight`)
- Worker tracks by `gmail_msg_id` for idempotency

### Tier A — Must-Subscribe (Core Firehoses)

| Source | Type | What It Covers |
|--------|------|----------------|
| **Deep Tech Now / DTM** | Newsletter | Deep-tech funding calls, fellowships, grants, founder programmes |
| **Scoutlight (Scouthappy)** | Newsletter | Global remote opportunities, 776 Fellowship, Antler cohorts |
| **Apart Research** | Newsletter + Web | AI safety hackathons, research sprints, fellowship announcements |
| **TechAways** | Newsletter + Web | Fellowships, hackathons, internships for students |
| **80,000 Hours** | Email digest + Web | High-impact AI safety, policy, research, founding team roles |

### Tier B — High-Utility (Needs Classifier)

| Source | Type | What It Covers |
|--------|------|----------------|
| **Intern Insider** | Newsletter | Internships, hackathons, tech case competitions |
| **ProFellow Insider** | Newsletter + Web | Fellowships, grants, scholarships, research programmes |

### Tier C — Broad Aggregators (Volume, Low Signal)

| Source | Type | What It Covers |
|--------|------|----------------|
| **Opportunity Desk** | Newsletter + Web | Massive global aggregator, requires strict filtering |
| **Scholarship Track** | Newsletter | Scholarships, fellowships, mostly undergraduate |

### Web-Only Sources (Static Site Scrapers)

| Source | URL | Notes |
|--------|-----|-------|
| Apart Research Events | `apartresearch.com/events` | All upcoming hackathons + sprints |
| 80k Hours Job Board | `80000hours.org/job-board/` | Filter by AI/software |
| ProFellow Open Calls | `profellow.com/open-calls/` | Current fellowship deadlines |
| TechAways Hub | `tech-aways.vercel.app/` | Programme listings |

### ⚠️ Sources to Avoid/Treat Carefully

| Source | Issue |
|--------|-------|
| **awesome-ai-residency (GitHub)** | Last updated April 2025 (~9 months stale). Use as seed for programme discovery only, not as live feed. |
| **Random job boards** | Too noisy, low signal |
| **Twitter/LinkedIn scraping** | Legal issues, brittleness, high noise |

---

## 7. Ingestion Pipeline

### Flow Diagram

```
┌─────────────────┐
│  Source Config  │
│  (DB row)       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Connector      │ ─────▶ EmailNewsletterSource
│  Factory        │ ─────▶ StaticSiteSource
└────────┬────────┘ ─────▶ MetaListSource
         │
         ▼
┌─────────────────┐
│  fetch_raw_     │
│  items()        │
└────────┬────────┘
         │
         ▼ (yields RawItem[])
┌─────────────────┐
│  Deduplication  │ ─── seen_before(content_hash)?
└────────┬────────┘
         │ (new items only)
         ▼
┌─────────────────┐
│  Classifier     │ ─── gpt-5-nano: "is this an opportunity?"
│  (nano)         │
└────────┬────────┘
         │ (opportunities only)
         ▼
┌─────────────────┐
│  Extraction     │ ─── gpt-5-nano: JSON extraction
│  (nano)         │     Returns 0-N opportunities per item
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Enrichment     │ ─── gpt-5-mini: summary, prestige, relevance
│  (mini)         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Store + Embed  │ ─── Insert to DB, create embedding
└─────────────────┘
```

### Pipeline Code Structure

```python
# worker/pipelines/ingest.py

async def ingest_source(source_row, db, llm: LLMClient):
    source = source_factory(source_row)
    raw_items = await source.fetch_raw_items()

    for raw in raw_items:
        if seen_before(raw, db):
            continue

        if not await is_probably_opportunity(raw, llm):  # nano
            continue

        opp_dicts = await extract_opportunities(raw, llm)  # nano, returns list

        for opp_dict in opp_dicts:
            opp = Opportunity(**opp_dict)
            opp = await enrich_opportunity(opp, llm)  # mini
            save_opportunity(opp, db)
            create_embedding(opp, db)
```

---

## 8. Query & Chat System

### Query Service

```python
# backend/services/opportunity_query.py

class OpportunityQueryService:
    def search(
        self,
        text_query: str | None,
        time_window_days: int | None,
        filters: dict,  # stipend_min, travel_support, tags, etc.
        limit: int = 20
    ) -> list[Opportunity]:
        # 1. Build SQL WHERE on deadlines, stipends, travel_support, region
        # 2. If text_query: vector search via pgvector, get candidate IDs
        # 3. Intersect with SQL filters
        # 4. Rank: 0.5*relevance + 0.3*prestige + 0.2*urgency
        # 5. Return top N
        ...
```

### Chat Endpoint Flow

1. Receive natural language query
2. Use nano/mini to parse into: intent + structured filters
3. Call `OpportunityQueryService` with filters
4. Pass top N opportunities to gpt-5 with system prompt
5. Return formatted response with links

---

## 9. Forms & Application Memory

### Form Parsing Flow

1. User pastes raw form text
2. LLM (mini) extracts schema: `[{key, label, type, is_required, suggested_answer_key}]`
3. Frontend renders clean form UI
4. User fills it out
5. Each answer saved to `user_answers` with embedding

### Auto-Fill Flow

1. For each field in new form
2. Vector search `user_answers` by question similarity + tags
3. If match found: propose reuse or draft tailored answer via mini
4. User reviews, edits, saves as new reusable block

---

## 10. Deployment

### Docker Compose Services

```yaml
services:
  api:
    build: ./backend
    ports: ["8000:8000"]
    depends_on: [db, redis]

  worker:
    build: ./worker
    depends_on: [db, redis]

  frontend:
    build: ./frontend
    ports: ["3000:3000"]

  db:
    image: pgvector/pgvector:pg16
    volumes: ["pgdata:/var/lib/postgresql/data"]

  redis:
    image: redis:7-alpine

  nginx:
    image: nginx:alpine
    ports: ["80:80", "443:443"]
```

### Scheduling

| Task | Frequency | Method |
|------|-----------|--------|
| Email newsletters | Every 10 min | cron / celery beat |
| RSS feeds | Every 30 min | cron / celery beat |
| Static sites | Every 1-6 hours | cron / celery beat |
| Re-scoring | Daily | cron |

---

## 11. Implementation Phases

### Phase 1: Skeleton & DB
- [ ] Set up repo structure
- [ ] FastAPI backend scaffold
- [ ] PostgreSQL + pgvector setup
- [ ] Core tables (opportunities, sources, raw_emails)
- [ ] LLM client wrapper with stub calls
- [ ] Simple dashboard listing manually inserted opportunities

### Phase 2: Core Ingestion (3-5 Key Sources)
- [ ] Email connector with IMAP
- [ ] Connect to dedicated Gmail mailbox
- [ ] StaticSiteSource for 2-3 priority sites
- [ ] Full ingestion pipeline (classify → extract → enrich → store)
- [ ] Deduplication logic
- [ ] Verify real opportunities appearing in DB

### Phase 3: Chat + Ranking
- [ ] Intent parsing for chat queries
- [ ] OpportunityQueryService with filters + vector search
- [ ] gpt-5 chat endpoint
- [ ] Ranking formula tuning with real data
- [ ] Chat UI in frontend

### Phase 4: Forms & Answer Memory
- [ ] Form parsing endpoint + UI
- [ ] user_answers storage + embeddings
- [ ] "Suggest answers" auto-fill feature
- [ ] Test with real application form

### Phase 5: Polish & Scale
- [ ] Add remaining Tier A/B sources
- [ ] Monitoring dashboard (items/day, sources health)
- [ ] Alerting for stale sources
- [ ] Tune classifiers and filters based on noise

---

## 12. Gmail Setup Checklist

### Account Setup
- [ ] Create `yourname.opps@gmail.com`
- [ ] Enable 2FA
- [ ] Create App Password for IMAP
- [ ] Store app password in VPS env: `OPPS_IMAP_PASSWORD`

### Labels to Create
```
src/deeptech
src/scoutlight
src/apart
src/techaways
src/80k
src/interninsider
src/profellow
src/opportunitydesk
```

### Gmail Filters (per newsletter)
For each source, create filter:
- Match: `From:` or `List-Id:` header
- Action: Apply label, Never spam, Mark important

### Newsletter Subscriptions
Subscribe the Gmail address to:
1. Deep Tech Now — `deeptech.build`
2. Scoutlight — `scouthappy.com`
3. Apart Research — `apartresearch.com`
4. TechAways — `tech-aways.vercel.app`
5. 80,000 Hours — `80000hours.org/job-board/`
6. Intern Insider — `interninsider.me`
7. ProFellow — `profellow.com`
8. Opportunity Desk — `opportunitydesk.org`

---

## 13. Open Questions / Decisions Needed

- [ ] **Gmail vs custom domain**: Use `yourname.opps@gmail.com` or set up `opps@yourdomain.com`?
- [ ] **Model costs**: Estimated monthly token usage for nano/mini/full?
- [ ] **User profile spec**: What exactly defines "relevant to you"? (AI, hardware, Europe, stipend threshold, etc.)
- [ ] **Priority sources**: Final approval on Tier A list before subscribing?
- [ ] **VPS specs**: 2-4 vCPU, 4-8GB RAM sufficient?

---

## 14. Quick Reference: Source Seed Config

```yaml
# data/sources.seed.yaml

- name: "Deep Tech Now / DTM"
  type: "email_newsletter"
  priority: "A"
  tags: ["deeptech", "ai", "startup", "funding"]
  config:
    gmail_label: "src/deeptech"
    sender_whitelist:
      - "hello@deeptech.build"
      - "newsletter@deeptech.build"

- name: "Apart Research Events"
  type: "static_site"
  priority: "A"
  tags: ["ai", "safety", "hackathon", "fellowship"]
  config:
    url: "https://www.apartresearch.com/events"
    list_selector: "a[href*='/events/']"
    title_selector: "h2, h3"

- name: "80,000 Hours Job Board"
  type: "static_site"
  priority: "A"
  tags: ["ai", "safety", "policy", "research", "job"]
  config:
    url: "https://80000hours.org/job-board/"
    list_selector: "article, li.job-listing"
    title_selector: "h2, h3, a"

# ... (see full sources.seed.yaml for complete list)
```

---

*Last updated: 2025-12-21*
