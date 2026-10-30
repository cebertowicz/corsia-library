---
name: gitlab-setup
description: Use when the work needs a person's GitLab projects, issues, merge requests or pipelines and GitLab isn't connected yet. Connects the tool server built into GitLab, on GitLab.com or their own instance, with one sign-in.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect GitLab

GitLab has a tool server (MCP server) built into GitLab itself, on GitLab.com, Self-Managed and Dedicated. Generally available from GitLab 19.5. Checked on GitLab's docs (the MCP server page, docs source in gitlab-org/gitlab) on 2026-10-29.

## Pick the address

- **GitLab.com:** `https://gitlab.com/api/v4/mcp`.
- **Their own instance:** `https://<their GitLab address>/api/v4/mcp`. Ask the person for the address; never guess it from a domain. An instance older than 19.5 may not have it.

## What it reaches

Project information, issues and merge requests, and toolsets covering work items, the repository, CI, wikis and security. It can act on the person's behalf, so get their yes before the first change. It works on the Free tier; some tools need more (GitLab Duo tools need a Duo plan and seats, the security tools need Ultimate).

## Steps

1. Check that GitLab isn't already in Connected apps. If it is, use it.
2. **Make sure it's allowed.** On GitLab.com it must be turned on for the person's top-level group; on their own instance, for the instance. If they can't change that setting, tell them who must, and stop.
3. Add a connected app by the address picked above.
4. Name the publisher as GitLab, from the docs page above. Say who can use it: the whole workspace, or only this project.
5. Hand the person the GitLab sign-in and the Allow (OAuth).
6. Read one real issue or merge request list through it, so you know it works.

## When it doesn't work

- **The sign-in fails because the instance has turned off app self-registration:** someone creates a GitLab OAuth application with the `mcp` scope and "Confidential" cleared (an administrator for an instance application, a group Owner for a group application, or the person for their own). Put its application ID in the app's `oauth_client_id`, then sign in again. The application ID isn't a secret; there is no client secret to handle.
- **A personal access token instead:** GitLab also accepts a fine-grained personal access token with the MCP tool's Execute permission, but its docs page doesn't say which header carries it. Don't set one up until the docs say how. Old-style personal, project and group tokens are refused.
- **Too many sign-in attempts:** GitLab limits app self-registration to 10 an hour from one address. Wait, then sign in once.

## Sources

GitLab's public MCP server documentation, read 2026-10-29. Written in our own words.
