---
name: elevenlabs-setup
description: Use when the work needs generated speech, voice cloning, transcription or audio cleanup through a person's ElevenLabs account and ElevenLabs isn't connected yet. Connects ElevenLabs' own hosted tool server with one sign-in.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect ElevenLabs

ElevenLabs runs its own hosted tool server (MCP server) at `https://api.elevenlabs.io/v1/mcp`, with OAuth sign-in. Checked on ElevenLabs' own repository for it (github.com/elevenlabs/elevenlabs-mcp) on 2026-10-29. That repository's local server is deprecated, so don't use it.

## What it reaches

The signed-in person's ElevenLabs account: speech from text, voice cloning, transcription, audio isolation and speech-to-speech. Most of these spend the account's credits. The free tier has 10,000 credits a month. Say roughly what a job will use before running it, and get the person's yes before cloning a voice.

## Steps

1. Check that ElevenLabs isn't already in Connected apps. If it is, use it.
2. Add a connected app by web address `https://api.elevenlabs.io/v1/mcp`.
3. Name the publisher as ElevenLabs, from the repository above. Say who can use it: the whole workspace, or only this project.
4. Hand the person the ElevenLabs sign-in and the Allow (OAuth). Nothing else is theirs to do.
5. Make one small, cheap call through it, such as listing the account's voices, so you know it works.

## When it doesn't work

- **Out of credits:** the person decides whether to buy more or change plan, in ElevenLabs. Never open billing yourself.

## Sources

ElevenLabs' public elevenlabs-mcp repository, read 2026-10-29. Written in our own words.
