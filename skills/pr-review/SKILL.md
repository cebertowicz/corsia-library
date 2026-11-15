---
name: pr-review
description: Install and operate an automated pull-request review gate using repository standards and a confined review worker.
license: Apache-2.0
metadata:
  corsia-data: corsia.yaml
  corsia-format: "1"
  version: "1.0.0"
---

# Pull-request review gate

The gate evaluates each pull-request head, stages its diff, conversation and standards, starts the `pr-reviewer` worker, and publishes its verdict. The scripts share a state folder under the owning thread.

## Requires

- CORSIA tools: `automation_schedule`, `event_rules`, `measure_sync` and `measure_status`, and the `start_worker` event-rule action.
- The `pr-reviewer` skill from this library, installed beside this one: the `review` step in `corsia.yaml` and the start rules run it.
- CORSIA's built-in `github-pr-screen` workflow (`screen_workflow` in `rule-set.json`).
- A GitHub account connected to CORSIA.
- A POSIX host (the scripts lock state with `fcntl`), Python 3.11 or later, and `git`.

## Reach

- Subprocesses: `git`, and `corsia github api`, which calls the GitHub API as the connected account.
- Network: the GitHub API, and `git fetch origin <sha>` from the reviewed repository's remote when the checkout lacks a head.
- Paths outside the thread folder: each `repo_checkouts` checkout, where stage runs `git worktree prune` and `git worktree add` (a detached worktree per review, inside the thread's staging folder), and `standards_local_root` if set.
- Writes in shadow mode: local state, staged inputs and shadow records under the thread folder only. Nothing is written to GitHub.
- Writes in live mode, on each configured repository: commit statuses on the head; pull-request review comments and replies; resolving and unresolving the gate's own review threads; one summary comment per pull request, posted once and then edited in place.

## Set up

1. Install this skill and `pr-reviewer` into the owning agent's thread, and connect GitHub with an account allowed to write commit statuses and pull-request comments.
2. Commit `references/review-standards.md` to the reviewed repository at the path listed in `rule-set.json` under `standards`; edit it to suit that repository.
3. Set `repos`, `repo_checkouts`, `agents_login`, optional `sme`, standards paths, status contexts and other settings in `rule-set.json`. Leave `mode` as `shadow`. Set `extended_window_base` only if you configure the corresponding worker profile.
4. Register each of the evaluate, stage and publish scripts with `automation_schedule`: kind `script`, no cadence, authenticated, timeout 300 seconds. Name exactly what each writes in its purpose: evaluate writes local state and emits events; stage writes local state, staged inputs and a detached worktree, and emits events; publish writes local state and shadow records, or in live mode commit statuses, pull-request comments and replies, review-thread resolution, and its own summary comment, and emits events.
5. Resolve the event rules with `review_gate_config.configured_rules(review_gate_config.load())`, then create each with `event_rules create`, substituting `$repos` and the registered script IDs. For an extended-window base, configure the named worker profile. The start rule uses `start_worker` with the worker skill and `request_identity` fact.
6. Run `measure_sync` for both skills' `measures.yaml` files and confirm the result with `measure_status`.
7. Run in shadow mode on a few pull requests and show the person the results. Switching to live mode posts on their repository: ask them, and switch only on their explicit yes. Then re-register the scripts and rules.
8. If a head has no verdict, first check the current head and existing events to avoid a duplicate review. Under `review_gate_evaluate.StateStore.transaction()`, clear that PR's `in_flight` claim; if it was marked reviewed without a published verdict, also clear `reviewed` and `mergeability`. Preserve the number of attempts already used as `attempts` = `{"head": <head>, "n": <used>}` using `put`. Re-firing a completed check run for that head is an action on the person's repository: ask them first. Once it re-fires, evaluate requests the next attempt. Verify the verdict on that exact head; do not reset the attempt to zero, which reuses an already-consumed request identity.

Registered scripts are hash-pinned: changing a file in their folder stops the registered runs. Install changes into a second script folder, register all three there, create the replacement rules, wait for queued old runs to start, disable the old rules, then cancel the old schedules. Tests belong in a separate copy while registered scripts are active.
