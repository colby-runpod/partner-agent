# Lead completion vs request completion

A check on a human question means its response finished. A bot check on the original lead submission and saved review card means the Partnerships intake action is recorded in Notion.

- A confirmed specialist handoff (automatic, routing button, or explicit routing request) queues a durable completion job.
- Colby's white_check_mark on an original submission or saved review card explicitly marks that lead actioned. The reaction is re-read before writing. Bot reactions and checks on unrelated question/draft replies do not close a lead.
- The existing strict Notion matching workflow links or creates the record. Ambiguous matches stop the operation.
- Record Status=Actioned; preserve an already Closed status. Confirmed handoffs also set Intake Stage=Routed Internally and Forward To. No delivered handoff is inferred from a manual check alone.
- Append who/why/source/handoff links to the page body. Preserve qualification score and findings. Read back Notion status before adding the bot's checks to both the original source and review card.
- An uncertain external write is held for reconciliation; failed or unconfirmed handoffs never generate completion. A failed Slack reaction leaves the Notion receipt intact with an error for repair.

Actioned means Partnerships intake/handoff is complete, not that the receiving team approved the lead, a deal was won, or all downstream follow-up finished. Drafting, answering, logging a record, or parking alone never closes the lead.

This release is Slack/action-driven. It does not poll arbitrary Notion status edits or automatically reopen a record when a check is removed. Explicit reopening and Notion-to-Slack status synchronization remain separate work.

Includes the pending Slack draft-formatting fix from codex/draft-slack-formatting.
