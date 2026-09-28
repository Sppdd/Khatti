-- Khatti data layer. Every tenant-owned row carries tenant_id and is protected by
-- Row-Level Security; the application sets khatti.tenant_id per transaction.
-- Tables without PII (tenants, api_keys, jobs) have no RLS so auth and the job queue work
-- before a tenant is known.

CREATE TABLE IF NOT EXISTS tenants (
    id          uuid PRIMARY KEY,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants (id),
    key_hash    text NOT NULL UNIQUE,  -- sha256 of the key; the key itself is shown once
    label       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz
);

CREATE TABLE IF NOT EXISTS sessions (
    id                  text PRIMARY KEY,
    tenant_id           uuid NOT NULL REFERENCES tenants (id),
    kind                text NOT NULL DEFAULT 'kyc',  -- kyc | document
    status              text NOT NULL,  -- created | submitted | processing | needs_review | completed | retake_requested | failed
    slots               jsonb NOT NULL,
    ruleset_version     text NOT NULL,
    external_ref        text,
    outcome             text,
    session_confidence  double precision,
    reasons             jsonb NOT NULL DEFAULT '[]',
    result_enc          bytea,  -- full SessionResult, envelope-encrypted (contains PII)
    final_outcome       text,   -- approve | reject after human review, or auto_pass
    error               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    submitted_at        timestamptz,
    decided_at          timestamptz
);

CREATE TABLE IF NOT EXISTS documents (
    id            text PRIMARY KEY,
    tenant_id     uuid NOT NULL REFERENCES tenants (id),
    session_id    text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    slot          text NOT NULL,
    status        text NOT NULL,  -- awaiting_upload | uploaded | ingested | superseded | deleted
    upload_key    text NOT NULL,  -- where the client PUTs the plaintext upload
    object_key    text,           -- envelope-encrypted original after ingest
    mime_type     text NOT NULL DEFAULT 'image/jpeg',
    sha256        text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);
CREATE INDEX IF NOT EXISTS documents_session_idx ON documents (session_id);

CREATE TABLE IF NOT EXISTS fields (
    id             bigserial PRIMARY KEY,
    tenant_id      uuid NOT NULL REFERENCES tenants (id),
    session_id     text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    document_id    text NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    name           text NOT NULL,
    status         text NOT NULL,
    confidence     double precision NOT NULL,
    value_enc      bytea,
    value_hmac     text,  -- keyed HMAC for lookup/dedupe of identifiers
    version        int NOT NULL DEFAULT 1,
    UNIQUE (document_id, name)
);
CREATE INDEX IF NOT EXISTS fields_hmac_idx ON fields (tenant_id, name, value_hmac) WHERE value_hmac IS NOT NULL;

CREATE TABLE IF NOT EXISTS field_versions (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants (id),
    field_id    bigint NOT NULL REFERENCES fields (id) ON DELETE CASCADE,
    version     int NOT NULL,
    status      text NOT NULL,
    value_enc   bytea,
    source      text NOT NULL,  -- model | reviewer
    actor       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS checks (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants (id),
    session_id  text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    code        text NOT NULL,
    severity    text NOT NULL,
    slot        text,
    field       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decisions (
    id                  bigserial PRIMARY KEY,
    tenant_id           uuid NOT NULL REFERENCES tenants (id),
    session_id          text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    outcome             text NOT NULL,
    session_confidence  double precision,
    reasons             jsonb NOT NULL DEFAULT '[]',
    decided_by          text NOT NULL,
    pipeline_version    text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_items (
    id          text PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants (id),
    session_id  text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    status      text NOT NULL,  -- open | closed
    reasons     jsonb NOT NULL DEFAULT '[]',
    created_at  timestamptz NOT NULL DEFAULT now(),
    closed_at   timestamptz
);
CREATE INDEX IF NOT EXISTS review_open_idx ON review_items (tenant_id, created_at) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS review_actions (
    id               bigserial PRIMARY KEY,
    tenant_id        uuid NOT NULL REFERENCES tenants (id),
    review_item_id   text NOT NULL REFERENCES review_items (id) ON DELETE CASCADE,
    actor            text NOT NULL,
    action           text NOT NULL,  -- approve | reject | request_retake
    corrections_enc  bytea,  -- per-field corrections: labelled data for evals
    retake_slots     jsonb NOT NULL DEFAULT '[]',
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS webhooks (
    id          text PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants (id),
    url         text NOT NULL,
    secret_enc  bytea NOT NULL,
    events      text[] NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Deliveries carry ids and statuses only (no PII) and are claimed across tenants: no RLS.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id               bigserial PRIMARY KEY,
    tenant_id        uuid NOT NULL REFERENCES tenants (id),
    webhook_id       text NOT NULL REFERENCES webhooks (id) ON DELETE CASCADE,
    event            text NOT NULL,
    payload          jsonb NOT NULL,  -- ids and statuses only, no PII
    status           text NOT NULL DEFAULT 'pending',  -- pending | delivered | failed
    locked_until     timestamptz,
    attempts         int NOT NULL DEFAULT 0,
    last_status      text,
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    tenant_id        uuid NOT NULL REFERENCES tenants (id),
    key              text NOT NULL,
    request_hash     text NOT NULL,
    response_status  int,
    response_body    jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE IF NOT EXISTS model_calls (
    id                 bigserial PRIMARY KEY,
    tenant_id          uuid NOT NULL REFERENCES tenants (id),
    session_id         text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    endpoint           text NOT NULL,
    model              text NOT NULL,
    purpose            text NOT NULL,
    prompt_sha256      text NOT NULL,
    image_sha256       text[] NOT NULL DEFAULT '{}',
    latency_ms         int NOT NULL,
    prompt_tokens      int,
    completion_tokens  int,
    ok                 boolean NOT NULL,
    output_enc         bytea,
    error              text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- Append-only, hash-chained audit log (per tenant).
CREATE TABLE IF NOT EXISTS audit_log (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants (id),
    session_id  text,
    at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor       text NOT NULL,
    event       text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    prev_hash   text NOT NULL,
    hash        text NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_session_idx ON audit_log (tenant_id, session_id, id);

CREATE OR REPLACE FUNCTION khatti_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END $$;
DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;
CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION khatti_audit_immutable();

-- Job queue: no PII, no RLS, so workers can claim across tenants.
CREATE TABLE IF NOT EXISTS jobs (
    id            bigserial PRIMARY KEY,
    tenant_id     uuid NOT NULL REFERENCES tenants (id),
    session_id    text NOT NULL,
    status        text NOT NULL DEFAULT 'queued',  -- queued | processing | done | failed
    attempts      int NOT NULL DEFAULT 0,
    locked_until  timestamptz,
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_pending_idx ON jobs (created_at) WHERE status IN ('queued', 'processing');

-- Row-Level Security on every tenant-owned table.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['sessions', 'documents', 'fields', 'field_versions', 'checks', 'decisions',
                             'review_items', 'review_actions', 'webhooks',
                             'idempotency_keys', 'model_calls', 'audit_log']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''khatti.tenant_id'', true), '''')::uuid) '
            'WITH CHECK (tenant_id = nullif(current_setting(''khatti.tenant_id'', true), '''')::uuid)', t);
    END LOOP;
END $$;
