# PartnerOS · Partner Development pilot

First complete slice: company intake → durable research job → evidence-backed recommendation → human review. Existing Python Slack code and its root Dockerfile are unchanged. This is a development pilot, not the full production-ready system in the product brief.

## Architecture

Node 24 / TypeScript, server-rendered authenticated web UI, a separate worker, and Postgres. `src/server.ts` owns forms and permissions; `src/db.ts` owns workflow transitions and SQL; `src/research.ts` contains fixed-endpoint Tavily and Anthropic adapters. Postgres jobs use leases and `FOR UPDATE SKIP LOCKED`. No Redis is needed for this bounded first release. The model never receives executable business-action tools.

The worker checkpoints evidence before analysis and reserves estimated cost before external calls. On a crash, expired jobs can resume up to three attempts using a new lease token. Stale workers cannot publish. Completed assessments are unique per job. Timeouts during research may consume provider credits; reserved cost persists. Failed runs require an explicit new research request. There are no external business writes to reconcile in this release.

## Local setup

Requires Node 24+, pnpm 11.19.0, and Postgres 16+.

```sh
pnpm install --frozen-lockfile --ignore-scripts
cp .env.example .env
```

Set the database, origin, session secret and reviewer password hash. Generate a password hash without placing the password in command history:

```sh
read -s PARTNEROS_PASSWORD
export PARTNEROS_PASSWORD
node --input-type=module -e 'import {passwordHash} from "./src/auth.ts"; console.log(passwordHash(process.env.PARTNEROS_PASSWORD))'
unset PARTNEROS_PASSWORD
node --input-type=module -e 'import {randomBytes} from "node:crypto"; console.log(randomBytes(32).toString("hex"))'
```

The first output is REVIEWER_PASSWORD_HASH; the second is SESSION_SECRET. Use a unique password, not your Slack/Notion password. This pilot authenticates one reviewer, Colby; do not share this login as a substitute for team roles. For localhost set APP_ORIGIN=http://localhost:3000 and NODE_ENV=development. Secrets are loaded explicitly:

```sh
node --env-file=.env src/migrate.ts
node --env-file=.env src/server.ts
# In another terminal:
node --env-file=.env src/worker.ts
```

Open localhost:3000. For a no-credentials demonstration, set DEMO_MODE=true in a development environment. Create a candidate named “Example Inference · Demo” with website example.com and select “Run invented demo.” It uses invented evidence, visibly labeled throughout. This does not demonstrate actual company research.

## Railway deployment (separate from the Slack service)

1. Keep the current `partner-agent` Slack service and its root directory/start command as they are.
2. Create a separate development environment and Postgres database. Use Railway's private DATABASE_URL for PartnerOS services. Production must use a different database and secrets. Do not enable public Postgres networking for the application.
3. Add two services from the same repo, selecting branch `codex/partneros-phase-one` for this pilot, each with Root Directory `/partneros`. The web start command is `node src/server.ts`; worker is `node src/worker.ts`. Keep one worker replica initially. Use `/partneros/railway.web.json` and `/partneros/railway.worker.json` as their respective config-as-code paths (Railway paths are repository-relative; confirm the rendered build settings show the Dockerfile inside the root directory).
4. On web, run `node src/migrate.ts` as the pre-deploy command and use `/healthz` for the health check. Complete the first web migration before starting the worker. Migration runs inside a transaction and takes an advisory lock. It is additive; it does not touch the existing Slack SQLite files.
5. Generate a public HTTPS domain for web only. Set APP_ORIGIN to exactly that origin, without a trailing slash, and port to the Railway PORT value (default 3000). Worker needs no public domain. The web health endpoint checks database access; it is not a worker/provider health guarantee.
6. Web needs DATABASE_URL, APP_ORIGIN, SESSION_SECRET, REVIEWER_PASSWORD_HASH, DEMO_MODE, and research configuration to validate queued live runs. Worker needs DATABASE_URL, DEMO_MODE and the same research configuration. Put provider secrets only in Railway variables, not source files.
7. Deploy and test sign-in, a demo run, pause/resume and a review. Then configure approved domains and provider credentials, disable demo for production, and validate one real run before wider use. Configure database backups and retention for source excerpts, reviews, and audit records.

The Docker image has not been built locally because Docker Desktop is not running. Railway deployment is prepared but not executed by this task. Browser tests use an isolated local server and invented data.

## Research access checklist

- Anthropic API key and an accessible Claude model (`ANTHROPIC_MODEL`). No inference is made from credentials available inside Codex.
- Tavily API key for public search. This is a separate provider from the Claude key.
- Explicit APPROVED_RESEARCH_DOMAINS. Results outside this allowlist are discarded. No application code fetches arbitrary source URLs; outbound calls go only to the fixed search and model APIs. Search provider excerpts are shown as excerpts, not full-page verification.
- Current model input/output price per million tokens, search price per request, and MAX_RUN_USD. Values must be positive. A pessimistic reservation uses 60,000 input tokens, 3,000 output tokens, and one search per attempt; actual reported usage is also recorded. The serialized model payload is byte-bounded. Rates are estimates entered by the operator, not guaranteed provider billing caps. Set provider account spending limits.
- No HubSpot, Notion, email, calendar, or canonical-intake credentials are requested or used in this release.

## Operator guide

Add a candidate with a specific sourcing brief. Website normalization removes `www`, paths and tracking strings for domain matching; exact canonical domain duplicates reuse the existing record. Cross-domain aliases, subsidiaries and external CRM matches require manual review for now. There is no claim that an external record was checked.

Queue research, then refresh the candidate detail page. The UI shows queued/running/failed states, attempts, reserved budget, evidence dates and usage. A paused worker leaves queued jobs in place; in-flight work may finish. Suppression cancels pending/running work and blocks new research. Check existing relationships manually and record them in human notes. No outreach happens, even for accepted research.

Review the supporting excerpts against the claims. A valid citation ID proves traceability, not truth or semantic support. Qualification weights are editable in code (`domain.ts`) and versioned as `hypothesis-v1`; a settings editor and calibration workflow are not yet built. Unknown dimensions remain null, coverage is displayed separately, and fit is normalized over known dimensions only.

Accept research, reject, nurture, or request more research. Reviews require the current candidate version and latest assessment, so a stale tab cannot approve a newer recommendation. Human notes are stored separately and never replaced by model output. “Accepted” means research accepted, not partner interest, outreach approval, or handoff acceptance. No handoff acceptance control is provided until receiving-owner authentication exists.

If research fails, correct configuration and explicitly queue another run. Past failed jobs remain visible. Rotate SESSION_SECRET to invalidate all reviewer sessions; changing only the password hash does not invalidate existing sessions. The simple per-process login throttle is appropriate for a single-reviewer pilot; team rollout requires managed identity/SSO and shared rate limiting.

## Working versus pending

| Capability | Status |
|---|---|
| Candidate creation, exact-domain deduplication, review UI, audit, notes, suppression | Implemented and locally tested |
| Postgres jobs, lease recovery, checkpoints, pause, budget reservation | Implemented; tested with embedded Postgres (PGlite), including disk reopen |
| Claude analysis / Tavily search | Implemented adapters, mocked tests only; live credentials and access pending |
| Demo research | Explicit invented fixture, opt-in in development |
| Existing Slack bot | Preserved; not connected to PartnerOS candidate workflows yet |
| HubSpot, Notion, canonical intake sync, aliases | Not implemented; adapters/schema discovery deferred |
| Broad outbound sourcing briefs, nurture scheduling, playbook retrieval | Deferred |
| External sends, task assignments, CRM promotion | Disabled; no execution endpoints |
| Receiving-owner handoff acceptance / multi-user roles | Deferred to phase 2 |
| Production deployment / real partner demo | Pending Railway setup and live-provider test |

The delivered demo stops at accepted research. It does not claim to demonstrate an accepted business handoff, which requires phase 2 owner identity and external-system mapping.

## Validation

```sh
pnpm typecheck
pnpm test
```

Tests cover exact-domain duplication, null-score semantics, fake evidence references, stale review rejection, human-note preservation, suppression, pause, budget limits, worker leases and disk persistence, provider failure visibility, fixed outbound endpoints, source-injection inability to invoke actions, password/session checks, CSRF, escaped HTML, and absent external-write endpoints. Local browser checks verify desktop/mobile rendering with fixture data. UI artifacts live under ignored `artifacts/`.
