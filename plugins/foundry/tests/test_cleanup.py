"""PLP-365: `query cleanup` proposes merged branches / idle worktrees and deletes nothing."""
import os
import pathlib
import subprocess

import pytest

from foundry import cleanup
from foundry.models import PullRequest

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")


def _g(cwd, *args):
    return subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class FakeHost:
    def __init__(self, prs, fail=False):
        self.prs, self.fail, self.calls = prs, fail, []

    def resolve_repo(self, cwd=None):
        return "o/r"

    def list_prs(self, repo, head):
        self.calls.append(head)
        if self.fail:
            raise RuntimeError("gh unavailable")
        return self.prs.get(head, [])


def _pr(branch, sha, merged=True, number=1):
    return PullRequest(number=number, url=f"u/{number}", head=branch, base="main", sha=sha,
                       merged=merged, merge_sha="m" * 7, state="closed" if merged else "open")


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _g(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _g(tmp_path, "clone", "-q", str(origin), str(work))
    _g(work, "config", "user.email", "t@example.invalid")
    _g(work, "config", "user.name", "t")
    _g(work, "commit", "-q", "--allow-empty", "-m", "init")
    _g(work, "push", "-q", "origin", "main")
    _g(work, "remote", "set-head", "origin", "main")
    tips = {}
    for name in ("merged", "open", "nopr", "unsafe", "wtclean", "wtdirty", "tipdiffers", "gone"):
        _g(work, "checkout", "-q", "-b", name, "main")
        _g(work, "commit", "-q", "--allow-empty", "-m", name)
        tips[name] = _g(work, "rev-parse", "HEAD")
        _g(work, "push", "-q", "-u", "origin", name)
    _g(work, "checkout", "-q", "unsafe")
    _g(work, "commit", "-q", "--allow-empty", "-m", "local only")
    # "tipdiffers": the PR head is another commit (sibling, not in main); nothing unpushed
    _g(work, "checkout", "-q", "main")
    # "gone": upstream deleted on the remote after the merge
    _g(work, "push", "-q", "origin", "--delete", "gone")
    _g(work, "fetch", "-q", "--prune", "origin")
    # detached worktree on a commit already in main
    _g(work, "worktree", "add", "-q", "--detach", str(tmp_path / "wt-detached"), "main")
    clean, dirty = tmp_path / "wt-clean", tmp_path / "wt-dirty"
    _g(work, "worktree", "add", "-q", str(clean), "wtclean")
    _g(work, "worktree", "add", "-q", str(dirty), "wtdirty")
    (dirty / "x.txt").write_text("wip")
    prs = {"merged": [_pr("merged", tips["merged"], number=2)],
           "open": [_pr("open", tips["open"], merged=False, number=3)],
           "unsafe": [_pr("unsafe", tips["unsafe"], number=4)],
           "wtclean": [_pr("wtclean", tips["wtclean"], number=5)],
           "wtdirty": [_pr("wtdirty", tips["wtdirty"], number=6)],
           "tipdiffers": [_pr("tipdiffers", tips["merged"], number=7)],
           "gone": [_pr("gone", tips["gone"], number=8)]}
    return work, clean, dirty, prs


def _snapshot(work):
    return (_g(work, "for-each-ref"), _g(work, "worktree", "list"), _g(work, "status", "--porcelain"))


def test_proposal_lists_merged_branches_and_clean_worktrees_and_writes_nothing(repo):
    work, clean, dirty, prs = repo
    before = _snapshot(work)
    host = FakeHost(prs)
    out = cleanup.propose(str(work), host)
    assert _snapshot(work) == before
    assert sorted(b["branch"] for b in out["branches"]) == [
        "gone", "merged", "wtclean", "wtdirty"]
    assert sorted(w["path"] for w in out["worktrees"]) == sorted(
        os.path.realpath(p) for p in (clean, clean.parent / "wt-detached"))
    assert [w["branch"] for w in out["worktrees"] if w["branch"] is None] == [None]
    assert sorted(u["branch"] for u in out["unsafe"]) == ["tipdiffers", "unsafe"]
    assert {u["branch"]: u["unpushed"] for u in out["unsafe"]}["unsafe"] == 1
    assert "main" not in host.calls and "open" in host.calls
    by = {b["branch"]: b for b in out["branches"]}
    assert by["wtdirty"]["blocked_by_worktree"] and not by["wtclean"]["blocked_by_worktree"]
    by_unsafe = {u["branch"]: u for u in out["unsafe"]}
    assert by_unsafe["tipdiffers"]["unpushed"] == 0
    assert by_unsafe["tipdiffers"]["reasons"] == ["local tip differs from the merged PR head"]
    assert by["gone"]["unpushed"] is None
    assert out["commands"][0].startswith("git worktree remove ")
    assert "git branch -D merged" in out["commands"]
    assert "git branch -D wtdirty" not in out["commands"]
    assert "git branch -D unsafe" not in out["commands"]


def test_propose_leaves_linked_worktree_indexes_untouched(repo):
    work, clean, dirty, prs = repo
    (clean / "tracked.txt").write_text("t")
    _g(clean, "add", "tracked.txt")
    _g(clean, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "t")
    _g(clean, "push", "-q")
    prs["wtclean"] = [_pr("wtclean", _g(clean, "rev-parse", "HEAD"), number=5)]
    # a stale stat entry makes a plain `git status` rewrite the index
    os.utime(clean / "tracked.txt", (1_000_000_000, 1_000_000_000))
    idx = pathlib.Path(_g(clean, "rev-parse", "--absolute-git-dir")) / "index"
    didx = pathlib.Path(_g(dirty, "rev-parse", "--absolute-git-dir")) / "index"
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (idx, didx)}
    out = cleanup.propose(str(work), FakeHost(prs))
    # the clean worktree is proposed, so its `git status` did run
    assert os.path.realpath(clean) in [w["path"] for w in out["worktrees"]]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (idx, didx)} == before


def test_worktree_with_failing_status_is_not_proposed(repo, monkeypatch):
    work, clean, _, prs = repo
    real = cleanup._git_ok

    seen = []

    def fake(cwd, *args):
        if "status" in args:
            seen.append(cwd)
            return None
        return real(cwd, *args)

    monkeypatch.setattr(cleanup, "_git_ok", fake)
    assert cleanup.propose(str(work), FakeHost(prs))["worktrees"] == []
    assert os.path.realpath(clean) in seen


def test_failing_rev_list_makes_the_branch_unsafe(repo, monkeypatch):
    work, _, _, prs = repo
    real = cleanup._git_ok

    def fake(cwd, *args):
        return None if "rev-list" in args else real(cwd, *args)

    monkeypatch.setattr(cleanup, "_git_ok", fake)
    out = cleanup.propose(str(work), FakeHost(prs))
    merged = [u for u in out["unsafe"] if u["branch"] == "merged"]
    assert merged and "unknown" in merged[0]["reasons"][0]
    assert "merged" not in [b["branch"] for b in out["branches"]]


def test_missing_origin_head_refuses(repo):
    work, _, _, prs = repo
    _g(work, "remote", "set-head", "origin", "-d")
    with pytest.raises(cleanup.CleanupUnavailable, match="refs/remotes/origin/HEAD"):
        cleanup.propose(str(work), FakeHost(prs))


def test_query_cleanup_refuses_an_extra_argument(monkeypatch):
    import runpy
    import sys
    monkeypatch.setattr(sys, "argv", ["query.py", "cleanup", "extra"])
    monkeypatch.delitem(sys.modules, "foundry.query", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("foundry.query", run_name="__main__")
    assert "usage: query cleanup" in str(exc.value)


def test_code_host_failure_fails_closed(repo):
    work, _, _, prs = repo
    with pytest.raises(cleanup.CleanupUnavailable, match="code host unavailable"):
        cleanup.propose(str(work), FakeHost(prs, fail=True))


def test_cli_verb_is_registered_and_skill_is_documented():
    from foundry import query
    assert "cleanup" in query._CMDS
    text = open(os.path.join(_SKILLS_DIR, "cleanup", "SKILL.md")).read()
    assert "/foundry:cleanup" in text and "$foundry:cleanup" in text
    assert "query cleanup" in text and "AGENTS.md#R6" in text
    readme = open(os.path.join(_SKILLS_DIR, "..", "README.md")).read()
    assert "`foundry:cleanup`" in readme
