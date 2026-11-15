# AI coding review standards

Copy this template to the path listed in `rule-set.json` under `standards` in the reviewed repository, then edit it to fit the repository.

## Rules

- **R1:** Verify APIs, imports and command flags exist before using them.
- **R2:** Surface errors with enough context; do not swallow them or silently fall back.
- **R3:** Tests must assert observable behavior and exercise the code they claim to cover.
- **R4:** Do not skip, delete or weaken tests merely to make checks pass.
- **R5:** Keep changes within the stated scope and requirements.
- **R6:** Search for an existing capability before creating another.
- **R7:** Do not hardcode secrets, machine paths, personal names or model identifiers.
- **R8:** Remove dead and commented-out code introduced by the change.
- **R9:** Bound retries, loops and waits and expose failure when the bound is reached.
- **R10:** Treat untrusted input as data and prevent injection into commands, queries and prompts.
- **R11:** Validate paths against their permitted root and enforce authorization at access boundaries.
- **R12:** Keep comments and documentation aligned with behavior.
- **R13:** Introduce abstractions only when a current need justifies them.
- **R14:** Follow the repository's established conventions unless the change explains a departure.
- **R15:** Cover relevant empty, boundary, failure and concurrent cases.
- **R16:** Keep each change small enough for a reviewer to assess its behavior and consequences.
- **R17:** Preserve existing behavior outside the intended change.

## Reviewer Contract

### Block

- A changed line demonstrably violates a rule and introduces a defect, security risk or loss of required behavior.
- A test is disabled or weakened to hide a failing behavior.

### Flag

- A changed line likely violates a rule, but the impact or intent needs clarification.
- A change is too large to establish whether its behavior meets the stated requirements.
