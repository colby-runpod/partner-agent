# Lead-level status markers

The queue worker reconciles up to ten saved HubSpot leads each minute, cycling through the current AWS ledger. It marks the original source submission and latest review card:
- clock3: no confirmed completion and not parked; still needs review/action, including failed reviews.
- white_check_mark: a completion receipt confirms Actioned/Closed in Notion.
- double_vertical_bar: deliberately parked; not completed.

Completion takes precedence over an earlier park flag. Only bot-owned conflicting markers are removed, after the target marker is added successfully. Human reactions, Q&A messages and draft responses are untouched. Cosmetic API failures retry on the next cycle. This does not re-score leads, route leads, send email, or treat all checks as proof of completion. Direct edits to Notion are not polled. The backfill is limited to leads in the fresh-start AWS ledger; earlier Railway history remains outside this workflow.

## Weekly audit repair

The Nov 2 weekly Body was 5,505 characters. Slack returned a receipt for its second split message, leaving the first feedback thread unowned. The verified original and continuation are reconciled to one existing outbox delivery, with timestamp aliases retained. Future long weekly posts use verbatim section blocks and a short fallback text, retaining one parent and one receipt. Oversized lines fail validation before sending rather than being silently truncated. Earlier failed/cancelled producer rows are retained for audit and are not requeued.
