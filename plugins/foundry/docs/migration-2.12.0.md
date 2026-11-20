# Foundry 2.12.0 — upgrade and rollback, 2026-11-20

**Guide for the PLP-390 source candidate, from 2.11.1.** See the
[release note](release-2.12.0.md) for scope and preparation status.
Tag, GitHub Release, host installation and listed / installed / loaded versions are not
established by this preparation.

## Prerequisites and data

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including ledgers,
receipts and review history; the existing [data and rollback
rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the reference. No environment
read and no `add_argument` is added under `tooling/` (read from the diff between the tag
`foundry-v2.11.1` and this candidate). The new options `--dry-run`, `--tracker` and
`--marker-only` are read by hand in `setup_project.py` and `registry.py`, not by
`add_argument`. `registry rebind` rewrites an existing registry entry (`_save`) and the
checkout's marker (`_publish_marker`) ([registry.py](../tooling/foundry/registry.py),
`rebind_repository_binding`); it takes `_cutover_lock`, which
creates the data directory and its `.registry-cutover.lock` when they are absent (read from
the code, not run). Outside `tooling/`, the PRs merged since that tag touch only READMEs,
changelog fragments, docs, skills, tests and this repository's own marker
`.foundry/tracker.json`; this release PR adds the manifests, `CHANGELOG.md` and a comment in
`scripts/ci_plan.py`: no configuration key changes.

## What changes for a user

- **New verb `query cleanup` and skill `foundry:cleanup` (PLP-365, PLP-389).**
  `foundry_cli.py query cleanup` is registered in `_CMDS` of
  [query.py](../tooling/foundry/query.py) and takes no argument (it exits with
  `usage: query cleanup (no argument)` otherwise). The proposal logic, the `unsafe` cases and
  the fail-closed behaviour on a failed `git status` or `rev-list` are in
  [cleanup.py](../tooling/foundry/cleanup.py) (`propose`); the skill and the scheduling advice
  are in the [cleanup skill](../skills/cleanup/SKILL.md) and the
  [README](../README.md). Invoke it as `/foundry:cleanup` (Claude Code) or `$foundry:cleanup`
  (Codex).
- **New verb `registry rebind` (PLP-385, PLP-386).** `registry rebind <tracker>
  <old-repository> <new-repository> [--marker-only]` is handled in
  [registry.py](../tooling/foundry/registry.py) (`rebind_repository_binding`). Its refusals,
  all raised before the registry or the marker is written, are the ones of the CHANGELOG
  section: a GitHub Projects transfer to another owner, a rename colliding with a legacy
  entry of another provider, and a checkout with no marker (its message gives the steps).
  When the provider does not carry the new identity, `rebind` refuses with the transfer steps
  and writes neither (the `ProjectIdentityMismatchError` of `verify_existing_project` is
  turned into that message). The rule per provider is in
  [tracker-contract.md](tracker-contract.md) (paragraph `rebind`) and the steps in the
  [doctor skill](../skills/doctor/SKILL.md).
- **New skill `foundry:new-project` and `setup_project` options (PLP-384, PLP-387).**
  [setup_project.py](../tooling/foundry/setup_project.py) accepts `--dry-run` and
  `--tracker <name>`; the skill is described in the
  [new-project skill](../skills/new-project/SKILL.md). `--import-adrs` is checked by
  `check_adr_dir` before any write.
- **`DevHubProjectScopeError` and the conflict branches (PLP-384).** A DevHub
  `403 insufficient_scope` on the project resolution raises it
  ([devhub.py](../tooling/foundry/trackers/devhub.py)); it is a `TrackerConflictError`, so a
  script that matched the former `RuntimeError` on a bound checkout now sees
  `status: "conflict"` from `issue` and `query` (CHANGELOG section; the exit path of each verb
  was not run for this guide). `doctor` prints its message whole (`_failure_detail` in
  [doctor.py](../tooling/foundry/doctor.py)). Causes and remedies:
  [devhub-tracker.md](devhub-tracker.md).
- **Documentation only (PLP-382).** No tool change.

## Official host procedures: references, not executed here

The strings below reference the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and [configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They are examples, not procedures run for 2.12.0 on either host. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#8-stop--host-update). The marketplace name
`patolabs` was read on 2026-11-20 from both hosts' marketplace listings
(`claude plugin marketplace list`, `codex plugin marketplace list`) on the maintainer's
machine.

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 8–10, with `2.12.0` as the
expected version. Manager listing, installed manifest and session-loaded version
remain separate observations per host; none is supplied by this preparation.

## Return to 2.11.1: not qualified

An official-manager downgrade to 2.11.1 has not been exercised here, and no reverse
migration is provided. Deduced from the diff between the tag `foundry-v2.11.1` and this
candidate, not run: 2.11.1 has no `cleanup` entry in `_CMDS` of `query.py` and no `rebind`
branch in `registry.py`, so a return removes both verbs; it also removes the `--dry-run` and
`--tracker` options of `setup_project`. A registry entry or marker already rewritten by
`rebind` is not reverted by the downgrade (deduced, not run).
