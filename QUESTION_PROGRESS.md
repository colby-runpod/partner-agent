# Request reactions

Accepted human questions and commands show progress on the requesting message:
- eyes: durably received (best-effort acknowledgment within the webhook's time budget)
- hourglass_flowing_sand: processing
- white_check_mark: response confirmed posted with no recorded processing failure
- warning: processing failed or delivery was uncertain

The bot removes only its own previous status reactions. Human eyes reactions used to approve lead forwarding are untouched. Automated HubSpot intake messages are not marked by this feature. Duplicate webhook deliveries do not restart acknowledgment; a late acknowledgment is removed if processing has already begun. Reaction failures never block an answer or trigger a resend.

Applied to lead-thread Q&A, explicit lead routing, manual review requests, and generic mention chat. The separately staged Notion-backed ask-partners handler uses the same helper when that release is enabled. This does not enable disabled chat or knowledge features.

Requires reactions:write, already declared in slack-manifest.json. A missing_scope error means an administrator must reinstall after granting the scope. Scope failure is logged and does not prevent processing.

The progress release includes the pending Q&A reliability fix. Check marks mean the response finished, not that a lead is approved or qualified. Reaction delivery is cosmetic/best-effort; there is no new persistent retry queue for Slack reactions.
