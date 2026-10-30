---
name: product-design
description: >-
  Steps for recurring product requirements and design work, including a design-only deliverable before anything is built: ground the current experience, record requirements, verify human and agent journeys, resolve required approvals, and hand off the exact design. Not for already-specified small fixes or implementation. No test results yet.
license: Apache-2.0
metadata:
  version: 1.0.0
  corsia-data: corsia.yaml
  corsia-format: '1'
---

# Product design

No test results yet: this has real runs behind it, its latest fixes haven't had a fresh run, and nobody has measured whether it beats doing without. It supplies checkpoints, not a design standard or a source of authority; use your project's own design rules. It can finish with a design alone, with no code.

Do every required step, even when its check already has evidence. Skip human design only when no human-facing experience changes, and agent design only when no agent-facing surface changes, and record why. The accountable owner keeps intent, alternatives, design judgment, recommendations and acceptance. Workers may gather bounded evidence or render a design that's already specified.

## Applying these steps

1. **Ground — accountable owner.** Read the whole request, the current experience as people actually meet it, requirements, prior decisions and applicable standards. Establish the audience, the authorized scope and the strongest plausible counterexample. Keep observations, assumptions and missing evidence apart. Don't narrow the request to its example.
2. **Requirements — accountable owner.** Record the reasoning that matters: the simplest credible alternatives, doing nothing included, measurable obligations, relevant states and failures, cost, dependencies and what's out of scope. Scale the record to the consequence. Don't invent a concept or an approval to fill a template.
3. **Human design — owner, with a reviewer where your project has one.** Use your project's design rules. Inspect actual renders across the required journeys and states. If you can't get access, hold that check. Keep any existing review and approval stops.
4. **Agent design — accountable owner.** Check what an agent is actually given, its first decisions and how it recovers from failure. Record evidence from a fresh turn and its limits; edited source files prove nothing. If required verification isn't available, hold acceptance of that part.
5. **Acceptance — accountable owner.** Check the complete design against the requirements and the counterevidence, and settle each finding. Ask what a skilled designer would notice beyond the checks: something correct on its own can break the journey next to it. Check that boundary and record who owns it and the evidence. Resolve required choices and reviews through their usual channels. Silence is never approval.
6. **Handoff — accountable owner.** Put the design and its numbered changes, exact scope, acceptance evidence and open limits where the next person can read them. Keep later work dependent on the required acceptance. Finishing a design grants no authority to build, spend or publish.

## Judging a run

Look at recipient corrections, requirements that drifted, journey failures that got through, and total cost including reviews and rework. Keep the person's questions, approval boundaries and limits when you revise this. Simplify or retire it if ordinary plans serve better.
