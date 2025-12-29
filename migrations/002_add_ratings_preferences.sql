-- Migration: Add rating and preference learning tables
-- Run this in Supabase SQL Editor

-- User ratings on opportunities (5-point scale)
CREATE TABLE IF NOT EXISTS opportunity_ratings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    rating SMALLINT NOT NULL CHECK (rating >= 1 AND rating <= 5),
    feedback TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (opportunity_id)
);

-- Learned signal weights (adjusted based on ratings)
CREATE TABLE IF NOT EXISTS learned_signal_weights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_name TEXT NOT NULL UNIQUE,
    signal_type TEXT NOT NULL CHECK (signal_type IN ('high_value', 'low_value')),
    weight REAL DEFAULT 1.0,
    sample_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Few-shot examples for scoring prompt
CREATE TABLE IF NOT EXISTS scoring_examples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID REFERENCES opportunities(id) ON DELETE SET NULL,
    example_text TEXT NOT NULL,
    user_rating SMALLINT NOT NULL CHECK (user_rating >= 1 AND user_rating <= 5),
    token_count INTEGER NOT NULL,
    is_condensed BOOLEAN DEFAULT FALSE,
    priority REAL DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    category TEXT GENERATED ALWAYS AS (
        CASE
            WHEN user_rating >= 4 THEN 'good'
            WHEN user_rating <= 2 THEN 'bad'
            ELSE 'neutral'
        END
    ) STORED
);

-- Track condensation events
CREATE TABLE IF NOT EXISTS example_condensation_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    condensed_at TIMESTAMPTZ DEFAULT NOW(),
    examples_before INTEGER,
    examples_after INTEGER,
    tokens_before INTEGER,
    tokens_after INTEGER,
    llm_model TEXT
);

-- Add user_rating column to opportunities table if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'opportunities' AND column_name = 'user_rating'
    ) THEN
        ALTER TABLE opportunities
        ADD COLUMN user_rating SMALLINT CHECK (user_rating >= 1 AND user_rating <= 5);
    END IF;
END $$;

-- Add indexes for performance
CREATE INDEX IF NOT EXISTS idx_ratings_opportunity ON opportunity_ratings(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_examples_category ON scoring_examples(category);
CREATE INDEX IF NOT EXISTS idx_examples_priority ON scoring_examples(priority DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_user_rating ON opportunities(user_rating) WHERE user_rating IS NOT NULL;

-- Enable RLS (Row Level Security) - disabled for single-user app
-- If you want multi-user support later, enable RLS and add policies
