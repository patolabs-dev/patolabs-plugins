---
name: cleanup
description: >
  Proposes the removal of local branches whose PR is merged and of worktrees with no local
  change, then removes only what the maintainer confirms. USE WHEN the user invokes
  /foundry:cleanup (Claude Code), $foundry:cleanup (Codex), "cleanup", "ménage des branches",
  or "nettoyer les worktrees".
allowed-tools: Bash(python3:*), Bash(git:*)
---

# cleanup

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present, otherwise the Codex fallback. For Codex, derive
`<foundry-root>` from this skill's absolute path by removing `/skills/cleanup/SKILL.md`.
Never pass the placeholder literally.

Run from a checkout of the repository to clean.

## 1. Propose (read-only)
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query cleanup
```
The command (`tooling/foundry/cleanup.py`, `propose`) reads local Git state and, for each
local branch, the PRs of that head through the code host (`list_prs`). It fetches, deletes
and writes nothing, so it reflects the last `git fetch`: run `git fetch --prune` yourself first
if the maintainer wants fresh data. If the code host is unavailable it stops with a
message and proposes nothing: report it and stop.

Payload: `worktrees` (clean linked worktrees whose branch is merged, or detached on a commit
already in the default branch), `branches` (merged PR, local tip equal to the PR head or
already in the default branch, nothing unpushed;
`blocked_by_worktree` when a worktree that is not proposed holds it), `unsafe` (merged PR
but unpushed local commits, an unpushed count that `git rev-list` failed to read, or a
local tip that differs from the PR head and is not in the default branch: list them, never
propose them), and
`commands` (what the maintainer could run).

## 2. Confirm
Show the proposal grouped: worktrees first (a branch checked out in a worktree cannot be
deleted before it), then branches, then the `unsafe` items as information only. Ask the
maintainer to confirm item by item or as a whole. Nothing is removed without an explicit
yes; no answer means no removal. Warn that `git worktree remove` also deletes the ignored
files of a worktree (`.env`, `.venv`, local artefacts), which the clean check does not show
(observed on a scratch repository, without `--force`).

## 3. Remove only what was confirmed
Run the matching entries of `commands` (`git worktree remove <path>`, then
`git branch -D <branch>`; `-D` because a squash-merged branch is not an ancestor of the
default branch). Never touch an `unsafe` item, never use `--force`, and never delete a
remote branch. Report what was removed and what was kept.

## Scheduling
The proposal is meant to run as a recurring task on the maintainer's own machine
(`AGENTS.md#R6`): a local scheduled task of the desktop host, or a cron or launchd entry
running the read-only `query cleanup` and surfacing its output. A cloud routine is not
acceptable: it does not run on that machine. This repository ships the command and this
skill only; it creates no scheduled task. Removal always stays a manual confirmation in
step 2, never part of the scheduled run.
