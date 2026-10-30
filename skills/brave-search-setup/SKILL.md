---
name: brave-search-setup
description: Use when the work needs web, news, image, video or local search through Brave Search and Brave Search isn't connected yet. Runs Brave's own tool server on this machine with the person's API key.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Brave Search

Brave publishes its own tool server (MCP server), which runs on this machine. Checked on Brave's repository for it (github.com/brave/brave-search-mcp-server) and Brave's Search API page (brave.com/search/api) on 2026-10-29. Brave states no hosted address.

## What it reaches

Web, local, video, image and news search, plus `brave_llm_context`. It reads nothing private. Brave's free allowance is up to $20 of calls a month. Full local search needs Brave's Pro plan.

## Steps

1. Check that Brave Search isn't already in Connected apps. If it is, use it.
2. **The key is the person's.** They generate it in Brave's developer dashboard. That page shows the key in the clear, so don't open it yourself. Choosing a plan or entering payment is theirs too.
3. Add a local connected app: command `npx`, arguments `-y @brave/brave-search-mcp-server --transport stdio`, and `BRAVE_API_KEY` listed in its `secret_env`. The person pastes the key at Set key in Connected apps, never into chat.
4. Name the publisher as Brave, from the repository above. Say who can use it: the whole workspace, or only this project.
5. Run one web search through it, so you know it works.

## When it doesn't work

- **A local search comes back empty or refused:** it may need the Pro plan. Tell the person in one line. The choice is theirs.

## Sources

Brave's public brave-search-mcp-server repository and Search API page, read 2026-10-29. Written in our own words.
