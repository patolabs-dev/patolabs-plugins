# Foundry 2.12.0 — release note, 2026-11-20

**Source candidate prepared under PLP-390.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.12.0`.
The minor-release requirements are `release_kind` and `release_requirements` in
[test_release_contract.py](../tests/test_release_contract.py); upgrade references are in
[migration-2.12.0.md](migration-2.12.0.md). This note covers the Claude Code and Codex hosts.

## Behaviour change to read first

Two CLI verbs are new: `query cleanup` (PLP-365, PLP-389), registered in `_CMDS` of
[query.py](../tooling/foundry/query.py), and `registry rebind <tracker> <old-repository>
<new-repository> [--marker-only]` (PLP-385, PLP-386), handled in
[registry.py](../tooling/foundry/registry.py) (`rebind_repository_binding`). The skills
`foundry:cleanup` and `foundry:new-project` are new, and `setup_project` gains `--dry-run` and
`--tracker` (PLP-384, [setup_project.py](../tooling/foundry/setup_project.py)).
One behaviour of an existing path changes: `DevHubProjectScopeError`
([devhub.py](../tooling/foundry/trackers/devhub.py)) is a `TrackerConflictError`, so on a bound
checkout it enters the conflict branches of `issue` and `query` where a `RuntimeError` surfaced
before (PLP-384). What each means for a user is in
[migration-2.12.0.md](migration-2.12.0.md#what-changes-for-a-user).

## Scope and sources

The `2.12.0` section of the [CHANGELOG](../CHANGELOG.md) contains PLP-389, PLP-386, PLP-385,
PLP-384, PLP-382 and PLP-365, assembled byte-for-byte by
[`assemble`](../scripts/changelog_assemble.py). This preparation removes those six fragments
and leaves `Unreleased` empty. PLP-387 has no fragment: it amended the PLP-384 fragment, which
carries its `--import-adrs` check (`check_adr_dir` in
[setup_project.py](../tooling/foundry/setup_project.py)).

| Issue | Packaged artifact | Behaviour and evidence source |
| --- | --- | --- |
| PLP-365, PLP-389 | `query cleanup`; skill `foundry:cleanup` | [cleanup.py](../tooling/foundry/cleanup.py) (`propose`); [query.py](../tooling/foundry/query.py); [cleanup skill](../skills/cleanup/SKILL.md); [README](../README.md) |
| PLP-385, PLP-386 | `registry rebind`, `--marker-only`, refusals | [registry.py](../tooling/foundry/registry.py) (`rebind_repository_binding`, `ProjectIdentityMismatchError`); [tracker-contract.md](tracker-contract.md); [doctor skill](../skills/doctor/SKILL.md) |
| PLP-384 (and PLP-387) | skill `foundry:new-project`; `setup_project --dry-run`, `--tracker`; `check_adr_dir`; `DevHubProjectScopeError` | [setup_project.py](../tooling/foundry/setup_project.py); [devhub.py](../tooling/foundry/trackers/devhub.py); [doctor.py](../tooling/foundry/doctor.py); [new-project skill](../skills/new-project/SKILL.md); [devhub-tracker.md](devhub-tracker.md) |
| PLP-382 | Documentation, comments and tests only | [merge-pr skill](../skills/merge-pr/SKILL.md); [campaign-effects.md](campaign-effects.md); [kpi-export.md](kpi-export.md); [token_attribution.py](../tooling/foundry/token_attribution.py) |

## Publication status

Release merge, tag `foundry-v2.12.0`, GitHub Release, host installation, and listed /
installed / loaded version readbacks on Claude Code and Codex are not done or claimed by this
preparation. The merge, tag, GitHub Release and host-update STOPs each require their own
explicit agreement ([release skill](../skills/release/SKILL.md), steps 5–8); readbacks and
their release-ticket record are specified by steps 7–10. This page supplies no review verdict
or CI result. The triage of the open follow-up issues required before this PR
([release skill](../skills/release/SKILL.md), step 4) is recorded on the release ticket
PLP-390, not here.

The changelog section and these release pages are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.12.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status |
| --- | --- |
| Both manifest versions | Updated from `2.11.1` to `2.12.0`; no other manifest field changed |
| Dated changelog and the six fragments | Updated: byte-identical entries assembled into `2.12.0 — 2026-11-20`; fragments removed |
| Release note and migration guide | New: this page and [migration-2.12.0.md](migration-2.12.0.md) |
| Root and Foundry READMEs | Updated: source-candidate status, version, release/migration pointers and release-version list |
| `scripts/ci_plan.py` release-version comment | Updated: `2.11.0` and `2.11.1` marked published and tagged, `2.12.0` added; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: `2.12.0` entry added; earlier entries retained |
| CLI, configuration and behaviour implementation | No change in this preparation; the source documentation of each issue is linked above |
