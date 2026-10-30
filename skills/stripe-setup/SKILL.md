---
name: stripe-setup
description: Use when the work needs a person's Stripe data (customers, payments, invoices, subscriptions) or Stripe's docs and Stripe isn't connected yet. Connects Stripe's own hosted tool server with one sign-in.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Stripe

Stripe runs its own hosted tool server (MCP server) at `https://mcp.stripe.com`, in public preview. Checked on Stripe's docs (docs.stripe.com, the MCP page) on 2026-10-29.

## What it reaches

Any Stripe API read, and any Stripe API create, change or delete: this is the person's money. Get their yes before every change, not just the first. Stripe also asks a person to confirm some changes itself, such as refunds and outgoing payments. It can search Stripe's docs too. SQL analytics needs Stripe Sigma.

## Steps

1. Check that Stripe isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address `https://mcp.stripe.com`.
3. Name the publisher as Stripe, from the docs page above. Say who can use it: the whole workspace, or only this project. Prefer the narrowest scope that does the work.
4. Hand the person the Stripe sign-in and the Allow (OAuth). If the work is a test, they sign in to a Stripe sandbox, not their live account.
5. Read one real list (customers, say) through it, so you know it works.

## When it doesn't work

- **Access is refused for the whole team:** a Stripe team administrator can turn MCP access on or off. Tell the person who must turn it on, and stop.
- **No sign-in is possible:** use a Stripe agent API key, which the Stripe Dashboard marks with an Agent badge. Creating it, and the page that shows it, are the person's. List `Authorization` in the app's `secret_headers`, with the `Bearer` scheme in `key_header_schemes`. The person pastes it at Set key in Connected apps, never into chat. Don't use a full secret key or a restricted key without the Agent badge: Stripe's docs (read 2026-10-29) say it stops accepting those from December.
- **Connected accounts:** sign-in doesn't reach a platform's connected accounts.

## Sources

Stripe's public MCP documentation, read 2026-10-29. Written in our own words.
