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

## Set up

1. Connect GitHub with an account allowed to write commit statuses and pull-request comments.
2. Commit `references/review-standards.md` to the reviewed repository at the path listed in `rule-set.json` under `standards`; edit it to suit that repository.
3. Set `repos`, `repo_checkouts`, `agents_login`, optional `sme`, standards paths, status contexts and other settings in `rule-set.json`. Leave `mode` as `shadow` initially. Set `extended_window_base` only if you configure the corresponding worker profile.
4. Register each of the evaluate, stage and publish scripts with `automation_schedule`: kind `script`, no cadence, authenticated, timeout 300 seconds. Name exactly what each writes in its purpose: evaluate writes local state and emits events; stage writes local state, staged inputs and a detached worktree, and emits events; publish writes local state and shadow records, or commit statuses and pull-request comments in live mode, and emits events.
5. Resolve the event rules with `review_gate_config.configured_rules(review_gate_config.load())`, then create each with `event_rules create`, substituting `$repos` and the registered script IDs. For an extended-window base, configure the named worker profile. The start rule uses `start_worker` with the worker skill and `request_identity` fact.
6. Run `measure_sync` for both skills' `measures.yaml` files and confirm the result with `measure_status`.
7. Run in shadow mode on a few pull requests, inspect results, then switch to live mode when satisfied and re-register the scripts and rules.
8. If a head has no verdict, first check the current head and existing events to avoid a duplicate review. Under `review_gate_evaluate.StateStore.transaction()`, clear that PR's `in_flight` claim; if it was marked reviewed without a published verdict, also clear `reviewed` and `mergeability`. Preserve the number of attempts already used as `attempts` = `{"head": <head>, "n": <used>}` using `put`. Re-fire a completed check run for that head so evaluate requests the next attempt. Verify the verdict on that exact head; do not reset the attempt to zero, which reuses an already-consumed request identity.

Registered scripts are hash-pinned: changing a file in their folder stops the registered runs. Install changes into a second script folder, register all three there, create the replacement rules, wait for queued old runs to start, disable the old rules, then cancel the old schedules. Tests belong in a separate copy while registered scripts are active.
