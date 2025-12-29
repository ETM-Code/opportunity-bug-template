# OpportunityBug

A personal opportunity radar that automatically monitors career pages, newsletters, and job boards to surface fellowships, internships, hackathons, and jobs that match your profile.

## Features

- **Multi-source monitoring**: Scrapes career pages (OpenAI, Anthropic, xAI, etc.) and parses email newsletters
- **AI-powered scoring**: Uses Claude to extract structured opportunity data and score relevance
- **Learning system**: Adapts to your preferences based on your ratings
- **Web UI**: Mobile-friendly interface to rate opportunities and train the system
- **Email digests**: Daily summaries of new opportunities
- **Scale to zero**: Runs on Fly.io with ~$0.50-1/month cost (only runs when needed)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         GitHub Actions                               │
│                    (Free cron scheduler)                            │
│                   Triggers: 8am, 12pm, 6pm                          │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ flyctl ssh console
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Fly.io Machine                              │
│                      (scale-to-zero VM)                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ Web Server   │  │ Batch Jobs   │  │ Playwright Browser       │  │
│  │ (FastAPI)    │  │ (CLI)        │  │ (JS-rendered pages)      │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Supabase │  │ Gmail    │  │ Resend   │
        │ (DB)     │  │ (IMAP)   │  │ (Email)  │
        └──────────┘  └──────────┘  └──────────┘
```

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/opportunity-bug.git
cd opportunity-bug

# Copy example config and customize
cp data/sources.example.yaml data/sources.yaml
# Edit data/sources.yaml with your profile and interests
```

### 2. Set up services

You'll need accounts for:
- **Supabase** (free tier): Database storage
- **Anthropic** (pay-as-you-go): AI scoring (~$0.10/day)
- **Gmail** (free): Newsletter parsing via IMAP
- **Resend** (free tier): Email digests
- **Fly.io** (pay-as-you-go): Hosting (~$0.50-1/month)

### 3. Configure secrets

Create a `.env` file for local development:

```bash
# Database
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=xxx

# AI
ANTHROPIC_API_KEY=sk-ant-xxx

# Email (for newsletter parsing)
IMAP_HOST=imap.gmail.com
IMAP_USER=your-email@gmail.com
IMAP_PASSWORD=your-app-password

# Email (for digests)
RESEND_API_KEY=re_xxx
DIGEST_TO_EMAIL=your-email@gmail.com
DIGEST_FROM_EMAIL=opportunities@yourdomain.com

# Web auth
AUTH_PASSWORD=your-secure-password
```

### 4. Deploy to Fly.io

```bash
# Install flyctl
brew install flyctl

# Login and create app
flyctl auth login
flyctl apps create opportunity-bug

# Set secrets
flyctl secrets set \
  SUPABASE_URL=xxx \
  SUPABASE_KEY=xxx \
  ANTHROPIC_API_KEY=xxx \
  IMAP_HOST=imap.gmail.com \
  IMAP_USER=xxx \
  IMAP_PASSWORD=xxx \
  RESEND_API_KEY=xxx \
  DIGEST_TO_EMAIL=xxx \
  DIGEST_FROM_EMAIL=xxx \
  AUTH_PASSWORD=xxx \
  -a opportunity-bug

# Deploy
flyctl deploy
```

### 5. Set up GitHub Actions

1. Fork this repo
2. Add `FLY_API_TOKEN` secret to your repo (get from `flyctl tokens create deploy`)
3. The scheduled pipeline will run automatically

## Local Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
playwright install chromium

# Run the CLI
python -m opportunity_radar.main run

# Run the web server
uvicorn opportunity_radar.web.app:app --reload
```

## CLI Commands

```bash
# Full pipeline (scrape + extract + score + digest)
python -m opportunity_radar.main run

# Pages only (quick check)
python -m opportunity_radar.main run --pages-only

# Emails only
python -m opportunity_radar.main run --emails-only

# Digest only (no scraping)
python -m opportunity_radar.main run --digest-only

# Initialize database schema
python -m opportunity_radar.main init
```

## Customization

Edit `data/sources.yaml` to configure:

- **User profile**: Your background, interests, and constraints
- **Page sources**: Career pages and job boards to monitor
- **Email sources**: Newsletters to parse (requires Gmail filters)
- **Scoring signals**: What makes opportunities valuable to you

## Cost Breakdown

| Service | Monthly Cost |
|---------|-------------|
| Fly.io | ~$0.50-1 (scale-to-zero) |
| Supabase | Free tier |
| Anthropic | ~$3-5 (depending on volume) |
| Resend | Free tier |
| GitHub Actions | Free |
| **Total** | **~$4-6/month** |

## License

MIT
