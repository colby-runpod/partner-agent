CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS settings(id integer PRIMARY KEY CHECK(id=1), paused boolean NOT NULL DEFAULT false);
INSERT INTO settings(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS candidates(
 id uuid PRIMARY KEY, name text NOT NULL, domain text NOT NULL UNIQUE, website text NOT NULL,
 segment text NOT NULL, brief text NOT NULL, notes text NOT NULL DEFAULT '',
 stage text NOT NULL DEFAULT 'new' CHECK(stage IN ('new','researching','review','accepted','rejected','nurture','blocked')),
 suppressed boolean NOT NULL DEFAULT false, existing_relationship boolean NOT NULL DEFAULT false,
 version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS jobs(
 id uuid PRIMARY KEY, candidate_id uuid NOT NULL REFERENCES candidates(id),
 mode text NOT NULL CHECK(mode IN ('live','fixture')), state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','done','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0, lease_until timestamptz, lease_token uuid,
 checkpoint jsonb, result jsonb, error text, usage jsonb, budget_reserved numeric NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(candidate_id) WHERE state IN ('queued','running');
CREATE TABLE IF NOT EXISTS assessments(
 id uuid PRIMARY KEY, candidate_id uuid NOT NULL REFERENCES candidates(id), job_id uuid NOT NULL UNIQUE REFERENCES jobs(id),
 mode text NOT NULL, rubric_version text NOT NULL DEFAULT 'hypothesis-v1', evidence jsonb NOT NULL,
 recommendation jsonb NOT NULL, score jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS reviews(
 id uuid PRIMARY KEY, candidate_id uuid NOT NULL REFERENCES candidates(id), assessment_id uuid REFERENCES assessments(id),
 action text NOT NULL, comment text NOT NULL, actor text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS audit(
 id bigserial PRIMARY KEY, candidate_id uuid REFERENCES candidates(id), actor text NOT NULL,
 action text NOT NULL, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());
