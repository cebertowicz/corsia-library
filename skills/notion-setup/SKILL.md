---
name: notion-setup
description: Use when the work needs a person's Notion pages or databases and Notion isn't connected yet. Connects Notion's own hosted tool server with one sign-in; no key and no app to create.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Notion

Notion runs its own hosted tool server (MCP server). Checked on Notion's developer docs (developers.notion.com/docs/mcp) on 2026-10-29.

## What it reaches

Only what the signed-in person can already see in that Notion workspace. It works on every Notion plan. The person must be a member of the workspace, not a guest.

## Steps

1. Check that Notion isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address: `https://mcp.notion.com/mcp` (Streamable HTTP). Use `https://mcp.notion.com/sse` only if the add fails because Streamable HTTP isn't supported.
3. Name the publisher as Notion, from the docs page above. Say who can use it: the whole workspace, or only this project.
4. Hand the person the Notion sign-in and the Allow. Nothing else is theirs to do.
5. Read one real page through it, so you know it works.

## When it doesn't work

- **The person's workspace admin blocks it:** on the Enterprise plan, admins can limit which tools members connect. Say so in one line and stop. Only their admin can change it.
- **No sign-in is possible (unattended use):** Notion offers tokens that need no sign-in only on Business and Enterprise, and only workspace owners can create them. The key goes into the key store at Set key, never into chat.

## Sources

Notion's public MCP documentation, read 2026-10-29. Written in our own words.
