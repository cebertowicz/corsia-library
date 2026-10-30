---
name: linear-setup
description: Use when the work needs a person's Linear issues, projects or teams and Linear isn't connected yet. Connects Linear's own hosted tool server with one sign-in, read-only when the work only reads.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Linear

Linear runs its own hosted tool server (MCP server). Checked on Linear's docs (linear.app/docs/mcp) on 2026-10-29.

## Pick the address

- **The work only reads:** `https://mcp.linear.app/mcp/readonly`. It only ever exposes read tools.
- **The work creates or changes issues:** `https://mcp.linear.app/mcp`.

Both use Streamable HTTP. `https://mcp.linear.app/sse` is a deprecated fallback; use it only if the add fails.

## Steps

1. Check that Linear isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address, using the address picked above.
3. Name the publisher as Linear, from the docs page above. Say who can use it: the whole workspace, or only this project.
4. Hand the person the Linear sign-in and the Allow (OAuth). Nothing else is theirs to do.
5. Read one real issue list through it, so you know it works.

## When it doesn't work

- **No sign-in is possible (unattended use):** Linear also accepts a Linear API key. The person creates it in Linear's settings and pastes it at Set key in Connected apps, never into chat.
- **Writing when the read-only address is connected:** add the full address as a second connection only when the work needs it, and get the person's yes before the first change.

## Sources

Linear's public MCP documentation, read 2026-10-29. Written in our own words.
