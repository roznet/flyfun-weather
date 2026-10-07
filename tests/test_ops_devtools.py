"""scripts/ops/worktree_init.py + devserver.py: the pure decisions (no tmux, no venv)."""

from __future__ import annotations

import subprocess

from scripts.ops import devserver, worktree_init


def test_alembic_revisions_parse_current_and_heads():
    assert devserver.revisions("097 (head)\n") == {"097"}
    assert devserver.revisions("INFO  [alembic] Context impl SQLiteImpl.\n096\n") == {"096"}
    assert devserver.revisions("1a2b3c4d5e6f (head)\n9f8e7d6c5b4a (head)") == {
        "1a2b3c4d5e6f", "9f8e7d6c5b4a"}
    assert devserver.revisions("") == set()


def test_pick_port_main_sticky_and_first_free():
    assert devserver.pick_port("main", None, in_use=lambda p: True) == 8000
    assert devserver.pick_port("issue-1", 8004, in_use=lambda p: True) == 8004
    assert devserver.pick_port("issue-1", None, in_use=lambda p: p in (8001, 8002)) == 8003
    assert devserver.pick_port("issue-1", None, in_use=lambda p: True) is None


def test_relative_env_values_are_flagged():
    text = ("DATA_DIR=/abs/data\nCACHE=./cache\nUP=../x\nREL=data/sub\n"
            "URL=https://x\nFLAG=true\n# C=./commented\n")
    assert worktree_init.relative_env_lines(text) == ["CACHE", "UP", "REL"]


def test_existing_worktree_reads_porcelain(monkeypatch):
    porcelain = ("worktree /w/main\nHEAD abc\nbranch refs/heads/main\n\n"
                 "worktree /w/issue-7\nHEAD def\nbranch refs/heads/issue-7\n\n"
                 "worktree /w/detached\nHEAD 123\ndetached\n")
    monkeypatch.setattr(worktree_init, "git", lambda *a, cwd: subprocess.CompletedProcess(
        a, 0, porcelain, ""))
    assert worktree_init.existing_worktree("/w/main", "issue-7") == "/w/issue-7"
    assert worktree_init.existing_worktree("/w/main", "issue-70") is None


def test_branch_names_that_could_become_flags_are_refused(tmp_path):
    rep = worktree_init.Report("t")
    for bad in ("-rf", "a b", "x;y"):
        try:
            worktree_init.step_worktree(rep, tmp_path, bad, None, tmp_path / "w", True)
        except worktree_init.Stop:
            pass
        else:
            raise AssertionError(f"{bad!r} accepted")
    assert all(c.status == "problem" for c in rep.checks)
