---
name: exa-setup
description: Use when the work needs web search or page fetching through Exa and Exa isn't connected yet. Connects Exa's own hosted tool server, keyless first.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Exa

Exa runs its own hosted tool server (MCP server) at `https://mcp.exa.ai/mcp`, over HTTP. Checked on Exa's docs (exa.ai/docs, the Exa MCP page) on 2026-10-29.

## What it reaches

Web search, page fetching and advanced search on the public web. It reads nothing private. With no key, use is free and rate-limited. The `agent_run` tool always needs a key, and is billed by use.

## Steps

1. Check that Exa isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address `https://mcp.exa.ai/mcp`, with no key.
3. Name the publisher as Exa, from the docs page above. Say who can use it: the whole workspace, or only this project.
4. Run one search through it, so you know it works.

## When it doesn't work

- **Rate-limited, or the work needs `agent_run`:** the person creates a key in Exa's dashboard (dashboard.exa.ai). That page shows the key in the clear, so it is theirs, not yours. New accounts start with free credits. Any plan or payment choice is theirs too. List `x-api-key` in the app's `secret_headers`, and have the person paste the key at Set key in Connected apps, never into chat.
- Exa also supports OAuth sign-in. Use it if the key route isn't wanted.

## Sources

Exa's public MCP documentation, read 2026-10-29. Written in our own words.
