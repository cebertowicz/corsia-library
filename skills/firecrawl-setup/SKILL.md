---
name: firecrawl-setup
description: Use when the work needs to search the web, scrape or parse pages, or crawl a site through Firecrawl and Firecrawl isn't connected yet. Connects Firecrawl's own hosted tool server, keyless first.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Firecrawl

Firecrawl runs its own hosted tool server (MCP server) at `mcp.firecrawl.dev`, over HTTP. Checked on Firecrawl's docs (docs.firecrawl.dev, the Firecrawl MCP page) on 2026-10-29.

## Pick the address

- **No account (start here):** `https://mcp.firecrawl.dev/v2/mcp`. Search, scrape and parse, within daily limits. It answered without a key on 2026-10-29.
- **The person's Firecrawl team:** `https://mcp.firecrawl.dev/v2/mcp-oauth`, with sign-in. Every tool their team's plan allows.

## What it reaches

Pages on the public web. It reads nothing private.

## Steps

1. Check that Firecrawl isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address `https://mcp.firecrawl.dev/v2/mcp`, with no key.
3. Name the publisher as Firecrawl, from the docs page above. Say who can use it: the whole workspace, or only this project.
4. Scrape one page through it, so you know it works.

## When it doesn't work

- **The daily limit is reached, or the work needs a tool the keyless address lacks:** add `https://mcp.firecrawl.dev/v2/mcp-oauth` instead and hand the person the Firecrawl sign-in, where they pick their team and Allow (OAuth). An account, and any plan or payment choice, is theirs.
- **No sign-in is possible:** the `https://mcp.firecrawl.dev/v2/mcp` address also takes a Firecrawl API key as a bearer token. Creating the key, and any page that shows it, are the person's, not yours. List `Authorization` in the app's `secret_headers`, with the `Bearer` scheme in `key_header_schemes`. The person pastes the key at Set key in Connected apps, never into chat.

## Sources

Firecrawl's public MCP documentation, read 2026-10-29. Written in our own words.
