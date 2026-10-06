# Partner Agent

Runpod's internal partnerships agent. This repository starts with a **preview-only objective briefing pilot**. It is not deployed and does not send Slack messages or modify Notion.

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
- Add Slack questions and thread replies, then HubSpot and approval workflows.

The planned runtime is a small hosted Python application. Reusing Dan's existing hosting and bot framework remains an option; the current repository does not assume Railway is approved or that existing bots use it.

## References

- [OpenAI Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart)
- [Notion page retrieval](https://developers.notion.com/reference/retrieve-a-page)
- [Slack application quickstart](https://docs.slack.dev/quickstart/)
