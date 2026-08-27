CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    email TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS admin_audit (
    id BIGSERIAL PRIMARY KEY,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wasp_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('session_start', 'run_success', 'run_failure', 'download')
    ),
    session_hash CHAR(64) NOT NULL,
    country_code CHAR(2) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (event_type = 'session_start' OR run_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS wasp_events_exact_once
    ON wasp_events (event_type, session_hash, occurred_at, COALESCE(run_id, '00000000-0000-0000-0000-000000000000'::uuid));
CREATE UNIQUE INDEX IF NOT EXISTS run_outcome_once
    ON wasp_events (run_id)
    WHERE event_type IN ('run_success', 'run_failure');
CREATE INDEX IF NOT EXISTS wasp_events_period_country
    ON wasp_events (occurred_at, country_code);

CREATE TABLE IF NOT EXISTS monthly_reports (
    report_month DATE PRIMARY KEY CHECK (EXTRACT(DAY FROM report_month) = 1),
    snapshot JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('generated', 'sending', 'sent', 'failed')),
    generated_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    message_id TEXT,
    failure_code TEXT
);
