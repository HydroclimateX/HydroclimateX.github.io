-- Generalise the WASP-only usage table into one table for every research tool,
-- discriminated by `app`. Existing WASP rows keep their data as app = 'wasp'.
-- Every statement is re-runnable: the migration runs on each deploy.

DO $$
BEGIN
    IF to_regclass('public.wasp_events') IS NOT NULL
       AND to_regclass('public.usage_events') IS NULL THEN
        ALTER TABLE wasp_events RENAME TO usage_events;
    END IF;
END
$$;

-- Migrations are re-run on every deploy, so 001 recreates an empty wasp_events
-- each time. It has already been renamed away above, so drop whatever is left.
DROP TABLE IF EXISTS wasp_events;

-- Fresh installs land here; renamed installs already have the table.
CREATE TABLE IF NOT EXISTS usage_events (
    id BIGSERIAL PRIMARY KEY,
    app TEXT NOT NULL DEFAULT 'wasp',
    event_type TEXT NOT NULL CHECK (
        event_type IN ('session_start', 'run_success', 'run_failure', 'download')
    ),
    session_hash CHAR(64) NOT NULL,
    country_code CHAR(2) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    run_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (event_type = 'session_start' OR run_id IS NOT NULL)
);

ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS app TEXT NOT NULL DEFAULT 'wasp';

-- The inherited indexes must go first: the old exact-once index coalesces run_id
-- against a uuid literal, which blocks widening the column to text.
DROP INDEX IF EXISTS wasp_events_exact_once;
DROP INDEX IF EXISTS run_outcome_once;
DROP INDEX IF EXISTS wasp_events_period_country;

-- LISFLOOD identifies a run by a 20-character job id, not a UUID.
ALTER TABLE usage_events ALTER COLUMN run_id TYPE TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'usage_events_app_check'
    ) THEN
        ALTER TABLE usage_events
            ADD CONSTRAINT usage_events_app_check CHECK (app IN ('wasp', 'lisflood'));
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS usage_events_exact_once
    ON usage_events (app, event_type, session_hash, occurred_at, COALESCE(run_id, ''));
CREATE UNIQUE INDEX IF NOT EXISTS run_outcome_once
    ON usage_events (app, run_id)
    WHERE event_type IN ('run_success', 'run_failure');
CREATE INDEX IF NOT EXISTS usage_events_period_country
    ON usage_events (app, occurred_at, country_code);
