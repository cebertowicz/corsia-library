---
name: tavily-setup
description: Use when the work needs web search, page extraction, site crawling or research through Tavily and Tavily isn't connected yet. Connects Tavily's own hosted tool server with one sign-in.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Tavily

Tavily runs its own hosted tool server (MCP server) at `https://mcp.tavily.com/mcp/`, over HTTP, with OAuth sign-in. Checked on Tavily's docs (docs.tavily.com, the MCP page) on 2026-10-29.

## What it reaches

Search, extract, crawl, map and research on the public web. It reads nothing private. Tavily has a free account. The hosted address refuses use without sign-in (checked 2026-10-29).

## Steps

1. Check that Tavily isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address `https://mcp.tavily.com/mcp/`.
3. Name the publisher as Tavily, from the docs page above. Say who can use it: the whole workspace, or only this project.
4. Hand the person the Tavily sign-in and the Allow (OAuth). If they have no account, signing up is theirs too.
5. Run one search through it, so you know it works.

## When it doesn't work

- **The sign-in fails or the add is refused:** Tavily also publishes a local server, `npx -y tavily-mcp@latest`, which needs Node.js 20 or later (CORSIA supplies its own Node for `npx`), and reads the key from `TAVILY_API_KEY`: list it in the app's `secret_env`. The person opens their Tavily dashboard, which shows the key in the clear, so that page is theirs, not yours. They paste the key at Set key in Connected apps, never into chat.
- **Don't put the key in the web address.** Tavily's docs allow a key in the address, but Connected apps refuses addresses that carry credentials.

## Sources

Tavily's public MCP documentation, read 2026-10-29. Written in our own words.
