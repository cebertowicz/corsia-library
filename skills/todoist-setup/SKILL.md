---
name: todoist-setup
description: Use when the work needs a person's Todoist tasks or projects and Todoist isn't connected yet. Connects Todoist's own hosted tool server with one sign-in.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Todoist

Doist, the maker of Todoist, runs its own hosted tool server (MCP server) at `https://ai.todoist.net/mcp`, over Streamable HTTP. Checked on Doist's own repository for it (github.com/Doist/todoist-mcp) on 2026-10-29.

## What it reaches

The signed-in person's Todoist: it can find, add and change tasks. Reading never permits changing: get the person's yes before the first change.

## Steps

1. Check that Todoist isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address `https://ai.todoist.net/mcp`.
3. Name the publisher as Doist, from the repository above. Say who can use it: the whole workspace, or only this project.
4. Hand the person the Todoist sign-in and the Allow (OAuth). Nothing else is theirs to do.
5. List today's tasks through it, so you know it works.

## When it doesn't work

- **The sign-in fails or the add is refused:** Doist also publishes a local server, `npx @doist/todoist-mcp`, which reads a Todoist API token from `TODOIST_API_KEY`: list it in the app's `secret_env`. The person copies the token from Todoist's own settings and pastes it at Set key in Connected apps, never into chat.

## Sources

Doist's public todoist-mcp repository, read 2026-10-29. Written in our own words.
