---
name: google-maps-setup
description: Use when the work needs to find places, plan routes, check the weather somewhere or resolve a location through Google Maps and Google Maps isn't connected yet. Connects Google Maps Platform's own hosted tool server with a key.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Google Maps

Google Maps Platform runs its own hosted tool server (MCP server) at `https://mapstools.googleapis.com/mcp`, over Streamable HTTP. Checked on Google's docs (developers.google.com, Maps grounding for agents) on 2026-10-29.

## What it reaches

Place search, weather, routes, and turning place names into locations and Maps links. It doesn't give turn-by-turn directions. It only looks things up. When you pass on what it returns, keep Google Maps' attribution with it, as Google's terms require.

## What the person needs

A Google Cloud project with the Maps Experience agent tools API (`mapstools.googleapis.com`) turned on, and billing on, which is the person's choice. Google lists a free monthly allowance (10,000 Essentials and 5,000 Pro requests on 2026-10-29), and Pro results only for projects Google has allowed. For a quick test, Google offers a demo key that needs no billing.

## Steps

1. Check that Google Maps isn't already in Connected apps. If it is, use it.
2. **Set up the Google Cloud project.** If you can drive a browser, do the console steps yourself: create or pick the person's project, and turn on the Maps Experience agent tools API (`mapstools.googleapis.com`). Hand the person only what agents must not do: the Google sign-in, choosing billing, and creating the API key, since the page that creates it shows the key. With no browser, the person does these steps themselves.
3. Add a connected app by web address `https://mapstools.googleapis.com/mcp`. List `X-Goog-Api-Key` in its `secret_headers`.
4. Name the publisher as Google, from the docs page above. Say who can use it: the whole workspace, or only this project.
5. The person pastes the key at Set key in Connected apps, never into chat.
6. Look up one place through it, so you know it works.

## When it doesn't work

- **Refused for billing, or the API isn't on:** finish step 2: turn the API on, and ask the person to set up billing. Billing choices are theirs.
- **Pro results missing:** Pro needs Google's allowlist during its limited release. Use the Essentials results.
- Google also offers OAuth sign-in for this server, but it needs a client secret, which Connected apps doesn't take. Use the key.

## Sources

Google Maps Platform's public documentation, read 2026-10-29 (page last updated 2026-10-28). Written in our own words.
