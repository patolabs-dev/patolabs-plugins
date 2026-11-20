"""Read-only proposal of merged branches and idle worktrees to remove (PLP-365).

`query cleanup` lists, never deletes (`AGENTS.md#R1`, FOUNDRY-ADR-0003: lean payload,
the judgment lives in `skills/cleanup`). Squash merges leave no Git ancestry, so the
authority is the PR state read through the CodeHost port (`list_prs`), one call per
local candidate branch. Nothing is fetched: results reflect the last `git fetch`.
Local state is read with `git` read verbs only (`for-each-ref`, `worktree list`,
`status --porcelain` with `--no-optional-locks` so no index refresh is written,
`rev-parse`, `rev-list`, `merge-base`).
"""
from __future__ import annotations

import os
import shlex
import subprocess

CONTRACT = "foundry.cleanup.v1"
NOTE = "Reflects the last git fetch; nothing is fetched, deleted or written by this command."


class CleanupUnavailable(RuntimeError):
    """The repository or the code host cannot be read: fail closed, propose nothing."""


def _git(cwd: str, *args: str, check: bool = True) -> str:
    r = subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise CleanupUnavailable(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() if r.returncode == 0 else ""


def _is_ancestor(cwd: str, commit: str, ref: str) -> bool:
    return subprocess.run(("git", "merge-base", "--is-ancestor", commit, ref),
                          cwd=cwd, capture_output=True).returncode == 0


def _worktrees(cwd: str) -> list[dict]:
    items, cur = [], {}
    for line in _git(cwd, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur:
                items.append(cur)
            cur = {}
            continue
        key, _, value = line.partition(" ")
        cur[key] = value
    return items


def _default_branch(cwd: str) -> str:
    ref = _git(cwd, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if not ref.startswith("origin/"):
        raise CleanupUnavailable(
            "default branch unknown: refs/remotes/origin/HEAD is not set "
            "(run `git remote set-head origin --auto`).")
    return ref.removeprefix("origin/")


def propose(cwd: str | None = None, codehost=None) -> dict:
    """Return the cleanup proposal for the checkout at `cwd`. Writes nothing."""
    cwd = os.path.realpath(cwd or os.getcwd())
    root = os.path.realpath(_git(cwd, "rev-parse", "--show-toplevel"))
    default = _default_branch(root)
    current = _git(root, "branch", "--show-current", check=False)
    try:
        if codehost is None:
            import foundry
            codehost = foundry.codehost(cwd=root)
        repo = codehost.resolve_repo(root)
    except Exception as exc:  # noqa: BLE001 - any provider failure is fail-closed
        raise CleanupUnavailable(f"code host unavailable: {exc}") from exc

    trees = _worktrees(root)
    checked_out = {t["branch"].removeprefix("refs/heads/"): os.path.realpath(t["worktree"])
                   for t in trees if t.get("branch")}
    rows = _git(root, "for-each-ref", "--format=%(refname:short)\t%(objectname)\t%(upstream:short)"
                "\t%(upstream:track)", "refs/heads").splitlines()
    branches, unsafe = [], []
    for row in rows:
        name, tip, upstream, track = (row.split("\t") + ["", "", "", ""])[:4]
        if name in (default, current):
            continue
        try:
            prs = codehost.list_prs(repo, name)
        except Exception as exc:  # noqa: BLE001
            raise CleanupUnavailable(f"code host unavailable ({name}): {exc}") from exc
        if not prs or any(p.state == "open" and not p.merged for p in prs):
            continue
        merged = [p for p in prs if p.merged]
        if not merged:
            continue
        pr = max(merged, key=lambda p: p.number)
        item = {"branch": name, "pr": pr.number, "url": pr.url, "merge_sha": pr.merge_sha,
                "worktree": checked_out.get(name)}
        reasons = []
        if upstream and "gone" not in track:
            ahead = int(_git(root, "rev-list", "--count", f"{upstream}..{name}", check=False) or 0)
            item["unpushed"] = ahead
            if ahead:
                reasons.append(f"{ahead} local commit(s) not on {upstream}")
        else:
            item["unpushed"] = None
        if tip != pr.sha and not _is_ancestor(root, tip, f"origin/{default}"):
            reasons.append("local tip differs from the merged PR head")
        if reasons:
            unsafe.append({**item, "reasons": reasons})
        else:
            branches.append(item)

    safe_names = {b["branch"] for b in branches}
    worktrees = []
    for t in trees[1:]:
        path = os.path.realpath(t["worktree"])
        if path == root or "locked" in t or "prunable" in t or not os.path.isdir(path):
            continue
        branch = t["branch"].removeprefix("refs/heads/") if t.get("branch") else None
        if branch is not None:
            eligible = branch in safe_names
        else:
            eligible = bool(t.get("HEAD")) and _is_ancestor(root, t["HEAD"], f"origin/{default}")
        if eligible and not _git(path, "--no-optional-locks", "status", "--porcelain", check=False):
            worktrees.append({"path": path, "branch": branch, "clean": True})

    removed = {w["path"] for w in worktrees}
    for b in branches:
        b["blocked_by_worktree"] = bool(b["worktree"] and os.path.realpath(b["worktree"]) not in removed)
    commands = [f"git worktree remove {shlex.quote(w['path'])}" for w in worktrees]
    commands += [f"git branch -D {shlex.quote(b['branch'])}" for b in branches
                 if not b["blocked_by_worktree"]]
    return {"contract": CONTRACT, "repo": repo, "default_branch": default, "note": NOTE,
            "worktrees": worktrees, "branches": branches, "unsafe": unsafe,
            "commands": commands}
