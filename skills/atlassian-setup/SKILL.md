---
name: atlassian-setup
description: Use when the work needs a person's Jira, Confluence or other Atlassian Cloud content and Atlassian isn't connected yet. Connects Atlassian's own hosted tool server with one sign-in.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Atlassian (Jira, Confluence)

Atlassian runs its own hosted tool server (MCP server) at `https://mcp.atlassian.com/v2/mcp`, over HTTP. Checked on Atlassian's docs (the Atlassian MCP server support page, and the atlassian/atlassian-mcp-server repository) on 2026-10-29.

## What it reaches

Search and read Jira, Confluence, Bitbucket and other Atlassian Cloud products, and create or update work items and pages. Get the person's yes before the first change. Deleting or managing Jira items works only once an Atlassian admin turns it on. Some calls use Rovo credits, which are the person's to spend. It needs an Atlassian Cloud site.

## Steps

1. Check that Atlassian isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address `https://mcp.atlassian.com/v2/mcp`. Don't use the old `/v1/sse` address: it stopped being supported after June 30, 2026.
3. Name the publisher as Atlassian, from the docs page above. Say who can use it: the whole workspace, or only this project.
4. Hand the person the Atlassian sign-in and the Allow (OAuth).
5. Search one real Jira project or Confluence space through it, so you know it works.

## When it doesn't work

- **The work needs Jira Service Management:** those tools work only with an API token, not sign-in.
- **Using an API token:** an Atlassian organization admin must first turn on API token sign-in, and give the person a service account API key. Creating it, and the page that shows it, belong to the person or their admin. List `Authorization` in the app's `secret_headers`, with the `Bearer` scheme in `key_header_schemes`. The person pastes it at Set key in Connected apps, never into chat. A personal API token uses a different scheme (`Basic`); don't set one up unless the docs give a form Connected apps takes.
- **Bitbucket or Loom are missing:** Bitbucket needs a workspace linked to the Atlassian organization; Loom needs a site linked to Atlassian.

## Sources

Atlassian's public MCP server documentation and repository, read 2026-10-29. Written in our own words.
