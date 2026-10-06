"""scripts/ops/hosts.py + opscheck.py: the path probe and the ok/problem/unknown contract."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest

from scripts.ops import hosts

opscheck = hosts.opscheck  # the module hosts.py itself imported


def _statuses(result: dict) -> dict[str, str]:
    return {c["name"]: c["status"] for c in result["checks"]}


# --- dotenv parsing -------------------------------------------------------------

def test_parse_env_expands_earlier_keys_and_strips_quotes_and_comments():
    env = opscheck.parse_env(
        "# comment\n"
        "WORKING_DIR=/w\n"
        "DATA_DIR=${WORKING_DIR}/data\n"
        "export AIRPORTS_DB=\"$WORKING_DIR/data/nav.db\"\n"
        "LITERAL='${WORKING_DIR}'\n"
        "TRAIL=/x  # note\n"
        "#DISABLED=/nope\n",
        environ={},
    )
    assert env["DATA_DIR"] == "/w/data"
    assert env["AIRPORTS_DB"] == "/w/data/nav.db"
    assert env["LITERAL"] == "${WORKING_DIR}"
    assert env["TRAIL"] == "/x"
    assert "DISABLED" not in env


def test_unresolvable_reference_expands_to_empty_not_literal():
    assert opscheck.parse_env("A=${MISSING}/x", environ={})["A"] == "/x"


# --- exit-code contract ---------------------------------------------------------

@pytest.mark.parametrize("statuses,code", [
    (["ok", "skip"], 0),
    (["ok", "unknown"], 2),
    (["unknown", "problem"], 1),  # a definite problem outranks could-not-tell
    ([], 0),
])
def test_exit_code(statuses, code):
    checks = [opscheck.Check(f"c{i}", s) for i, s in enumerate(statuses)]
    assert opscheck.exit_code(checks) == code


# --- the probe on a fake checkout --------------------------------------------------

@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "venv" / "bin").mkdir(parents=True)
    (repo / "venv" / "bin" / "python").write_text("")
    data = tmp_path / "data"
    data.mkdir()
    (data / "nav.db").write_bytes(b"x" * 10)
    (data / "flyfun.db").write_bytes(b"x")
    (repo / ".env").write_text(
        f"DATA_DIR={data}\nAIRPORTS_DB=${{DATA_DIR}}/nav.db\nECMWF_GRIB_DIR={tmp_path}/nope\n")
    return repo


def test_probe_checks_kinds_and_exports_only_verified_paths(checkout):
    result = opscheck.probe({"repo": str(checkout), "venv": "venv", "dev_db": True,
                             "keys": hosts.CHECKOUT_KEYS})
    st = _statuses(result)
    assert st["DATA_DIR"] == st["AIRPORTS_DB"] == st["dev DB"] == "ok"
    assert st["ECMWF_GRIB_DIR"] == "problem"          # set but missing on disk
    assert st["CELLS_INBOX_DIR"] == "skip"            # optional and unset
    assert "ECMWF_GRIB_DIR" not in result["values"]   # never export an unverified path
    assert result["values"]["AIRPORTS_DB"].endswith("nav.db")


def test_probe_dir_where_file_expected_is_a_problem(checkout):
    env = checkout / ".env"
    env.write_text(env.read_text().replace("AIRPORTS_DB=${DATA_DIR}/nav.db",
                                           "AIRPORTS_DB=${DATA_DIR}"))
    st = _statuses(opscheck.probe({"repo": str(checkout), "keys": hosts.CHECKOUT_KEYS}))
    assert st["AIRPORTS_DB"] == "problem"


def test_probe_resolves_container_path_basename_under_host_dir(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    (host_data / "nav.db").write_text("x")
    (repo / ".env").write_text(f"HOST_DATA_DIR={host_data}\nAIRPORTS_DB=/app/data/nav.db\n")
    keys = [{"key": "HOST_DATA_DIR"}, {"key": "AIRPORTS_DB", "under": "HOST_DATA_DIR",
                                       "kind": "file", "export": "HOST_AIRPORTS_DB"}]
    result = opscheck.probe({"repo": str(repo), "keys": keys})
    assert result["values"]["HOST_AIRPORTS_DB"] == str(host_data / "nav.db")


def test_probe_wrong_hostname_is_a_problem(checkout):
    st = _statuses(opscheck.probe({"repo": str(checkout), "hostname": "not-this-machine"}))
    assert st["hostname"] == "problem"
    me = socket.gethostname().split(".")[0]
    assert _statuses(opscheck.probe({"repo": str(checkout), "hostname": me}))["hostname"] == "ok"


def test_probe_missing_repo_and_env(tmp_path, checkout):
    assert _statuses(opscheck.probe({"repo": str(tmp_path / "x")}))["repo"] == "problem"
    (checkout / ".env").unlink()
    assert _statuses(opscheck.probe({"repo": str(checkout)}))["env file"] == "problem"
    st = _statuses(opscheck.probe({"repo": str(checkout), "env_optional": "cloud"}))
    assert st["env file"] == "skip"


def test_probe_flags_database_url_as_could_not_tell(checkout):
    env = checkout / ".env"
    env.write_text(env.read_text() + "DATABASE_URL=sqlite:////elsewhere.db\n")
    st = _statuses(opscheck.probe({"repo": str(checkout), "dev_db": True,
                                   "keys": hosts.CHECKOUT_KEYS}))
    assert st["dev DB"] == "unknown"


# --- ssh plumbing (no network) -----------------------------------------------------

def _runner(returncode=0, stdout="", stderr=""):
    def run(cmd, **kw):
        assert cmd[0] == "ssh" and "python3 - probe" in cmd[-1]
        assert "def probe(" in kw["input"]  # the probe source is piped over
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return run


def test_remote_probe_parses_last_json_line():
    out = "motd noise\n" + json.dumps({"checks": [], "values": {"REPO": "/r"}})
    result, err = hosts.run_remote_probe("h", {"repo": "~"}, _runner(stdout=out))
    assert err == "" and result["values"]["REPO"] == "/r"


def test_remote_probe_ssh_failure_and_garbage_are_errors():
    _, err = hosts.run_remote_probe("h", {}, _runner(255, stderr="ssh: Connection refused"))
    assert "Connection refused" in err
    result, err = hosts.run_remote_probe("h", {}, _runner(1, stderr="python3: not found"))
    assert result is None and "probe failed" in err


def test_unreachable_lan_only_node_is_unknown_with_hint(monkeypatch):
    node = {"name": "n", "ssh": "u@h", "repo": "~/r", "lan_only": True}
    rep = hosts.check_node(node, runner=_runner(255, stderr="Operation timed out"))
    assert [c.status for c in rep.checks] == ["unknown"]
    assert "NOT known to be down" in rep.checks[0].evidence
    assert rep.exit_code == 2


def test_node_on_wrong_branch_is_a_problem():
    payload = {"checks": [], "values": {"BRANCH": "feature", "REPO": "/r"}}
    node = {"name": "n", "ssh": "u@h", "repo": "~/r", "branch": "main"}
    rep = hosts.check_node(node, runner=_runner(stdout=json.dumps(payload)))
    assert rep.exit_code == 1
    assert rep.values["NODE_REPO"] == "/r"


def test_server_values_keep_host_prefix():
    payload = {"checks": [], "values": {"HOST_DATA_DIR": "/d", "DATA_VOLUME": "/m",
                                        "REPO": "/r"}}
    cfg = {"server": {"ssh": "u@h", "project_dir": "p"}}
    rep = hosts.check_server(cfg, runner=_runner(stdout=json.dumps(payload)))
    assert rep.values["HOST_DATA_DIR"] == "/d" and rep.values["DATA_VOLUME"] == "/m"
    assert rep.values["SERVER_REPO"] == "/r" and rep.values["SERVER_SSH"] == "u@h"


# --- inventory loading -----------------------------------------------------------------

def test_load_hosts_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    with pytest.raises(hosts.ConfigError, match="hosts.example.json"):
        hosts.load_hosts(tmp_path / "missing.json")
    bad = tmp_path / "h.json"
    bad.write_text(json.dumps({"server": {"ssh": "x"}, "nodes": [{"name": "n"}]}))
    with pytest.raises(hosts.ConfigError, match="project_dir"):
        hosts.load_hosts(bad)


def test_cloud_session_without_inventory_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    with pytest.raises(hosts.ConfigError, match="cloud session"):
        hosts.load_hosts(tmp_path / "missing.json")
    assert hosts.main(["server", "--hosts-file", str(tmp_path / "missing.json")]) == 2
    assert "cloud session" in capsys.readouterr().err


def test_example_inventory_is_valid():
    cfg = hosts.load_hosts(hosts.REPO_ROOT / "deploy" / "hosts.example.json")
    assert cfg["server"]["project_dir"] and cfg["nodes"][0]["lan_only"] is True


def test_server_values_returns_verified_values_or_exits(tmp_path):
    inv = tmp_path / "h.json"
    inv.write_text(json.dumps({"server": {"ssh": "u@h", "project_dir": "p"}}))
    ok = {"checks": [], "values": {"HOST_DATA_DIR": "/d"}}
    v = hosts.server_values("SERVER_SSH", "HOST_DATA_DIR", hosts_file=inv,
                            runner=_runner(stdout=json.dumps(ok)))
    assert v["SERVER_SSH"] == "u@h" and v["HOST_DATA_DIR"] == "/d"
    bad = {"checks": [{"name": "HOST_DATA_DIR", "status": "problem", "value": "/d",
                       "evidence": "does not exist"}], "values": {}}
    with pytest.raises(SystemExit, match="HOST_DATA_DIR did not resolve"):
        hosts.server_values("HOST_DATA_DIR", hosts_file=inv,
                            runner=_runner(stdout=json.dumps(bad)))
    with pytest.raises(SystemExit, match="ssh"):
        hosts.server_values("HOST_DATA_DIR", hosts_file=inv,
                            runner=_runner(255, stderr="ssh: Connection refused"))
