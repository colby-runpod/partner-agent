# Email drafting in reviewed lead threads

Colby can mention the agent in a reviewed HubSpot thread:
- “Draft an email to this person.”
- “Make it shorter/warmer.”
- “Rewrite the draft to focus on the buying inquiry.”
- “Save this draft to Gmail.”

Drafts are posted with recipient, subject and body. The recipient comes from the saved lead, not model output. Revisions use the latest successfully posted structured draft from this same thread and earlier than the current request. A low score does not prevent drafting or force a decline. The draft does not alter the saved assessment or route the lead.

Draft text is escaped for Slack. The model receives verified source-thread context but has no action tools. It cannot send email. Request reactions follow the received/working/completed/warning lifecycle.

“Save this draft to Gmail” is a separate explicit handoff. It links/creates the appropriate Notion record using the existing strict matching workflow, then requests the exact recipient, subject and body through Next Step Notes / Suggested Next Step. The request specifies From partners@runpod.io and the Gmail signature partners@, and instructs the downstream task to stop if either is unavailable. The AWS agent does not have Gmail authorization and cannot itself verify those settings or create a Gmail draft. Its acknowledgment says requested, not created. The existing downstream task must record Gmail Draft ID and its link after actual creation.

Existing Gmail Draft ID or a pending draft request prevents a second request. An interrupted external write is held for reconciliation rather than automatically repeated. Revisions made after a Gmail request do not automatically replace that request or existing Gmail draft. Review the existing record before requesting another.

No Gmail OAuth change or database migration is required. Slack drafts are retained in the durable job receipt and conversation; Notion is written only on an explicit Gmail request. This release does not add a Save button: the explicit mention command performs the handoff.
