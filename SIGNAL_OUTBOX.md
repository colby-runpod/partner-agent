# Partner signal enrichment delivery

Claude remains responsible for Gong/usage research and the final summary. AWS only delivers the completed text as int-partner-agent, with a final cc to Colby.

Enable SIGNAL_OUTBOX_ENABLED=true after merging and deploying. This enables only channel C0BN1S7055G alongside the existing outbox selection rules; other channels and the fresh-start cutoff remain unchanged. Default is disabled.

Producer contract:
- Reuse one Partner Agent Outbox row per reporting week, titled partner-signal-enrichment:YYYY-MM-DD (Monday date).
- Channel ID: C0BN1S7055G; Kind: other; Status: Queued once ready.
- Body: final Slack-formatted summary with HTTPS source links and explicit incomplete-data notes. No user/group/broadcast mentions or “Sent using Claude” footer. AWS appends cc Colby.
- Leave Mention IDs, Thread TS, Message TS and Permalink empty for a new row.
- Optional Not Before: date-time with timezone. Otherwise posts on next poll, normally within three minutes.
- On retries, reuse the same row. Do not clone, reset a posted row, or post directly to Slack. Delivery deduplication is by row ID, not title.

AWS posts top-level with no buttons. Successful sends write Posted, Message TS, Permalink and Posted At. Definite failures write Failed/Error; uncertain sends are held for reconciliation, never blindly resent. Saved receipts retry Notion writeback without reposting. Replies and reactions do not trigger work.

Cutover: inspect channel membership, run one approved end-to-end test after deployment, then change Claude from direct Slack delivery to this outbox contract. Keep the already-posted November 2 report intact unless explicitly asked to replace it. A test should be labeled TEST and must not imply fresh research. This change does not add AWS Gong research or fix unfinished upstream data-agent requests.
