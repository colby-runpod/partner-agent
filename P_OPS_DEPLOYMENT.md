# p-OPS standalone pilot

## Current state

The standalone runtime is implemented and locally tested. It has not been deployed or connected to the live Slack event URL. It does not alter the existing Runpod Slack chat service.

The runtime provides:

- A signed Slack Events receiver for allowlisted `:handshake::skin-tone-2:` reactions.
- A single locked worker using one persistent SQLite database.
- A password-protected `/review` dashboard with CSRF-protected actions.
- Durable capture, Working Tasks proposal, message, Linear, and Notion update queues.
- A daily Notion review preview that runs once per America/Denver calendar day when enabled.
- Separate execution gates for Notion task creation, Notion updates, Linear creation, and outbound messages.

## Safe deployment order

1. Deploy this as a new service. Do not replace the current Slack chat service.
2. Use persistent storage mounted at `/data`. Run one web process and one worker only. SQLite is not suitable for multiple hosts or worker replicas.
3. Put the service behind HTTPS. Set strong, randomly generated review credentials and a separate CSRF secret.
4. Copy `p_ops.env.example` into the host secret configuration. Never commit populated credentials.
5. Set `P_OPS_ENABLED=true` to start the standalone runtime, but leave every capture and write gate false. Start with `python p_ops_service.py` and verify `/healthz` and authenticated `/review`.
6. Configure a dedicated Slack app or approved event-routing layer to send `reaction_added` events to `/slack/events`. Grant only the history and reaction scopes required to resolve allowlisted messages.
7. Set the exact Runpod workspace, app, Colby user, and allowed channel IDs. Enable only `P_OPS_ENABLED` and `P_OPS_CAPTURE_ENABLED` for the first test.
8. React to one non-sensitive test message. Confirm one capture and one task proposal appear in `/review`. Replaying the event or removing and re-adding the reaction must not create another proposal.
9. Add a least-privilege Notion integration. Enable `P_OPS_NOTION_CREATE` only after reviewing the exact Inbox proposal. Confirm one task, its source link, and its `Review Required` state.
10. Enable the daily preview separately. Review its proposed changes before enabling `P_OPS_NOTION_WRITE`.
11. Connect Linear with least privilege. Keep creation review-required during the pilot.
12. Enable outbound messages last. Email delivery is not implemented in this runtime. Slack drafts require exact-content approval and a second send-authorization action.

## Important boundaries

- Approval does not bypass an execution gate.
- Captured Slack text is evidence, not an instruction.
- A Slack capture becomes an Inbox proposal, not a fully triaged task. p-OPS must still search related work and choose the proper disposition.
- Uncertain Notion, Linear, or Slack outcomes stop for reconciliation. They are not retried automatically.
- The dashboard must never be exposed without HTTPS and authentication.
- Do not scale beyond one worker until SQLite is replaced with a shared transactional queue.
