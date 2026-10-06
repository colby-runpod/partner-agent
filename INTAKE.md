# Immediate partner intake

Confirmed destination: **Partner Intake & Qualification**, Notion data source `ffecfb43-625c-42c6-a6af-1f8951641bba`. Schema inspected October 6, 2026. `intake.py` now uses this destination and defaults from `intake-routing.json`; the service has not been deployed or tested against live accounts.

## Intended flow

1. A human request arrives in `partnerships-agent-capture`, or the trusted form integration posts in `team-rev-sales-partnerships`.
2. Validate the Slack event, source channel and sender. Accept the verified form bot while ignoring this agent's own messages. Form app/bot identity still needs verification from a real event payload; search results do not identify it reliably. Never trust a matching message heading alone.
3. Persist the event before acknowledging it. Deduplicate Slack retries by event ID and intake writes by canonical source reference.
4. A worker starts immediately and replies in the source thread. Resolve forwarded message context; ask for the actual request if a message only says “send this” without accessible source material.
5. Extract supported fields, retain the original request and source link, and check the queue for an existing intake reference. Email/company similarity is a review signal, not automatic permission to merge separate requests.
6. Create an intake row with Status New, Intake Stage New, and Conversion Status Not ready. If the reference already exists, return the existing row rather than duplicating it. Ambiguous prior writes must be reconciled before retrying.
7. Reply with the saved Notion link only after creation is confirmed. Record failures and expose them in-thread.

## Field handling

- Contact is the title; Company, Email, Website URL, Request Summary, Submitted, Intake Ref, and HubSpot Contact have existing fields.
- Preserve the source submission time; do not substitute a made-up date or turn unknown values into facts.
- Intake Ref is text and can retain the canonical Slack permalink. Keep all original source links in the page body when content is forwarded.
- Source currently allows Slack Form, Email Direct, Email Forwarded, Google Form, and Press Inbox. Use Slack Form for web-form notifications delivered through Slack. For manual captures, preserve known original provenance; otherwise leave Source empty and describe manual Slack capture in the body. Do not invent a new option.
- Partnership Type, Suggested Next Step, Forward To, and qualification fields exist, but require explicit classification rules and evidence. Initial intake does not automatically qualify, disqualify, invite, convert, forward externally, or send email.
- Google Form Completed must not be checked merely because a website form was submitted.

## Still required for a working service

Slack app installation and channel membership, event subscriptions for the public and private channels, verified request handling, a durable queue/worker, Notion API access to this data source, Slack reply credentials, approved hosting, and an end-to-end test. Existing Codex connector access does not automatically grant the deployed application its own credentials.

The intake listener, durable worker, Notion writer, source lookup, and threaded reply path are implemented and tested with fake clients. No live intake records have been created. See DEPLOYMENT.md for the current limits, setup, and activation steps.
