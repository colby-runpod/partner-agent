"""
Team Partner Agent -- MCP connector for posting partnership summaries.

Exposes ONE tool, `post_summary`, which posts to #team-partnerships-development
(C0C7R970C5T) as the Team Partner Agent bot. The channel is hard-coded so the
tool cannot be used to post anywhere else.

Auth: Claude custom connectors don't let you set static headers, so the
endpoint lives at a secret path: https://<railway-domain>/mcp/<MCP_PATH_SECRET>
Anyone without the full URL gets a 404.

Env vars:
  SLACK_BOT_TOKEN   xoxb-... token for the Team Partner Agent app (needs chat:write)
  MCP_PATH_SECRET   random string, 32+ chars (python -c "import secrets; print(secrets.token_urlsafe(32))")
  PORT              set automatically by Railway
"""

import os
import sys

import uvicorn
from mcp.server.fastmcp import FastMCP
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from starlette.requests import Request
from starlette.responses import JSONResponse

CHANNEL_ID = "C0C7R970C5T"  # #team-partnerships-development -- do not make this a parameter
MAX_CHARS = 12000  # Slack's limit for markdown_text

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
PATH_SECRET = os.environ.get("MCP_PATH_SECRET", "")

if not SLACK_BOT_TOKEN.startswith("xoxb-"):
    sys.exit("SLACK_BOT_TOKEN must be set to the bot's xoxb- token")
if len(PATH_SECRET) < 32:
    sys.exit("MCP_PATH_SECRET must be set and at least 32 characters")

slack = AsyncWebClient(token=SLACK_BOT_TOKEN)

# host="0.0.0.0" so Railway's public hostname isn't rejected by the SDK's
# localhost-only DNS-rebinding guard. stateless_http keeps it simple behind
# Railway's proxy (no sticky sessions needed).
mcp = FastMCP(
    "team-partner-agent",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    streamable_http_path=f"/mcp/{PATH_SECRET}",
    stateless_http=True,
)


@mcp.tool()
async def post_summary(text: str) -> dict:
    """Post a partnerships summary to #team-partnerships-development as Team Partner Agent.

    `text` is standard markdown: **bold**, _italic_, `code`, - bullets,
    numbered lists and [label](https://...) links all render. Max 12,000 chars.
    Returns the message ts and permalink so the caller can read it back.
    """
    if not text or not text.strip():
        raise ValueError("text is empty")
    if len(text) > MAX_CHARS:
        raise ValueError(f"text is {len(text)} chars; Slack's limit is {MAX_CHARS}")

    try:
        resp = await slack.chat_postMessage(
            channel=CHANNEL_ID,
            markdown_text=text,
            unfurl_links=False,
            unfurl_media=False,
        )
        ts = resp["ts"]
        link = await slack.chat_getPermalink(channel=CHANNEL_ID, message_ts=ts)
    except SlackApiError as e:
        # Surface Slack's error code (e.g. not_in_channel, invalid_auth) to the caller.
        raise RuntimeError(f"Slack error: {e.response.get('error', 'unknown')}") from e

    return {"ok": True, "channel": CHANNEL_ID, "ts": ts, "permalink": link["permalink"]}


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    uvicorn.run(
        mcp.streamable_http_app(),
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
