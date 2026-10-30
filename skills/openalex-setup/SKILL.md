---
name: openalex-setup
description: Use when the work needs scholarly research data (papers, authors, journals, institutions, topics, citations) through OpenAlex and OpenAlex isn't connected yet. Connects OpenAlex's own hosted tool server with one sign-in.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect OpenAlex

OpenAlex runs its own hosted tool server (MCP server) at `https://mcp.openalex.org/mcp`, over Streamable HTTP with OAuth sign-in. Any client that speaks that can connect. Checked on OpenAlex's help pages (help.openalex.org, the AI agent connector page, last updated 2026-10-03) and its entry in the official MCP registry (`org.openalex/openalex`) on 2026-10-29.

## What it reaches

Searches and reads OpenAlex's catalogue of works, authors, sources, institutions and topics. The only thing it can change is the person's own OpenAlex author profile, and only when they ask, so get their yes before any profile change.

## What the person needs

A free OpenAlex account. Queries spend the account's daily budget; a search with the default reranking costs more than a plain one. When the budget runs out, single-record lookups still work and the rest resumes at the daily reset. Adding paid usage or a plan is the person's choice.

## Steps

1. Check that OpenAlex isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address `https://mcp.openalex.org/mcp`.
3. Name the publisher as OpenAlex, from the help page above. Say who can use it: the whole workspace, or only this project.
4. Hand the person the OpenAlex sign-in and the approval (OAuth). If they have no account, signing up is theirs too.
5. Look up one real work or author through it, so you know it works.

## When it doesn't work

- **Searches stop partway through the day:** the daily budget is spent. Use single-record lookups, wait for the reset, or tell the person their budget ran out; paying for more is their call.
- **Don't put a key in the web address.** OpenAlex's plain API takes a key as a query parameter, but Connected apps refuses addresses that carry credentials, and the tool server uses sign-in instead.

## Sources

OpenAlex's public help pages and its official MCP registry entry, read 2026-10-29. Written in our own words.
