---
name: new-project
description: >
  Starts a fresh repository under Foundry: preflight of the provider and key, explicit
  confirmation, `setup_project`, the marker committed on a branch with a PR, then `doctor`.
  USE WHEN the user invokes /foundry:new-project (Claude Code), $foundry:new-project
  (Codex), "new project", "nouveau projet", or "démarrer un dépôt sous Foundry".
argument-hint: "<Name> <KEY> [--tracker <name>]"
allowed-tools: Bash(python3:*), Bash(git:*)
---

# new-project

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present, otherwise the Codex fallback. For Codex, derive
`<foundry-root>` from this skill's absolute path by removing `/skills/new-project/SKILL.md`.
Never pass the placeholder literally.

Run from the checkout of the new repository (its `origin` remote must exist: creating the
GitHub repository, CI and runners are out of scope). `<repo>` is the basename of that
checkout's `origin`. Ask the user for the project name, the key and, if the host default
provider may not be the wanted one, the provider; never assume the host default.

## 1. Preflight (read-only)
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" setup_project "<Name>" <KEY> <repo> --dry-run [--tracker <name>] [--import-adrs <dir>]
```
It runs the local proofs of `setup_project` (no marker, no canonical or legacy registry
entry under any provider, no registry entry already named after the repository) and the provisioning-support check, and, with `--import-adrs`, a read-only check of
the directory (`setup_project.check_adr_dir`: filenames, parsing, numbering), then prints
the provider, the key, the repo and, when the provider requires it, the canonical origin
identity; it writes nothing, locally or at the provider (`tooling/foundry/setup_project.py`,
`setup(..., dry_run=True)`). A refusal means the checkout is already linked or the provider
cannot provision: say it in one sentence and stop; recovery of an existing binding belongs to
`foundry:doctor` (`registry bootstrap`, `registry rebind`), not to this skill.

## 2. Confirm
Show the user the provider and the key printed by step 1 and ask for an explicit
confirmation. Do nothing that writes at the provider before a clear yes. If the provider is
not the wanted one, re-run step 1 with `--tracker <name>` (`youtrack`, `linear`,
`ghprojects`, `devhub`; only `youtrack` and `devhub` provision, `project_provisioning_supported`).

## 3. Provision
Same arguments as step 1, without `--dry-run`, and with `--tracker <provider confirmed in
step 2>` always passed (otherwise the provider is re-read from `FOUNDRY_TRACKER`):
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" setup_project "<Name>" <KEY> <repo> --tracker <name> [--import-adrs <dir>]
```
After a partial failure (interruption, network) before the registry write, re-run the same
command: it recovers the same provider project, never a duplicate (`docs/devhub-tracker.md`,
"Project binding and cutover"). After the registry write (marker write failed, or
`--import-adrs` failed), `setup_project` refuses the re-run because the entry exists
(`setup_project.py`): go to `foundry:doctor`. A DevHub `403 insufficient_scope` on the
project resolution means, as a deduced cause, that the project was created outside Foundry
and the service token has no grant on it: grant the Foundry service token access to the
project in DevHub, then re-run (or bind with `registry bootstrap` once granted); report this
to the user instead of retrying.

## 4. Marker and PR
For a provider whose project carries `canonical_repo` (DevHub), `setup_project` also wrote
the marker `.foundry/tracker.json` (`setup_project.py`, `registry.bootstrap_repository_binding`);
if the file does not exist, there is nothing to commit (YouTrack, for one, leaves only a
legacy registry entry named after the repository, no marker): tell the user why and skip
to step 5. Otherwise check
whether Git ignores it:
```bash
git check-ignore -q .foundry/tracker.json
```
Exit 0: the repository ignores the marker, so there is nothing to commit; tell the user why
and skip to step 5. Otherwise create a bootstrap issue in the new project, start it, commit
the marker on its branch and open the PR (never `gh pr create`, never a push to the default
branch, R1):
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit create-issue '{"title":"Bind the repository to its tracker project","body":"Commit .foundry/tracker.json."}'
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue start <ISSUE-ID>
git add .foundry/tracker.json
git commit -m "chore: bind the repository to its tracker project"
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue openpr <ISSUE-ID> --summary-stdin < /path/to/summary.md
```
Write a short summary to a scratch file first (`/path/to/summary.md`). `<ISSUE-ID>` is the id printed by `create-issue`. If `create-issue` fails after a partial
effect, keep the spec and the error and never rerun it automatically (see
`foundry:intake`, step 3). The merge stays with `foundry:merge-pr`
and the user; do not merge here.

## 5. Check
Run the `foundry:doctor` CLI:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" doctor
```
Report `<repo> → <KEY>` when its current-repo line is green, otherwise the red step and the
matching remedy from `foundry:doctor`.
