# Summary connector (MCP)

Lets Claude post partnership summaries to #team-partnerships-development
(C0C7R970C5T) as Team Partner Agent, so they arrive as normal Slack
notifications. It exposes one tool, `post_summary(text)`. The channel is
hard-coded.

This folder is a **separate Railway service**. It does not touch the root Slack
service, its Dockerfile, its `/data` volume or its single-replica rule. The
root CI (`python -m unittest discover`) skips this folder, because it isn't a
package.

## Railway

1. Commit this folder as `mcp_connector/` at the repo root.
2. In the same project: **New → GitHub Repo**, pick this repo and branch.
   - Root Directory: `/mcp_connector`
   - Config-as-code path: `/mcp_connector/railway.json`
3. Variables:
   - `SLACK_BOT_TOKEN`: reference the bot service's variable, `${{partner-agent.SLACK_BOT_TOKEN}}` (use your service's actual name)
   - `MCP_PATH_SECRET`: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
4. **Settings → Networking → Generate Domain** (the port comes from `PORT`).
5. Check that `https://<domain>/healthz` returns `{"ok":true}`.

No Slack app changes are needed. The bot already has `chat:write` and is in
the channel.

## Claude

Settings → Connectors → **Add custom connector**. Use the URL
`https://<domain>/mcp/<MCP_PATH_SECRET>`.

Custom connectors can't send static headers, so the secret path is the auth.
Treat the full URL like a password. To rotate it, change `MCP_PATH_SECRET` and
update the connector URL.

## Verified locally

These checks ran from a clean virtualenv with the pinned requirements and a
fake token:

- `/healthz` returns 200
- A wrong path returns 404
- An external Host header is accepted
- `tools/list` shows `post_summary`
- A tool call with a bad token returns Slack's `invalid_auth` as a tool error

Live Slack posting still needs the deployed test.
