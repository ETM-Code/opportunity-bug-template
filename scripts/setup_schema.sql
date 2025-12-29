-- Opportunity Radar - Database Schema
-- Run this in Supabase SQL Editor or via API

-- Enable pgvector extension (for future embedding support)
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- SOURCES TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT NOT NULL,  -- 'page', 'email', 'rss'
    priority TEXT NOT NULL DEFAULT 'medium',  -- 'critical', 'high', 'medium', 'low'
    tags TEXT[] NOT NULL DEFAULT '{}',
    config JSONB NOT NULL DEFAULT '{}',
    active BOOLEAN NOT NULL DEFAULT true,
    last_checked_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- SEEN ITEMS (for deduplication)
-- =============================================================================
CREATE TABLE IF NOT EXISTS seen_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash TEXT NOT NULL UNIQUE,
    source_id UUID REFERENCES sources(id) ON DELETE CASCADE,
    url TEXT,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_seen_items_hash ON seen_items(content_hash);

-- =============================================================================
-- OPPORTUNITIES
-- =============================================================================
CREATE TABLE IF NOT EXISTS opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES sources(id) ON DELETE SET NULL,

    -- Core fields
    title TEXT NOT NULL,
    organization TEXT,
    url TEXT NOT NULL,
    application_url TEXT,

    -- Details
    type TEXT,  -- 'residency', 'hackathon', 'fellowship', 'job', 'grant', 'internship'
    deadline TIMESTAMPTZ,
    stipend_amount NUMERIC,
    stipend_currency TEXT DEFAULT 'USD',
    travel_support TEXT,  -- 'none', 'partial', 'full', 'unknown'
    location TEXT,
    is_remote BOOLEAN,
    eligibility TEXT,

    -- LLM-generated
    summary TEXT,
    relevance_score FLOAT,  -- 0-1
    prestige_score FLOAT,   -- 0-1
    highlights TEXT[],      -- Key selling points

    -- Metadata
    raw_content TEXT,
    content_hash TEXT,
    notified_at TIMESTAMPTZ,  -- When we emailed about this
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_opportunities_deadline ON opportunities(deadline);
CREATE INDEX IF NOT EXISTS idx_opportunities_notified ON opportunities(notified_at);
CREATE INDEX IF NOT EXISTS idx_opportunities_relevance ON opportunities(relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_url ON opportunities(url);

-- =============================================================================
-- RAW EMAILS (for IMAP idempotency)
-- =============================================================================
CREATE TABLE IF NOT EXISTS raw_emails (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES sources(id) ON DELETE CASCADE,
    gmail_msg_id TEXT NOT NULL,
    gmail_thread_id TEXT,
    subject TEXT,
    sender TEXT,
    received_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'processed', 'failed', 'skipped'
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(source_id, gmail_msg_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_emails_status ON raw_emails(status);

-- =============================================================================
-- USER PROFILE (stored for reference)
-- =============================================================================
CREATE TABLE IF NOT EXISTS user_profile (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    background TEXT,
    interests TEXT[],
    constraints JSONB,
    high_value_signals TEXT[],
    low_value_signals TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- DIGEST LOG (track what we've sent)
-- =============================================================================
CREATE TABLE IF NOT EXISTS digest_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    opportunity_count INT NOT NULL,
    opportunity_ids UUID[] NOT NULL,
    email_subject TEXT,
    status TEXT NOT NULL DEFAULT 'sent',  -- 'sent', 'failed'
    error_message TEXT
);

-- =============================================================================
-- HELPER FUNCTIONS
-- =============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
DROP TRIGGER IF EXISTS update_sources_updated_at ON sources;
CREATE TRIGGER update_sources_updated_at
    BEFORE UPDATE ON sources
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_opportunities_updated_at ON opportunities;
CREATE TRIGGER update_opportunities_updated_at
    BEFORE UPDATE ON opportunities
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_user_profile_updated_at ON user_profile;
CREATE TRIGGER update_user_profile_updated_at
    BEFORE UPDATE ON user_profile
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- INITIAL DATA: Insert user profile
-- =============================================================================
-- User profile is loaded from data/sources.yaml at runtime
-- No default data needed here - configure your profile in sources.yaml

-- Done!
SELECT 'Schema created successfully!' as status;
