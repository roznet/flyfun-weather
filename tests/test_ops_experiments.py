"""scripts/ops/experiments.py: the experiments registry and its read-only runner."""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from pathlib import Path

import pytest

from scripts.ops import experiments as ex

TODAY = dt.date(2026, 11, 14)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "experiments.toml"
    p.write_text(text)
    return p


# --- the real registry -----------------------------------------------------------

def test_real_registry_loads_and_uses_only_exported_host_names():
    entries = ex.load_registry()
    assert entries, "registry should not be empty"
    exported = set(re.findall(r"\b(?:SERVER|HOST|NODE|LOCAL|DATA|CONTAINER)_[A-Z_]+\b",
                              ex.hosts.__doc__))
    for e in entries:
        for cmd in e.commands:
            for name in ex.PLACEHOLDER.findall(cmd):
                assert name in exported, f"{e.id}: {{{name}}} is not a hosts.py export"
        prefix = {"server": ("SERVER_", "HOST_", "DATA_", "CONTAINER_"),
                  "node": ("NODE_",), "local": ("LOCAL_",)}[e.host]
        for cmd in e.commands:
            for name in ex.PLACEHOLDER.findall(cmd):
                assert name.startswith(prefix), f"{e.id}: {name} not resolved for host {e.host}"


# --- validation -------------------------------------------------------------------

@pytest.mark.parametrize("body, msg", [
    ('[a]\ntitle="t"\nhost="server"\n', "missing commands"),
    ('[a]\ntitle="t"\nhost="moon"\ncommands=["x"]\n', "host must be"),
    ('[a]\ntitle="t"\nhost="server"\ncommands=[]\n', "non-empty"),
    ('[a]\ntitle="t"\nhost="server"\ncommands=["x"]\ncolour="red"\n', "unknown field"),
    ('[a]\ntitle="t"\nhost="server"\ncommands=["x"]\ndecide_by="soon"\n', "YYYY-MM-DD"),
    ('[a]\ntitle="t"\nhost="server"\ncommands=["x"]\nkeep=["("]\n', "bad keep regex"),
])
def test_registry_rejects_bad_entries(tmp_path, body, msg):
    with pytest.raises(ex.RegistryError, match=msg):
        ex.load_registry(_write(tmp_path, body))


def test_decide_by_accepts_toml_date_and_string(tmp_path):
    p = _write(tmp_path, '[a]\ntitle="t"\nhost="server"\ncommands=["x"]\ndecide_by=2026-11-20\n'
                         '[b]\ntitle="t"\nhost="server"\ncommands=["x"]\ndecide_by="2026-11-20"\n')
    a, b = ex.load_registry(p)
    assert a.decide_by == b.decide_by == dt.date(2026, 11, 20)


@pytest.mark.parametrize("decide, label", [
    (None, "standing check"),
    (dt.date(2026, 11, 14), "decide today"),
    (dt.date(2026, 11, 20), "decide in 6 d (2026-11-20)"),
    (dt.date(2026, 11, 10), "OVERDUE by 4 d: decide or remove"),
])
def test_due_label(decide, label):
    e = ex.Entry(id="a", title="t", host="server", commands=["x"], decide_by=decide)
    assert e.due_label(TODAY) == label


# --- rendering and filtering ------------------------------------------------------

def test_render_fills_placeholders_and_refuses_missing_ones():
    assert ex.render("cd {NODE_REPO} && ls", {"NODE_REPO": "/r"}) == "cd /r && ls"
    with pytest.raises(KeyError, match="NODE_VENV"):
        ex.render("{NODE_VENV}/bin/python", {"NODE_REPO": "/r"})


def test_render_leaves_lowercase_braces_alone():
    # python -c snippets and dict literals must survive untouched
    assert ex.render("echo {x} {SERVER_CONTAINER}", {"SERVER_CONTAINER": "wb"}) == "echo {x} wb"


def test_filter_output_keeps_first_line_and_matches_then_caps():
    text = "header\nnoise\nPASS a\n\nnoise\nFAIL b\nPASS c\n"
    assert ex.filter_output(text, ["PASS", "FAIL"], 25) == ["header", "PASS a", "FAIL b", "PASS c"]
    capped = ex.filter_output(text, [], 2)
    assert capped[:2] == ["header", "noise"] and "cut" in capped[2]


# --- running ----------------------------------------------------------------------

class FakeRun:
    def __init__(self, stdout="line\n", returncode=0, stderr=""):
        self.calls = []
        self.out = subprocess.CompletedProcess([], returncode, stdout, stderr)

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return self.out


def _server(monkeypatch, values=None):
    vals = {"SERVER_SSH": "me@prod", "SERVER_CONTAINER": "wb", **(values or {})}
    monkeypatch.setattr(ex.hosts, "server_values", lambda *a, **k: vals)


def test_run_entry_runs_over_ssh_and_filters(monkeypatch):
    _server(monkeypatch)
    e = ex.Entry(id="v", title="Vis", host="server", keep=["PASS"],
                 commands=["docker exec {SERVER_CONTAINER} report"])
    run = FakeRun("12 scored hours\nnoise\n  sw99 PASS\n")
    res = ex.run_entry(e, TODAY, {}, runner=run)
    assert res.status == "ok"
    assert run.calls == [["ssh", "-o", "ConnectTimeout=15", "me@prod", "docker exec wb report"]]
    assert "  12 scored hours" in res.lines and "    sw99 PASS" in res.lines
    assert not any("noise" in ln for ln in res.lines)


def test_run_entry_reports_failure_with_stderr_tail(monkeypatch):
    _server(monkeypatch)
    e = ex.Entry(id="v", title="Vis", host="server", commands=["boom"])
    res = ex.run_entry(e, TODAY, {}, runner=FakeRun("", 3, "a\nb\nTraceback\nValueError: x\n"))
    assert res.status == "failed"
    assert "  exit 3" in res.lines and "  ! ValueError: x" in res.lines


def test_unreachable_host_is_not_ok(monkeypatch):
    def boom(*a, **k):
        raise SystemExit("hosts: cloud session -- no host inventory\nmore")
    monkeypatch.setattr(ex.hosts, "server_values", boom)
    e = ex.Entry(id="v", title="Vis", host="server", commands=["x"])
    res = ex.run_entry(e, TODAY, {})
    assert res.status == "unreachable" and res.lines == ["hosts: cloud session -- no host inventory"]


def test_dry_run_runs_nothing_and_resolves_once_per_host(monkeypatch):
    calls = []

    def values(*a, **k):
        calls.append(1)
        return {"SERVER_SSH": "me@prod", "SERVER_CONTAINER": "wb"}
    monkeypatch.setattr(ex.hosts, "server_values", values)
    run = FakeRun()
    cache: dict = {}
    for i in range(2):
        e = ex.Entry(id=f"e{i}", title="t", host="server", commands=["docker exec {SERVER_CONTAINER} x"])
        res = ex.run_entry(e, TODAY, cache, dry_run=True, runner=run)
        assert res.status == "dry-run" and "me@prod" in res.lines[0]
    assert run.calls == [] and len(calls) == 1


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    reg = _write(tmp_path, '[a]\ntitle="t"\nhost="server"\ncommands=["x"]\n')
    assert ex.main(["--registry", str(reg), "--list"]) == 0
    assert ex.main(["--registry", str(reg), "nope"]) == 1
    monkeypatch.setattr(ex.hosts, "server_values",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit("hosts: down")))
    assert ex.main(["--registry", str(reg)]) == 2
    assert "unreachable" in capsys.readouterr().out


def test_ssh_255_is_unreachable_not_failed(monkeypatch):
    _server(monkeypatch)
    e = ex.Entry(id="v", title="Vis", host="server", commands=["a", "b"])
    run = FakeRun("", 255, "ssh: connect to host prod port 22: Operation timed out\n")
    res = ex.run_entry(e, TODAY, {}, runner=run)
    assert res.status == "unreachable" and len(run.calls) == 1
    assert "  ! ssh: connect to host prod port 22: Operation timed out" in res.lines


def test_id_key_in_registry_is_a_registry_error(tmp_path):
    with pytest.raises(ex.RegistryError, match="unknown field"):
        ex.load_registry(_write(tmp_path, '[a]\nid="x"\ntitle="t"\nhost="server"\ncommands=["x"]\n'))


def test_decision_line_hidden_only_for_standing_checks():
    base = dict(title="t", due="whatever label", status="ok", decision="Q?")
    assert "decision: Q?" not in ex.format_result(ex.Result(id="a", standing=True, **base))
    assert "decision: Q?" in ex.format_result(ex.Result(id="a", standing=False, **base))
