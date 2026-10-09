#!/usr/bin/env python3
"""Shared plumbing for the ops scripts: the result contract and the path probe.

Every ops script reports a list of checks, each one of:

    ok       verified, with the evidence printed next to it
    problem  verified wrong (a path that is missing, the wrong host answered)
    unknown  could not tell (ssh failed, unexpected output) -- never read as ok
    skip     not applicable here (an optional setting left unset); informational

and exits 0 = all ok, 1 = at least one problem, 2 = no problem but at least one
unknown. "Nothing found" is never reported as ok: a check that cannot see its
evidence says unknown.

This file is also the remote probe. It is stdlib-only and self-contained so it
can be piped over ssh to a host that does not have this checkout (or has an
older one, like a compute node that is behind):

    ssh HOST python3 - probe '<spec json>' < scripts/ops/opscheck.py

The probe resolves the requested keys from a dotenv file, checks each path on
the host it runs on, and prints one JSON object.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

OK, PROBLEM, UNKNOWN, SKIP = "ok", "problem", "unknown", "skip"


@dataclass
class Check:
    name: str
    status: str
    value: str = ""
    evidence: str = ""

    def line(self, width: int = 22) -> str:
        tail = f"  ({self.evidence})" if self.evidence else ""
        return f"  {self.status:<8} {self.name:<{width}} {self.value}{tail}".rstrip()


@dataclass
class Report:
    title: str
    checks: list[Check] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)  # exported KEY -> value

    def add(self, name, status, value="", evidence="", export: str | None = None):
        self.checks.append(Check(name, status, str(value), evidence))
        if export and status == OK:
            self.values[export] = str(value)

    @property
    def exit_code(self) -> int:
        return exit_code(self.checks)

    def render(self) -> str:
        width = max([22] + [len(c.name) for c in self.checks])
        return "\n".join([self.title] + [c.line(width) for c in self.checks])

    def as_json(self) -> dict:
        return {"title": self.title, "exit_code": self.exit_code,
                "checks": [asdict(c) for c in self.checks], "values": self.values}


def exit_code(checks: list[Check]) -> int:
    statuses = {c.status for c in checks}
    if PROBLEM in statuses:
        return 1
    if UNKNOWN in statuses:
        return 2
    return 0


# --- dotenv -----------------------------------------------------------------

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def parse_env(text: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Parse dotenv text the way python-dotenv does for our files.

    Handles comments, `export`, single/double quotes, and ${VAR} / $VAR
    references to earlier keys (then to `environ`). Single-quoted values are
    literal. An unresolvable reference expands to "" like python-dotenv.
    """
    environ = os.environ if environ is None else environ
    out: dict[str, str] = {}
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if not m or raw.lstrip().startswith("#"):
            continue
        key, val = m.group(1), m.group(2).strip()
        literal = False
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            literal = val[0] == "'"
            val = val[1:-1]
        else:
            val = re.sub(r"\s+#.*$", "", val)  # trailing comment on unquoted value
        if not literal:
            val = _REF.sub(lambda r: out.get(r.group(1) or r.group(2),
                                             environ.get(r.group(1) or r.group(2), "")), val)
        out[key] = val
    return out


def container_to_host(path: str, mounts: list[dict]) -> str | None:
    """Map a container path to its host path through the longest covering mount."""
    best = None
    for m in mounts:
        dest = m["Destination"].rstrip("/")
        if path == dest or path.startswith(dest + "/"):
            if best is None or len(dest) > len(best["Destination"].rstrip("/")):
                best = m
    if best is None:
        return None
    rest = path[len(best["Destination"].rstrip("/")):]
    return best["Source"].rstrip("/") + rest


def mount_point(path: str) -> str:
    p = Path(path).resolve()
    while not os.path.ismount(p) and p != p.parent:
        p = p.parent
    return str(p)


def _run(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=20)
        return r.returncode, (r.stdout.strip() or r.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


# --- the probe (runs on the target host) -------------------------------------

def probe(spec: dict) -> dict:
    """Resolve and check paths on THIS host.

    spec keys:
      repo        checkout path (may start with ~)
      env_file    dotenv path relative to repo (default ".env")
      keys        [{"key", "kind": "dir"|"file", "required": bool,
                    "under": KEY (resolve basename of this key's value under KEY)}]
      venv        venv dir relative to repo, checked for bin/python (optional)
      container   docker container name that must be running (optional)
      hostname    expected short hostname (optional)
      mount_of    key whose mount point to report as DATA_VOLUME (optional)
      dev_db      true: check {DATA_DIR}/flyfun.db is the DB the app opens
      data_mount  key (e.g. HOST_DATA_DIR) the container must bind-mount; exports
                  CONTAINER_DATA_DIR. A key with "container_path": true is a
                  container path, translated to the host through docker's mounts.
      env_optional  reason string: a missing env file is a skip, not a problem
                    (a cloud session has no .env by design)
    """
    checks: list[Check] = []
    values: dict[str, str] = {}

    def add(name, status, value="", evidence="", export=None):
        checks.append(Check(name, status, str(value), evidence))
        if export and status == OK:
            values[export] = str(value)

    actual_host = socket.gethostname().split(".")[0]
    want = spec.get("hostname")
    if want:
        if actual_host.lower() == want.lower():
            add("hostname", OK, actual_host)
        else:
            add("hostname", PROBLEM, actual_host,
                f"expected {want} -- the ssh name reaches the wrong machine")

    repo = Path(os.path.expanduser(spec["repo"]))
    if not (repo / ".git").exists():
        add("repo", PROBLEM, str(repo), "no .git here")
        return {"checks": [asdict(c) for c in checks], "values": values}
    rc, head = _run(["git", "rev-parse", "--short", "HEAD"], cwd=str(repo))
    _, branch = _run(["git", "branch", "--show-current"], cwd=str(repo))
    add("repo", OK if rc == 0 else UNKNOWN, str(repo),
        f"{branch or 'detached'} @ {head}" if rc == 0 else f"git: {head}", export="REPO")
    if rc == 0:
        values["HEAD"], values["BRANCH"] = head, branch

    if spec.get("venv"):
        py = repo / spec["venv"] / "bin" / "python"
        add("venv", OK if py.exists() else PROBLEM, str(py.parent.parent),
            "" if py.exists() else "no bin/python", export="VENV")

    env_path = repo / spec.get("env_file", ".env")
    if not env_path.is_file():
        if spec.get("env_optional"):
            add("env file", SKIP, str(env_path), spec["env_optional"])
        else:
            add("env file", PROBLEM, str(env_path), "missing")
        return {"checks": [asdict(c) for c in checks], "values": values}
    env = parse_env(env_path.read_text(), environ={})
    add("env file", OK, str(env_path))

    # The container's bind mounts, read from docker itself rather than assumed
    # from the compose file: they are what maps a container path to a host path.
    mounts: list[dict] | None = None
    if spec.get("container"):
        rc, out = _run(["docker", "inspect", "-f", "{{json .Mounts}}", spec["container"]])
        if rc == 0:
            try:
                mounts = [m for m in json.loads(out) if m.get("Source") and m.get("Destination")]
            except json.JSONDecodeError:
                mounts = None

    for k in spec.get("keys", []):
        key, kind, required = k["key"], k.get("kind", "dir"), k.get("required", True)
        val = env.get(key, "")
        if k.get("container_path") and val:
            # A container path in the .env (e.g. AIRPORTS_DB=/app/data/nav.db):
            # translate it through the mount that covers it.
            if mounts is None:
                add(k.get("label", key), UNKNOWN, val, "container mounts unreadable")
                continue
            host = container_to_host(val, mounts)
            if host is None:
                add(k.get("label", key), PROBLEM, val, "no container mount covers this path")
                continue
            val = host
        if not val:
            add(k.get("label", key), PROBLEM if required else SKIP, "(not set)",
                "required in .env" if required else "", )
            continue
        p = Path(os.path.expanduser(val))
        exists = p.is_dir() if kind == "dir" else p.is_file()
        if exists:
            ev = kind
            if kind == "file":
                ev = f"file, {p.stat().st_size / 1e6:.1f} MB"
            add(k.get("label", key), OK, str(p), ev, export=k.get("export", key))
        else:
            what = "exists but is not a " + kind if p.exists() else "does not exist"
            add(k.get("label", key), PROBLEM, str(p), what)

    if spec.get("data_mount") and mounts is not None:
        host_dir = values.get(spec["data_mount"])
        hit = [m for m in mounts if host_dir and
               os.path.realpath(m["Source"]) == os.path.realpath(host_dir)]
        if hit:
            add("data mount", OK, hit[0]["Destination"], f"container path of {spec['data_mount']}",
                export="CONTAINER_DATA_DIR")
        elif host_dir:
            add("data mount", PROBLEM, host_dir, f"the container does not mount "
                f"{spec['data_mount']} -- the app is reading other data")
    elif spec.get("data_mount") and spec.get("container"):
        add("data mount", UNKNOWN, spec["container"], "docker inspect failed")

    if spec.get("mount_of") and spec["mount_of"] in values:
        add("DATA_VOLUME", OK, mount_point(values[spec["mount_of"]]),
            f"mount point of {spec['mount_of']}", export="DATA_VOLUME")

    if spec.get("dev_db"):
        data_dir = values.get("DATA_DIR")
        url = env.get("DATABASE_URL", "")
        if data_dir:
            db = Path(data_dir) / "flyfun.db"
            if db.is_file():
                ev = f"{db.stat().st_size / 1e6:.0f} MB"
                if url:
                    ev += ("; DATABASE_URL also set -- alembic uses it, the app "
                           "ignores it in dev (see worktree-init DB notes)")
                add("dev DB", OK if not url else UNKNOWN, str(db), ev, export="DEV_DB")
            else:
                add("dev DB", PROBLEM, str(db), "the app opens {DATA_DIR}/flyfun.db; not there")

    if spec.get("container"):
        rc, out = _run(["docker", "ps", "--filter", f"name=^{spec['container']}$",
                        "--format", "{{.Names}}\t{{.Status}}"])
        if rc != 0:
            add("container", UNKNOWN, spec["container"], f"docker ps failed: {out[:120]}")
        elif not out:
            add("container", PROBLEM, spec["container"], "not running")
        else:
            status = out.split("\t", 1)[-1]
            healthy = "unhealthy" not in status and "starting" not in status
            add("container", OK if healthy else PROBLEM, spec["container"], status,
                export="CONTAINER" if healthy else None)

    return {"checks": [asdict(c) for c in checks], "values": values}


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "probe":
        print(json.dumps(probe(json.loads(argv[1]))))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
