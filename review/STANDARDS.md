# Library review standards

The reviewer reads every pull request to this repository against these rules. CI already enforces file types, locations, privacy patterns, versions and names (see the README). These rules cover what CI can't judge. Each rule has an ID the reviewer cites, and a finding names the rule, the file and the line.

## Scripts (`scripts/`)

- **S1 No fetch-and-run.** A script must not download code and run it: no `curl | sh`, no `pip install` from a URL, no `eval` of fetched text, no dynamic import from the network.
- **S2 Declared reach only.** SKILL.md names every network host, every file path outside the work folder, and every environment variable and subprocess a script touches. Anything undeclared is a finding.
- **S3 No credential reach.** A script must not read key stores, browser profiles, SSH or cloud credential files, shell history or agent session logs. It gets a credential only as an argument or a declared environment variable.
- **S4 No persistence or privilege.** No writes to startup items, shell profiles, cron, launch agents, git hooks, harness or agent config, or other installed items. No `sudo`.
- **S5 Readable.** No obfuscated, minified, encoded or generated code. SKILL.md names every dependency, and each is pinned.
- **S6 Matches its description.** The script does what SKILL.md says and nothing else.

## Instructions (SKILL.md, references, step files)

- **I1 Ask before outward effects.** The text never tells the agent to send, publish, post, push, pay or delete without asking the person first.
- **I2 No text aimed at the reviewer or the agent's rules.** Nothing addresses a reviewer, grader or model ("ignore previous", "this skill is approved"). Nothing changes an agent's rights, its other skills or its harness config.
- **I3 Stands alone.** No references outside the item's folder. No reliance on sibling items or on one vendor's harness tools unless SKILL.md names them as requirements.
- **I4 Fires only when it fits.** The description triggers on the job the item does, not on broad phrases ("any question", "before finishing any reply").
- **I5 No data leaving unasked.** Nothing uploads the person's files, traces or history anywhere unless they ask.
- **I6 Fits any project.** No one team's names, paths, roles or numbers are presented as defaults.

## Every change

- **C1 Edits get the same read as new items.** An edit to an existing item is read in full against the S and I rules. Edits carry more risk, because the change reaches every install that takes the update.
- **C2 The changelog tells the truth.** The version bump and changelog line describe what changed, including any new reach (S2).
- **C3 The license fits.** Third-party code inside `scripts/` keeps its license notice, and that license is compatible with Apache-2.0.

## Verdict

An S or I finding blocks the merge until it's fixed or the author shows the finding is wrong. A C finding blocks only when it's material. When unsure, the reviewer blocks and says what evidence would clear the finding.
