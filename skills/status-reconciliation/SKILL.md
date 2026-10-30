---
name: status-reconciliation
description: >-
  Steps for a recurring standup or status sweep across several owners' work: collect current sources, reconcile work and open asks, update the status page, verify it, and send only what changed. Not for a single status question, doing the work itself, filing backlog items, or a decision alone. No test results yet.
license: Apache-2.0
metadata:
  version: 1.0.0
  corsia-data: corsia.yaml
  corsia-format: '1'
---

# Status reconciliation

No test results yet: this comes from real recurring standups, and nobody has measured whether it beats doing without. Keep scope, cadence, recipients and the choice of page in the run, not in this definition. Reuse the reporting you already have; don't create another schedule or status store.

## Applying these steps

1. **Collect — accountable owner of the sweep.** Establish the whole requested scope and a snapshot time. Read current plans, direct requests, decisions, holds and evidence of outcomes. Mark inaccessible, stale or missing inputs. A list of who's on the team shows coverage, not progress. Bounded workers may gather specified evidence; they don't decide status or priority.
2. **Reconcile — accountable owner.** Give every in-scope source a disposition. Keep reported, verified, merged, live, accepted and paused distinct, using what the source actually means. Check unresolved returns and asks before new work. Keep contradictions visible. Surface only unanswered material decisions that existing authority can't settle. Never infer approval from a status report or change another owner's work because of one.
3. **Update — owner or a bounded worker.** Amend the status page with source and time, keeping unrelated edits. Domain truth stays with its owner. Move finished items out of active tables, keeping their evidence where it belongs, and keep unresolved dependencies and approval stops. See "Writing for the person who decides" below.
4. **Verify — accountable owner.** Check whole-scope coverage and every changed row against current sources. Check links and presentation. Ask what a skilled coordinator would notice beyond these checks: a fresh-looking page can combine observations from different times. Record that limit, or refresh the affected sources before claiming a common state. Send defects back to the step that caused them.
5. **Deliver — accountable owner.** Send only the useful change, blocker or supported decision through the audience's usual channel. Record handoff and verification limits in your plan, not on the reader's page, and don't assume it was read or agreed. Keep follow-through in existing dependencies. Nothing here authorizes new assignments, model changes or public actions.

## Writing for the person who decides

- The page's first lines say what's blocking the thing they care about most. No preamble.
- List only asks that are ready for them. Each says what it is, why it needs them, where to go and how long it takes. Open any linked page first and confirm it exists. An item that isn't ready stays off the list, named in one line below it.
- Keep every ask still awaiting an answer in one ordered list. A note saying not to re-ask means don't message them again, never leave it off. Rank what blocks the main goal first, then what leaves people idle, shortest first, then everything else, biggest unblock first.
- Edit the page directly, then say once that it's ready.

## Keeping pages fresh

In every sweep, check each page you own whose reason to update has come up since its last edit. Refresh it in that sweep, or hand it to whoever writes it, so nothing stays stale under your name.

## Judging a run

Look at stale or false status that got through, recipient corrections, attention spent, and total cost including rework. Missing history stays unknown. If ordinary plans and your existing reporting work as well with less overhead, simplify or retire this. Keep every ask, approval boundary and limit when you revise it.
