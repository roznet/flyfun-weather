"""prod-briefings-review fetch.py (mini transport) and scripts/ops/fetch_pack.py."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
from pathlib import Path

import pytest

from scripts.ops import fetch_pack

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "pbr_fetch", REPO / ".claude/skills/prod-briefings-review/scripts/fetch.py")
fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch)


# --- fetch_pack -----------------------------------------------------------------------

def test_parse_target_from_urls_and_ids():
    u = "https://weather.flyfun.aero/briefing.html?flight=egtf_lfat-2026-10-09-ab12&pack=2026-10-09T10-00-00.1p00-00"
    assert fetch_pack.parse_target(u, None) == ("egtf_lfat-2026-10-09-ab12",
                                               "2026-10-09T10-00-00.1p00-00")
    admin = "https://x/briefing.html?flight=f1&t=2026-10-09T10:00:00%2B00:00"
    assert fetch_pack.parse_target(admin, None) == ("f1", "2026-10-09T10:00:00+00:00")
    assert fetch_pack.parse_target("f1", "p") == ("f1", "p")
    for bad in ("https://x/briefing.html?pack=1", "../etc", "a/b"):
        with pytest.raises(SystemExit):
            fetch_pack.parse_target(bad, None)


def test_choose_pack_by_dir_name_iso_or_newest():
    names = ["2026-10-08T09-00-00.000001p00-00", "2026-10-09T10-00-00.000002p00-00", ".tmp"]
    assert fetch_pack.choose(names, None) == names[1]
    assert fetch_pack.choose(names, "2026-10-08T09:00:00.000001+00:00") == names[0]
    assert fetch_pack.choose(names, "2026-10-08T09-00-00") == names[0]  # prefix
    assert fetch_pack.choose(names, "2025-01-01") is None
    assert fetch_pack.choose([], None) is None


# --- fetch.py mini transport ------------------------------------------------------------

class FakeNode:
    """Records every command; fails the on_mini step when asked."""

    def __init__(self, fail_job=False):
        self.calls, self.fail_job = [], fail_job

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        joined = " ".join(map(str, cmd))
        if "mktemp" in joined:
            return subprocess.CompletedProcess(cmd, 0, b"/tmp/pbr-x.abcd\n", b"")
        if "on_mini.py" in joined and cmd[0] == "ssh" and self.fail_job:
            return subprocess.CompletedProcess(cmd, 1, b"", b"Traceback: boom")
        if cmd[0] == "scp" and str(cmd[-1]).endswith("radar.json") and ":" in str(cmd[-2]):
            Path(cmd[-1]).write_text("[]")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


@pytest.fixture
def node(monkeypatch):
    monkeypatch.setattr(fetch.hosts, "node_values", lambda *a, **k: {
        "NODE_SSH": "u@mini", "NODE_VENV": "/Users/u/repo/venv", "NODE_NAME": "mini"})


def _args(tmp_path, job="airport-radar"):
    inp = tmp_path / "points.json"
    inp.write_text("[]")
    return argparse.Namespace(job=job, input=str(inp), output=str(tmp_path / "radar.json"),
                              node=None)


def test_mini_runs_with_the_node_venv_and_cleans_up(node, tmp_path):
    fake = FakeNode()
    fetch.cmd_mini(_args(tmp_path), runner=fake)
    joined = [" ".join(map(str, c)) for c in fake.calls]
    assert any("/Users/u/repo/venv/bin/python on_mini.py airport-radar points.json radar.json"
               in j for j in joined)
    assert "rm -rf /tmp/pbr-x.abcd" in joined[-1]
    assert (tmp_path / "radar.json").read_text() == "[]"


def test_mini_cleans_up_even_when_the_job_fails(node, tmp_path):
    fake = FakeNode(fail_job=True)
    with pytest.raises(fetch.Failed, match="on_mini.py"):
        fetch.cmd_mini(_args(tmp_path), runner=fake)
    assert "rm -rf /tmp/pbr-x.abcd" in " ".join(map(str, fake.calls[-1]))


def test_mini_unreachable_node_is_could_not_tell(monkeypatch, tmp_path, capsys):
    def unreachable(*a, **k):
        raise SystemExit("hosts: NODE_SSH did not resolve ... lan_only node")
    monkeypatch.setattr(fetch.hosts, "node_values", unreachable)
    with pytest.raises(SystemExit, match="lan_only"):
        fetch.cmd_mini(_args(tmp_path), runner=FakeNode())


def test_run_maps_ssh_255_to_could_not_tell():
    def r(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 255, b"", b"ssh: connect refused")
    with pytest.raises(fetch.Failed) as exc:
        fetch.run(["ssh", "h", "x"], what="t", runner=r)
    assert exc.value.code == 2
