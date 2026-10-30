# Explicit lead-thread routing

Implemented 2026-10-30; pending merge and AWS deployment.

In a reviewed submission thread in #team-rev-sales-partnerships, Colby can mention int-partner-agent and say “can you route this to the supply leads channel?” (the observed “cahnnel” typo is also accepted). Supported destination names: supply, events, startup/VC, research/edu and affiliate, plus their configured channel mentions. One destination per request. Requests are parsed from Colby's message only, not quoted submissions or attachments. No language-model action selection is used.

Manual routing bypasses score/tier, generic blocker and already-in-play suppression for internal specialist validation. It preserves the original score, assessment, warnings and source links, names the requested handoff as Colby's instruction, and does not qualify the lead, approve terms or contact the applicant. Spam remains suppressed. Automatic routing and existing button restrictions are unchanged.

This branch is based on current main and is independent of the unmerged ask-partners knowledge branch. General Q&A and other arbitrary instructions are not enabled by this change.

## Notion record and audit

Routing requires Notion writing to be enabled. Before posting, locate the existing saved Biz Dev record, otherwise use an exact Intake Ref match, otherwise one unique email match. Multiple matches stop the operation for reconciliation. If no match exists, create the record from the saved assessment. Do not overwrite an existing record's status, score or notes.

Append a request activity paragraph to the record body with a durable marker, requester, UTC time, original instruction and Slack link, destination, original score/assessment/signals, and the distinction between validation and approval. After the handoff, append the actual result and Slack permalink (or delivery-unconfirmed state). The source thread receives the delivery link and Notion record link. This is a page-body activity history, not new database columns.

A shared destination/contact receipt prevents duplicate handoffs, including repeated submissions. Each new explicit request still receives its own audit entry, explaining when a prior delivery was reused. A failure after a confirmed Slack post reports delivery separately from incomplete Notion logging. An ambiguous post or interrupted command is held for reconciliation; never repost automatically. The command's AWS receipt retains the Notion ID and handoff key/link for operators. No background reconciler is introduced here.

## Boundaries and acceptance

- Only Colby, signed events for the configured Slack app/team, configured review channels, and actual thread replies.
- Both specialist routing and Notion writing must be on; otherwise reply with the missing prerequisites.
- No saved review means reconciliation/fresh review is required. The handler does not infer a lead from arbitrary text.
- An unsupported or multiple destination receives a clarification reply.
- Existing startup/VC destination uses the VC/incubator handoff label in this release; individual startup scoring is separate work.
- Test low-score/blocker forwarding, repeat requests, unknown destinations, unauthorized users, ambiguous Notion matches, interrupted execution and uncertain Slack/Notion delivery.
- Live acceptance after deployment: request the Tensor44 supply handoff and verify the original thread acknowledgment, specialist message and both Notion activity receipts.
