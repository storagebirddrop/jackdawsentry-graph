-- Migration 007: graph_sessions table
--
-- Persists investigation graph sessions so that investigators can restore
-- their session after a page refresh or browser crash.
--
-- Each row corresponds to one TraceCompiler session (create_session call).
-- The frontend snapshot (node positions, visible nodes, filter state) is
-- stored in the snapshot JSONB column via the snapshot endpoint.

CREATE TABLE IF NOT EXISTS graph_sessions (
    session_id      UUID        PRIMARY KEY,
    seed_address    TEXT        NOT NULL,
    seed_chain      TEXT        NOT NULL,
    case_id         TEXT,                           -- optional link to compliance case
    created_by      TEXT,                           -- username / user_id from JWT
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    snapshot        JSONB,                          -- last saved frontend snapshot
    snapshot_saved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS graph_sessions_seed_address_idx
    ON graph_sessions (seed_address, seed_chain);

CREATE INDEX IF NOT EXISTS graph_sessions_created_by_idx
    ON graph_sessions (created_by);

CREATE INDEX IF NOT EXISTS graph_sessions_created_at_idx
    ON graph_sessions (created_at DESC);

-- Auto-update updated_at on any row modification.
CREATE OR REPLACE FUNCTION _touch_graph_session()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_graph_session_updated_at ON graph_sessions;
CREATE TRIGGER trg_graph_session_updated_at
    BEFORE UPDATE ON graph_sessions
    FOR EACH ROW EXECUTE FUNCTION _touch_graph_session();
