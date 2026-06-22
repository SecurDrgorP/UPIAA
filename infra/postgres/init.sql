-- ═══════════════════════════════════════════════════════════════════════════════
-- M8M PostgreSQL Initialization
-- Creates extensions and initial schema. Alembic manages migrations after this.
-- ═══════════════════════════════════════════════════════════════════════════════

-- Enable useful extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Workflows ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workflows (
    id          VARCHAR(64)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL DEFAULT 'Untitled Workflow',
    is_published BOOLEAN     NOT NULL DEFAULT FALSE,
    dag         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workflows_updated ON workflows (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflows_published ON workflows (is_published) WHERE is_published = TRUE;

-- ── Executions ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS executions (
    id              VARCHAR(64)  PRIMARY KEY,
    workflow_id     VARCHAR(64)  NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    state           VARCHAR(32)  NOT NULL DEFAULT 'pending',
    logs            JSONB        NOT NULL DEFAULT '[]'::jsonb,
    context         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    temporal_run_id VARCHAR(128)
);

CREATE INDEX IF NOT EXISTS idx_executions_workflow ON executions (workflow_id);
CREATE INDEX IF NOT EXISTS idx_executions_state ON executions (state);
CREATE INDEX IF NOT EXISTS idx_executions_started ON executions (started_at DESC);

-- ── Secrets (encrypted at rest via Fernet in the application layer) ──────────
CREATE TABLE IF NOT EXISTS secrets (
    id          SERIAL       PRIMARY KEY,
    key_name    VARCHAR(128) NOT NULL UNIQUE,
    cipher_text TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_secrets_key ON secrets (key_name);

-- ── Credentials (encrypted JSON bundles for node injection) ─────────────────
CREATE TABLE IF NOT EXISTS credentials (
    id          VARCHAR(64)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    provider    VARCHAR(64)  NOT NULL,
    cipher_text TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credentials_provider ON credentials (provider);

-- ── Hermies: Skills ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hermies_skills (
    id          SERIAL       PRIMARY KEY,
    name        VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    parameters  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    workflow_id VARCHAR(64)  REFERENCES workflows(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Hermies: User Model ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hermies_user_models (
    id              SERIAL       PRIMARY KEY,
    user_id         VARCHAR(128) NOT NULL,
    preferences     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    interaction_log JSONB        NOT NULL DEFAULT '[]'::jsonb,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_hermies_user ON hermies_user_models (user_id);

-- ── Token Usage Tracking ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS token_usage (
    id             SERIAL       PRIMARY KEY,
    execution_id   VARCHAR(64)  REFERENCES executions(id) ON DELETE CASCADE,
    node_id        VARCHAR(64)  NOT NULL,
    model          VARCHAR(128) NOT NULL,
    input_tokens   INTEGER      NOT NULL DEFAULT 0,
    output_tokens  INTEGER      NOT NULL DEFAULT 0,
    cost_usd       NUMERIC(10,6) NOT NULL DEFAULT 0,
    duration_ms    INTEGER      NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_token_usage_exec ON token_usage (execution_id);

-- ── Triggers ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS triggers (
    id          SERIAL       PRIMARY KEY,
    workflow_id VARCHAR(64)  NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    trigger_type VARCHAR(32) NOT NULL,  -- 'webhook', 'schedule', 'event'
    config      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_triggers_workflow ON triggers (workflow_id);
CREATE INDEX IF NOT EXISTS idx_triggers_active ON triggers (is_active) WHERE is_active = TRUE;
