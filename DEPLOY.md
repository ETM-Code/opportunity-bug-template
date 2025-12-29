# Deploying Opportunity Radar to Fly.io

Cost-optimized deployment using GitHub Actions as a free scheduler.

## Architecture

```
GitHub Actions (free cron)
    ↓ triggers
Fly.io Machine (starts → runs → stops)
    ↓ calls
FlareSolverr (auto-starts when needed)
```

**Estimated cost: ~$0.50-1/month** (vs ~$10/month for always-on)

## Prerequisites

1. Install flyctl: `brew install flyctl`
2. Login: `fly auth login`
3. Push code to GitHub (for Actions scheduler)

## Deployment Steps

### 1. Deploy FlareSolverr

```bash
cd fly-flaresolverr
fly apps create flaresolverr
fly deploy
```

### 2. Create Main App

```bash
cd ..
fly apps create opportunity-radar
```

### 3. Set Secrets

```bash
fly secrets set \
  SUPABASE_URL="https://xxx.supabase.co" \
  SUPABASE_ANON_KEY="xxx" \
  SUPABASE_SERVICE_KEY="xxx" \
  IMAP_HOST="imap.gmail.com" \
  IMAP_PORT="993" \
  IMAP_USERNAME="xxx@gmail.com" \
  IMAP_PASSWORD="xxx" \
  OPENAI_API_KEY="sk-xxx" \
  RESEND_API_KEY="re_xxx" \
  DIGEST_RECIPIENT="you@email.com"
```

### 4. Deploy

```bash
fly deploy
```

### 5. Set Up GitHub Actions

Add `FLY_API_TOKEN` to your GitHub repo secrets:

```bash
# Generate token
fly tokens create deploy -x 999999h

# Add to GitHub: Settings → Secrets → Actions → New secret
# Name: FLY_API_TOKEN
# Value: <paste token>
```

The workflow in `.github/workflows/schedule.yml` will now run automatically:
- **8am UTC**: Full pipeline (pages + emails + digest)
- **Noon & 6pm UTC**: Pages only check

### 6. Test Manually

Trigger a run from GitHub Actions → "Scheduled Pipeline" → Run workflow

Or locally:
```bash
fly machines start <machine-id> --app opportunity-radar
fly logs --app opportunity-radar
```

## Schedule

| Time (UTC) | Task | Duration |
|------------|------|----------|
| 08:00 | Full pipeline + digest | ~10 min |
| 12:00 | Pages only | ~5 min |
| 18:00 | Pages only | ~5 min |

Total runtime: ~20 min/day = **~$0.50/month**

## Commands

```bash
# View status
fly status --app opportunity-radar

# View logs
fly logs --app opportunity-radar

# Run manually
fly ssh console --app opportunity-radar
python -m opportunity_radar.main run

# Check FlareSolverr
fly status --app flaresolverr
```

## Updating

```bash
# Deploy new code
fly deploy --app opportunity-radar

# Update sources config
fly ssh console --app opportunity-radar
python -m opportunity_radar.main init
```
