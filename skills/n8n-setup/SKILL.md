---
name: n8n-setup
description: Use when the work needs to find, run or build workflows in a person's n8n and n8n isn't connected yet. Connects the tool server built into their own n8n instance, cloud or self-hosted.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect n8n

n8n has a tool server (MCP server) built into each instance. Each person's address is their own, whether on n8n's cloud or self-hosted. Checked on n8n's docs (docs.n8n.io, connect to n8n MCP server) on 2026-10-29.

## What it reaches

It can search workflows, and trigger and test the workflows the person has exposed. From n8n 2.13.0 it can also create and edit workflows and data tables. Running a workflow can act on anything that workflow touches: get the person's yes before the first run and the first edit.

## Steps

1. Check that n8n isn't already in Connected apps. If it is, use it.
2. **Turn it on, in n8n.** An instance owner or admin opens Settings > Instance-level MCP > Enable MCP access. Then they turn on MCP for each workflow the agent may use, one by one. Only search sees workflows they haven't turned on. If the person isn't an owner or admin, tell them who must do it, and stop.
3. **Get the address.** In n8n, Settings > Instance-level MCP > Connect a client shows the instance's address, ending in `/mcp-server/http`. Ask the person for it, or read it from that page if they've opened it for you. Never guess it from a domain.
4. Add a connected app by that web address.
5. Name the publisher as n8n, from the docs page above. Say who can use it: the whole workspace, or only this project.
6. Hand the person the n8n sign-in and the Allow (OAuth).
7. Search workflows through it, so you know it works.

## When it doesn't work

- **No sign-in is possible:** the same Connect a client page has an API key tab with a personal access token. That page shows the token in the clear, so it's the person's. List `Authorization` in the app's `secret_headers`, with the `Bearer` scheme in `key_header_schemes`. The person pastes the token at Set key in Connected apps, never into chat.
- **A workflow can't be found or run:** MCP isn't turned on for that workflow yet (step 2).

## Sources

n8n's public MCP server documentation, read 2026-10-29. Written in our own words.
