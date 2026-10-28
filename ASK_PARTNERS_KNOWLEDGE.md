# Ask-partners knowledge pilot

Built 2026-10-28. Requires deployment and `KNOWLEDGE_ENABLED=true`; this document does not mean it is live.

## Usage

In #ask-partners (C0C7HG8KYFL), Colby mentions @int-partner-agent and asks a plain-text question without an eyes reaction. The bot replies in the same thread. Examples:

- Who owns partner technical prioritization?
- What does our team charter say about partnerships?
- Where can I find partner onboarding guidance?

For a question containing a link or forwarded attachment, begin the text after the mention with `ask:`. Links and attachments are not fetched. Ask self-contained questions: the pilot does not retrieve prior thread context. Other users are not enabled yet.

A forwarded lead with a mention and Colby's eyes reaction keeps the existing lead review flow. Eyes present when first processed takes priority over question detection. Questions and reviews share one durable key per source message, so adding eyes after a question was accepted cannot convert it into a lead or produce another response. To review that lead, forward it in a new message and use the established approval flow. Delivery timeouts are held for reconciliation, never automatically reposted.

## Approved scope

Colby approved quoting these four pages to #ask-partners on 2026-10-28:

- Partnerships Home: c42ff732fc348245a2a38177f325bb6f
- Partnerships Team Charter: 3deff732fc348191b15dc3ed5ccc09c5
- Partner FAQs: 3e4ff732fc348005ac76f211511a65d5
- Partner How-tos: 3e4ff732fc3480f4ba57e8e3b3c38acf

Fetch live page content for every question. Page links, child pages, databases, synced blocks, files and external URLs are not followed. This is not a complete teamspace search and will not answer detailed partner status questions from deal records. New sources require an explicit audience review and allowlist change. Integration access alone never adds a source.

Retrieve page metadata and paginated nested text blocks, bounded at 12 block calls, six nesting levels and 20,000 characters per page. Omitted or inaccessible content is disclosed. Last-edited dates are context, not verification. Blank/inaccessible sources cannot support answers. No usable evidence means no model call.

The model receives the current question and approved source excerpts as untrusted evidence, with no tools. It must identify unknowns/conflicts and cite factual paragraphs. The renderer validates source IDs and produces links from the allowlist, never from model-supplied URLs. Source support is still a model judgment; citations allow human verification. The bot cannot approve terms, change Notion, send email or route leads from this path.

## Activation and acceptance

1. Merge passing code and deploy using the existing receiver-first worker handover.
2. Keep CHAT_ENABLED=false; set KNOWLEDGE_ENABLED=true alongside REVIEW_ENABLED and FORWARD_INTAKE_ENABLED. Existing NOTION_TOKEN and Anthropic credentials are used without exposing values.
3. Verify runtime retrieval of all four pages. Check skipped unsupported blocks; do not silently expand scope to linked pages.
4. Test a question with a known sourced answer, missing information and conflicting/draft information. Verify source links work and replies stay in-thread.
5. Test an approved forwarded lead and later eyes on a completed question. Confirm one workflow per source and no duplicate delivery.
6. Disable KNOWLEDGE_ENABLED to stop accepting new questions without disabling lead reviews. Already queued questions retain their durable receipt and may finish; pause the worker for immediate containment if needed.
