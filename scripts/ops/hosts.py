#!/usr/bin/env python3
"""Where everything is: the droplet, the compute nodes and this checkout.

Reads the host inventory from deploy/hosts.json -- this checkout's, or in a
worktree the main checkout's (gitignored; the tracked
deploy/hosts.example.json documents every field), then resolves and checks the
paths on each host. Nothing is cached: one ssh per host, every run.

    hosts.py server            droplet: repo, HOST_* paths, data volume, nav.db, container
    hosts.py node NAME         one compute node: hostname, repo/branch, venv, data paths, DB
    hosts.py nodes             every compute node
    hosts.py local             this checkout: DATA_DIR, AIRPORTS_DB, ECMWF dir, dev DB
    hosts.py all               all of the above

Output modes (default: a readable report, one line per check with evidence):

    --env       KEY='value' lines for `eval "$(hosts.py server --env)"`
    --get KEY   print one value, e.g. `hosts.py server --get HOST_DATA_DIR`
    --json      machine-readable report

Exported names: SERVER_SSH, SERVER_PROJECT_DIR, SERVER_URL, SERVER_REPO,
SERVER_HEAD, SERVER_CONTAINER, HOST_DATA_DIR, HOST_ECMWF_GRIB_DIR,
HOST_SNAPSHOT_INBOX, HOST_CELLS_INBOX, HOST_AIRPORTS_DB (the host-side nav.db
file), DATA_VOLUME; NODE_SSH, NODE_REPO, NODE_HEAD, NODE_BRANCH, NODE_VENV,
NODE_DATA_DIR, NODE_AIRPORTS_DB, NODE_ECMWF_GRIB_DIR, NODE_DEV_DB;
LOCAL_REPO, LOCAL_DATA_DIR, LOCAL_AIRPORTS_DB, LOCAL_ECMWF_GRIB_DIR,
LOCAL_EVAL_CORPUS_DIR, LOCAL_CELLS_INBOX_DIR, LOCAL_DEV_DB.
Only verified values are exported: a path that is missing is not exported, so
`--get` fails loudly instead of handing back a wrong path.

Cloud sessions (Claude Code on the web, CLAUDE_CODE_REMOTE=true) have no
hosts.json and no .env by design: there is no host access from there. `server`
/ `node` then report "could not tell (cloud session)" rather than a broken
setup, and `local` reports the missing .env as skip. Skills that run in the
cloud (implement-issue, process-review, code-review) must not depend on this
script.

Exit codes (see opscheck.py): 0 all ok, 1 a problem, 2 could not tell
(e.g. ssh failed). An unreachable lan_only node is "could not tell", never ok.
Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opscheck  # noqa: E402
from opscheck import OK, PROBLEM, UNKNOWN, Check, Report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_hosts_file() -> Path:
    """deploy/hosts.json here, else the main checkout's.

    It is gitignored, so a sibling worktree has none of its own; reading main's
    keeps a single inventory instead of copies that drift apart.
    """
    here = REPO_ROOT / "deploy" / "hosts.json"
    if here.is_file():
        return here
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                                 "--git-common-dir"], cwd=REPO_ROOT, capture_output=True,
                                text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return here
    main = Path(common).parent / "deploy" / "hosts.json" if common else here
    return main if main.is_file() else here


DEFAULT_HOSTS_FILE = _default_hosts_file()
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]

SERVER_KEYS = [
    {"key": "HOST_DATA_DIR"},
    {"key": "HOST_ECMWF_GRIB_DIR"},
    {"key": "HOST_SNAPSHOT_INBOX"},
    {"key": "HOST_CELLS_INBOX", "required": False},
    # AIRPORTS_DB in the server .env is a CONTAINER path; the host file is its
    # basename under HOST_DATA_DIR (compose maps HOST_DATA_DIR -> /app/data).
    {"key": "AIRPORTS_DB", "under": "HOST_DATA_DIR", "kind": "file",
     "label": "nav.db (host side)", "export": "HOST_AIRPORTS_DB"},
]
CHECKOUT_KEYS = [  # a compute node and a dev checkout read the same .env names
    {"key": "DATA_DIR"},
    {"key": "AIRPORTS_DB", "kind": "file"},
    {"key": "ECMWF_GRIB_DIR"},
    {"key": "CELLS_INBOX_DIR", "required": False},
]
LOCAL_EXTRA_KEYS = [{"key": "EVAL_CORPUS_DIR", "required": False}]


class ConfigError(Exception):
    pass


def is_cloud() -> bool:
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true"


def load_hosts(path: Path) -> dict:
    if not path.is_file() and is_cloud():
        raise ConfigError("cloud session -- no host inventory here by design, so host "
                          "checks are unavailable; skip host steps rather than guess paths")
    if not path.is_file():
        raise ConfigError(f"{path} not found -- copy deploy/hosts.example.json to "
                          f"deploy/hosts.json and fill in the real hosts")
    try:
        cfg = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc})") from exc
    server = cfg.get("server") or {}
    missing = [k for k in ("ssh", "project_dir") if not server.get(k)]
    if missing:
        raise ConfigError(f"{path}: server is missing {', '.join(missing)}")
    for n in cfg.get("nodes", []):
        miss = [k for k in ("name", "ssh", "repo") if not n.get(k)]
        if miss:
            raise ConfigError(f"{path}: node {n.get('name', '?')} is missing {', '.join(miss)}")
    return cfg


# --- running the probe ---------------------------------------------------------

def run_remote_probe(ssh: str, spec: dict, runner=subprocess.run) -> tuple[dict | None, str]:
    """Pipe opscheck.py to `python3 -` on the host. Returns (result, error)."""
    src = Path(opscheck.__file__).read_text()
    remote = "python3 - probe " + shlex.quote(json.dumps(spec))
    try:
        r = runner(["ssh", *SSH_OPTS, ssh, remote], input=src,
                   capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None, "ssh timed out after 60s"
    if r.returncode == 255:
        return None, (r.stderr.strip().splitlines() or ["ssh failed (255)"])[-1]
    out = r.stdout.strip().splitlines()
    try:
        return json.loads(out[-1]), ""
    except (IndexError, json.JSONDecodeError):
        tail = (r.stderr.strip() or r.stdout.strip())[-300:]
        return None, f"reached the host but the probe failed (exit {r.returncode}): {tail}"


def _merge(report: Report, result: dict, prefix: str, passthrough: tuple[str, ...] = ()):
    for c in result["checks"]:
        report.checks.append(Check(**c))
    for k, v in result["values"].items():
        name = k if k.startswith(passthrough) else f"{prefix}{k}"
        report.values[name] = v


# --- the three targets ---------------------------------------------------------

def check_server(cfg: dict, runner=subprocess.run) -> Report:
    s = cfg["server"]
    rep = Report(f"server {s.get('name', 'droplet')} ({s['ssh']})")
    rep.values.update({"SERVER_SSH": s["ssh"], "SERVER_PROJECT_DIR": s["project_dir"]})
    if s.get("url"):
        rep.values["SERVER_URL"] = s["url"]
    project = s["project_dir"]
    spec = {"repo": project if project.startswith(("/", "~")) else f"~/{project}",
            "keys": SERVER_KEYS, "mount_of": "HOST_DATA_DIR",
            "container": s.get("container"), "hostname": s.get("hostname")}
    result, err = run_remote_probe(s["ssh"], spec, runner)
    if result is None:
        rep.add("ssh", UNKNOWN, s["ssh"], err)
        return rep
    _merge(rep, result, "SERVER_", passthrough=("HOST_", "DATA_VOLUME"))
    return rep


def check_node(node: dict, runner=subprocess.run) -> Report:
    rep = Report(f"node {node['name']} ({node['ssh']})")
    rep.values["NODE_SSH"] = node["ssh"]
    spec = {"repo": node["repo"], "venv": node.get("venv", "venv"),
            "keys": CHECKOUT_KEYS, "dev_db": True, "hostname": node.get("hostname")}
    result, err = run_remote_probe(node["ssh"], spec, runner)
    if result is None:
        hint = (" -- lan_only node: expected when this machine is not on its home "
                "network; it is NOT known to be down" if node.get("lan_only") else "")
        rep.add("ssh", UNKNOWN, node["ssh"], err + hint)
        return rep
    _merge(rep, result, "NODE_")
    want, got = node.get("branch"), result["values"].get("BRANCH")
    if want and got is not None:
        rep.add("branch", OK if got == want else PROBLEM, got or "(detached)",
                "" if got == want else f"hosts.json says {want}")
    return rep


def check_local(repo: Path = REPO_ROOT) -> Report:
    rep = Report(f"local checkout ({repo})")
    spec = {"repo": str(repo), "venv": "venv",
            "keys": CHECKOUT_KEYS + LOCAL_EXTRA_KEYS, "dev_db": True}
    if is_cloud():
        spec["venv"] = None  # the cloud sandbox provides its own interpreter
        spec["env_optional"] = "cloud session: no .env by design"
    _merge(rep, opscheck.probe(spec), "LOCAL_")
    return rep


def server_values(*required: str, hosts_file: Path = DEFAULT_HOSTS_FILE,
                  runner=subprocess.run) -> dict[str, str]:
    """Resolved, verified server values for other scripts -- or exit loudly.

    One ssh, same checks as `hosts.py server`. Raises SystemExit naming the
    first required key that did not resolve, so a script never runs an rsync or
    ssh against a guessed path:

        prod = server_values("SERVER_SSH", "HOST_DATA_DIR")
    """
    try:
        rep = check_server(load_hosts(hosts_file), runner)
    except ConfigError as exc:
        raise SystemExit(f"hosts: {exc}") from None
    for key in required:
        if key not in rep.values:
            bad = [c.line() for c in rep.checks if c.status in (PROBLEM, UNKNOWN)]
            raise SystemExit(f"hosts: {key} did not resolve for {rep.title}:\n"
                             + "\n".join(bad or ["  (no failing check -- unknown key?)"])
                             + "\nrun `python3 scripts/ops/hosts.py server` for the full report")
    return rep.values


def build_reports(target: str, name: str | None, hosts_file: Path) -> list[Report]:
    if target == "local":
        return [check_local()]
    if target == "all" and is_cloud() and not hosts_file.is_file():
        return [check_local()]
    cfg = load_hosts(hosts_file)
    nodes = cfg.get("nodes", [])
    if target == "server":
        return [check_server(cfg)]
    if target == "node":
        match = [n for n in nodes if n["name"] == name]
        if not match:
            names = ", ".join(n["name"] for n in nodes) or "none configured"
            raise ConfigError(f"no node named {name!r} in {hosts_file} ({names})")
        return [check_node(match[0])]
    if target == "nodes":
        return [check_node(n) for n in nodes]
    return [check_server(cfg), *[check_node(n) for n in nodes], check_local()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", choices=["server", "node", "nodes", "local", "all"])
    ap.add_argument("name", nargs="?", help="node name (for `node`)")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--env", action="store_true")
    out.add_argument("--json", action="store_true")
    out.add_argument("--get", metavar="KEY")
    ap.add_argument("--hosts-file", type=Path, default=DEFAULT_HOSTS_FILE)
    args = ap.parse_args(argv)
    if args.target == "node" and not args.name:
        ap.error("`node` needs a node name")

    try:
        reports = build_reports(args.target, args.name, args.hosts_file)
    except ConfigError as exc:
        print(f"unknown  config: {exc}", file=sys.stderr)
        return 2
    code = opscheck.exit_code([c for r in reports for c in r.checks])
    values = {k: v for r in reports for k, v in r.values.items()}

    if args.json:
        print(json.dumps({"exit_code": code, "reports": [r.as_json() for r in reports]},
                         indent=2))
    elif args.env:
        for k, v in values.items():
            print(f"{k}={shlex.quote(v)}")
        for r in reports:
            for c in r.checks:
                if c.status in (PROBLEM, UNKNOWN):
                    print(f"# {c.status}: {c.name} {c.value} {c.evidence}".rstrip(),
                          file=sys.stderr)
    elif args.get:
        if args.get in values:
            print(values[args.get])
            return 0
        print(f"{args.get} not resolved -- run `hosts.py {args.target}"
              f"{' ' + args.name if args.name else ''}` to see why", file=sys.stderr)
        return code or 2
    else:
        print("\n\n".join(r.render() for r in reports))
    return code


if __name__ == "__main__":
    sys.exit(main())
