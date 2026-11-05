---
name: slack-setup
description: Use when the work needs to read, search or post in a person's Slack and Slack isn't connected yet. Creates the person's own Slack app from a manifest, then connects Slack's hosted tool server with one sign-in.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Slack

Slack runs its own hosted tool server (MCP server) at `https://mcp.slack.com/mcp`. Checked on Slack's developer docs (docs.slack.dev, the MCP server and app manifest pages) on 2026-10-29.

Slack lets only internal apps or apps published in its directory use that server, and it doesn't let an unknown tool register itself. So each person makes their own internal app, named CORSIA, from the manifest in this skill. Needs CORSIA 0.4.1 or later.

## What it reaches

What the signed-in person can see: public and private channels they're in, direct messages and search. It can post as them. Reading never permits posting: get the person's yes before the first message.

## Steps

1. Check that Slack isn't already in Connected apps. If it is, use it.
2. **Create the app.** Run `steps/create-app.json`. It opens Slack's create-app page with the manifest filled in. If steps from installed skills can't run yet, open the same address yourself. The person must be signed in to Slack in that browser, then picks the workspace and confirms Create. Choosing the workspace is theirs.
3. **Get the client ID.** On the new app's Basic Information page, read only the **Client ID** field with one `browser_eval` that returns that field's value and nothing else. It isn't a secret. The same page holds the client secret and signing secret: never snapshot or screenshot it, never click Show, and never read any other field. The sign-in doesn't use them. If that one-field read misses, ask the person to copy only the Client ID into chat.
4. **Add the connected app** by web address `https://mcp.slack.com/mcp`. Set `oauth_client_id` to that Client ID, and `oauth_callback` to host `localhost`, port `53682`, path `/callback`. These must match the manifest's redirect address exactly. Name the publisher as Slack, and say who can use it: the whole workspace, or only this project.
5. **Sign in.** Hand the person Slack's sign-in and the Allow. Allowing also installs the app for them.
6. Read one real channel through it, so you know it works.

## When it doesn't work

- **The workspace only allows approved apps:** Slack shows the person a request or a block instead of Allow. Workspace admins approve and manage these. Tell the person in one line who has to approve it, and stop there.
- **Free workspaces** allow 10 apps in total. If they're at the limit, the person decides which to remove.
- **Changing the manifest's permissions later** needs the person to sign in and Allow again.
- **A step misses** because Slack changed the page: finish creating the app yourself from that step, then offer the fixed step file back to the library.

## The manifest

The step file carries it already. The same text is here so you can paste it on Slack's "From a manifest" screen by hand:

```yaml
display_information:
  name: CORSIA
oauth_config:
  redirect_urls:
    - http://localhost:53682/callback
  pkce_enabled: true
  scopes:
    user:
      - search:read.public
      - search:read.private
      - search:read.mpim
      - search:read.im
      - channels:history
      - groups:history
      - mpim:history
      - im:history
      - channels:read
      - groups:read
      - im:read
      - mpim:read
      - chat:write
settings:
  token_rotation_enabled: true
```

## Sources

Slack's public MCP server and app manifest documentation, read 2026-10-29. Written in our own words.
