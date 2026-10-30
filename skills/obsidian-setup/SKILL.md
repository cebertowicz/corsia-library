---
name: obsidian-setup
description: Use when the work needs to read, search or add to a person's Obsidian vault (notes, daily notes, tasks) and the Obsidian command isn't available yet. Turns on Obsidian's own command-line interface.
license: Apache-2.0
metadata:
  version: "1.0.0"
---

# Connect Obsidian

Obsidian ships its own command-line interface (CLI), the `obsidian` command, from installer version 1.12.7. There is no tool server (MCP server) from Obsidian, so don't point to a community one. Checked on Obsidian's help docs (the Obsidian CLI page) on 2026-10-29.

## What it reaches

The person's vaults, through the running Obsidian app: `search`, `read`, `daily`, `daily:append`, `tasks` and `create`, among others. It can change notes: get the person's yes before the first change.

## Steps

1. Run `obsidian help`. If it works, skip to step 4.
2. **Turn it on, in Obsidian.** The person needs Obsidian installer 1.12.7 or later. They open Settings > General, turn on **Command line interface**, and follow the registration prompt.
   - **macOS:** registering adds the `obsidian` command at `/usr/local/bin/obsidian` and asks for an administrator password. That is the person's.
   - **Linux:** it copies the command to `~/.local/bin/obsidian`, which must be on `PATH`.
3. Start a new shell, since Obsidian says to restart the terminal, then run `obsidian help` again.
4. Search the vault for one word the person names, so you know it works. If Obsidian isn't running, the first command opens it.

## When it doesn't work

- **The command is found but your shell can't run it or reach the app:** your sandbox may block it. Tell the person in one line, and stop. Don't read the vault's files around the CLI unless they give you that folder.
- **An older installer:** updating the app alone isn't enough. The person reinstalls from Obsidian's site.

## Sources

Obsidian's public help documentation (Obsidian CLI), read 2026-10-29. Written in our own words.
