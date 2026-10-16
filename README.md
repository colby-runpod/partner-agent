## Partner Development workspace

The new first-release PartnerOS application is in [`partneros/`](partneros/README.md). It adds an authenticated candidate pipeline, evidence-backed research adapters, durable Postgres jobs and a human review queue. It is a separate development deployment; the existing Slack service remains in this repository root. See its guide for tested capabilities and pending integrations.

> For the Slack-only mention/chat pilot on Railway, follow [CHAT-SETUP.md](CHAT-SETUP.md). The Notion intake instructions below describe the separate original mode.

# Partner Agent

Runpod's internal partnerships agent. Includes a **preview-only objective briefing pilot** and an **immediate Slack intake service**. The Slack chat receiver has been deployed separately; the original Notion intake mode remains paused pending setup. The intake service can create Notion entries and reply in Slack only after credentials, hosting, and `INTAKE_ENABLED=true` are configured. See [deployment instructions](DEPLOYMENT.md).

## p-OPS foundation

The Partner Ops expansion is a deliberately isolated, disabled-by-default foundation:

- [`P_OPS_PROMPT.md`](P_OPS_PROMPT.md) is the durable operating and approval contract for p-OPS.
- [`p_ops.py`](p_ops.py) is a deterministic task-hygiene and Linear-routing planner. It previews evidence-bound status changes and never calls an external system.
- [`p_ops_notion.json`](p_ops_notion.json) maps the planner to the live Working Tasks schema. The database now has a dedicated `Linear Issue` URL property.
- [`p_ops_notion.py`](p_ops_notion.py) reads active Working Tasks, creates an idempotent SQLite preview and approval queue, and reconciles writes without blind retries.
- [`p_ops_capture.py`](p_ops_capture.py) accepts only Colby's allowlisted Slack `:handshake::skin-tone-2:` reactions in allowlisted channels, verifies Slack signatures, and stores the exact reacted message as untrusted source evidence. It does not create a task or send a reply.
- [`p_ops_approval.py`](p_ops_approval.py) separates message drafting, exact-content approval, and explicit send authorization. Editing the content or destination invalidates approval. An uncertain send is held for review and is never retried automatically.
- [`p_ops_linear.py`](p_ops_linear.py) drafts bounded Linear issues, searches by the source Notion URL before creation, and requires explicit approval plus a separate write gate. It cannot set priority, assignment, due dates, projects, or roadmap commitments.
- [`p_ops_inbox.py`](p_ops_inbox.py) turns a verified Slack capture into a conservative, duplicate-checked Working Tasks Inbox proposal. The proposal must be reviewed before the separate Notion creation gate can execute it.
- [`p_ops_runtime.py`](p_ops_runtime.py) connects the queues through one locked worker and a protected `/review` dashboard. It can schedule one daily Notion preview per America/Denver day without treating routine checks as meaningful task activity.
- [`p_ops_triage.py`](p_ops_triage.py) defines the strict structured decision contract and applies deterministic evidence, date, routing, risk, and approval checks after model analysis. Pilot results are always non-executable shadow decisions.
- [`p_ops_shadow.py`](p_ops_shadow.py) connects captured Inbox proposals to read-only triage. It shortlists active Working Tasks, records the evidence bundle and guarded decision in the local audit database, exposes no approval action, and cannot change an external system.
- [`p_ops_workflow.py`](p_ops_workflow.py) turns a review-required shadow decision into a persisted Slack thread with one approval question at a time. Only allowlisted reviewers in the configured channel may answer. Links to the source, related task, and p-OPS are included, and the recorded answers do not bypass any execution gate.
- [`p_ops_eval.py`](p_ops_eval.py) validates and optionally runs the 25-case triage corpus in [`evals/p_ops_triage_cases.jsonl`](evals/p_ops_triage_cases.jsonl). Each historical case records its reconciliation status and supporting source references; [`evals/HISTORICAL_EVIDENCE.md`](evals/HISTORICAL_EVIDENCE.md) summarizes the audit and remaining access gaps.
- [`P_OPS_DEPLOYMENT.md`](P_OPS_DEPLOYMENT.md) contains the isolated pilot deployment and validation order. The existing Slack chat service is not changed.

Run the p-OPS safety tests with:

```sh
python3 -m unittest test_p_ops.py -v
python3 -m unittest test_p_ops_notion.py -v
python3 -m unittest test_p_ops_capture.py test_p_ops_approval.py test_p_ops_linear.py -v
python3 -m unittest test_p_ops_inbox.py test_p_ops_runtime.py -v
python3 -m unittest test_p_ops_workflow.py test_p_ops_notifications.py -v
python3 -m unittest test_p_ops_triage.py -v
python3 p_ops_eval.py
```

The credential-free eval command validates the corpus and writes `output/p_ops_triage_eval.json`; it does not call a model. A live run requires `ANTHROPIC_API_KEY`, an explicitly selected `ANTHROPIC_MODEL`, and `--live`. Live evaluation may incur API charges. The historical corpus now records source-level reconciliation status: 15 verified, one partial, and two blocked by inaccessible Slack channel history.

The background worker can run the same contract against captured proposals only when `P_OPS_TRIAGE_SHADOW_ENABLED=true`. Shadow triage reads active Working Tasks, calls the configured model, and writes a non-executable audit record to the local p-OPS database. It does not update the proposal or authorize Notion, Linear, Slack, or Gmail actions.

Preview the live queue without writing:

```sh
NOTION_TOKEN=... python3 p_ops_notion.py preview
python3 p_ops_notion.py list
```

Review-required rows must be approved by ID. Approval does not write:

```sh
python3 p_ops_notion.py approve ACTION_ID
```

Applying is separately gated by `P_OPS_NOTION_WRITE=true`. Without `--include-auto`, only explicitly approved rows are eligible. Source changes after preview produce a conflict instead of a write. A timed-out or otherwise uncertain write is read back once and is never automatically retried.

The current Slack chat service automatically posts answers. It is not the p-OPS communication surface because p-OPS requires Colby's review and explicit send authorization for every message. The p-OPS capture, message, and Linear components remain disconnected from that service.

All new capabilities are off unless their individual gate is exactly `true`:

- `P_OPS_CAPTURE_ENABLED` accepts Slack capture events. `P_OPS_CAPTURE_CHANNEL_IDS` and `P_OPS_CAPTURE_USER_IDS` are required comma-separated allowlists.
- `P_OPS_NOTION_WRITE` permits approved Notion actions.
- `P_OPS_NOTION_CREATE` permits an approved, duplicate-checked Inbox proposal to become a Working Task.
- `P_OPS_LINEAR_WRITE` permits an approved Linear draft to be created after a duplicate search.
- `P_OPS_SEND_MESSAGES` permits a separately approved and send-authorized Slack or email draft to be delivered through an injected sender.
- `P_OPS_REVIEW_POSTING_ENABLED` permits review cards in the single configured internal Slack channel.
- `P_OPS_REVIEW_INTERACTIVE_ENABLED` permits allowlisted reviewers to answer the review card's sequential questions. It records decisions only; it does not execute the approved work.

These gates do not replace review. Keep them unset until the corresponding receiver/worker is deployed with persistent storage, scoped credentials, and an operator review surface.

## Run the pilot

The default workflow needs Python 3.9+ and no third-party packages or credentials:

```sh
python3 agent.py --fixture demo.json --date 2026-10-06
python3 -m unittest discover -v
```

The fixture is invented demonstration data, not current partnership status. The output is saved to `output/brief.txt`. The pilot highlights recorded blockers, missing owners, overdue work, and changes since the last accepted snapshot.

An initial run reports a baseline rather than pretending all records are new progress. Previewing does not advance that baseline. After reviewing a snapshot, explicitly accept it:

```sh
python3 agent.py --fixture demo.json --accept-baseline
```

Acceptance only establishes a local comparison point; it never means a message was delivered. State is stored in SQLite and should reside on persistent storage if scheduled later. Fixture and Notion snapshots have separate scopes.

## Read selected Notion records

1. Copy `config.example.json` to a local config file. Add that local file to `.gitignore` if it contains internal source identifiers.
2. Put the approved objective and task page IDs in `pages`. Relations are not automatically expanded.
3. Set `fields` to the exact property names in those records. The current pilot requires the same mapping across selected pages. Missing fields fail visibly rather than silently producing an incomplete report.
4. Provide `NOTION_TOKEN` through the environment or a secret manager. Grant the integration read access to only the selected records. `.env.example` documents variable names; `.env` files are not automatically loaded.
5. Run:

```sh
python3 agent.py --notion config.local.json
```

Coverage is **selected page properties only**. Page bodies, comments, relation expansion, paginated property values, and database queries are not implemented. Keep title/owner/text values short for this pilot; the live schema and possible API truncation need validation before production. Notion failures leave the accepted baseline unchanged. No retries or scheduled service are enabled yet.

## Optional AI recommendations

The factual brief is deterministic. An optional OpenAI Agents SDK pass adds clearly labeled recommendations without tools or write permissions.

Use Python 3.10+ in a virtual environment, install `requirements-ai.txt`, and configure `OPENAI_API_KEY` and `OPENAI_MODEL`. Then add `--ai` to the command. This sends the selected record fields to the configured OpenAI API account. API calls have not been exercised in this workspace. The optional dependency is not locked yet; resolve, test, and lock it before deployment. Tracing is disabled to avoid additional source-content logging.

## Next build steps

- Confirm Notion page selection and property mappings using the actual pilot objective.
- Validate a live read and review the first brief with Colby.
- Create or reuse a Partner Agent Slack application and confirm its destination channel.
- Implement explicit preview approval, delivery records, duplicate prevention, and failure recovery before scheduled sends.
- Choose approved hosting, persistent storage, and a weekday schedule in America/Denver.
- Validate the sequential Slack approval flow with one non-sensitive shadow task before enabling any execution gate.

The planned runtime is a small hosted Python application. Reusing Dan's existing hosting and bot framework remains an option; the current repository does not assume Railway is approved or that existing bots use it.

## References

- [OpenAI Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart)
- [Notion page retrieval](https://developers.notion.com/reference/retrieve-a-page)
- [Slack application quickstart](https://docs.slack.dev/quickstart/)
