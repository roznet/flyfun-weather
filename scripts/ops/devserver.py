#!/usr/bin/env python3
"""Start the dev server for this checkout in tmux: uvicorn --reload + esbuild watch.

    devserver.py                  HTTP, session wb-<basename>, port 8000 (main) / 8001-8010
    devserver.py --https          the singleton wb-https on https://localhost.ro-z.me:8443
                                  (iOS simulator); moves it here if it serves another checkout
    devserver.py --status         list running wb-* sessions, their checkout and port
    devserver.py --stop [--https] stop this checkout's session (or wb-https)
    --dry-run                     check everything, print what would happen, change nothing
    --skip-migration-check        start even though alembic current != heads

Run it from the checkout root (or pass --root). Checks before starting: the
checkout is weatherbrief, ./venv exists and its editable install resolves to
this checkout (never ../main/venv), .env exists, the TLS cert is readable
(--https), and alembic current == heads. Pending migrations stop it with a
problem line: whether to `alembic upgrade head` or `stamp head` is a judgement
(see the devserver skill), not this script's.

Each session records its checkout and port in its tmux environment
(WB_ROOT / WB_PORT); that, not the active pane's cwd, says which checkout a
session serves. After starting, it waits for /health to answer.
Exit codes as opscheck.py: 0 ok, 1 problem, 2 could not tell.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opscheck import OK, PROBLEM, SKIP, UNKNOWN, Report  # noqa: E402

CERT_DIR = Path("/usr/local/etc/letsencrypt/live/ro-z.me")
HTTPS_SESSION, HTTPS_PORT, HTTPS_HOST = "wb-https", 8443, "localhost.ro-z.me"
WORKTREE_PORTS = range(8001, 8011)


def run(cmd, cwd=None, timeout=60) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout)


# --- tmux ---------------------------------------------------------------------------

def tmux(*args) -> subprocess.CompletedProcess:
    return run(["tmux", *args], timeout=10)


def session_exists(name: str) -> bool:
    return tmux("has-session", "-t", f"={name}").returncode == 0


def session_info(name: str) -> tuple[str | None, int | None]:
    """(checkout root, port) a session serves.

    Read from WB_ROOT/WB_PORT, set at creation. For a session started before
    this script, fall back to pane 0's start path -- NOT the active pane, which
    is usually the esbuild pane sitting in web/.
    """
    root = port = None
    for line in tmux("show-environment", "-t", f"={name}").stdout.splitlines():
        if line.startswith("WB_ROOT="):
            root = line.split("=", 1)[1]
        elif line.startswith("WB_PORT="):
            port = int(line.split("=", 1)[1])
    if root is None:
        r = tmux("display-message", "-p", "-t", f"={name}:0.0", "#{pane_start_path}")
        start = r.stdout.strip()
        if start:
            root = start[:-4] if start.endswith("/web") else start
    return root, port


def wb_sessions() -> list[str]:
    r = tmux("list-sessions", "-F", "#{session_name}")
    return [s for s in r.stdout.split() if s.startswith("wb-")]


# --- ports & health -------------------------------------------------------------------

def port_in_use(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex((host, port)) == 0:
                return True
    return False


def pick_port(basename: str, sticky: int | None, in_use=port_in_use) -> int | None:
    if basename == "main":
        return 8000
    if sticky:
        return sticky
    return next((p for p in WORKTREE_PORTS if not in_use(p)), None)


def health(url: str, timeout: float = 3) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            return r.status == 200, f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, str(getattr(exc, "reason", exc))


# --- alembic --------------------------------------------------------------------------

_REV = re.compile(r"^([0-9a-f]{3,40}|[0-9]{3,}[0-9a-z_]*)\b", re.M)


def revisions(text: str) -> set[str]:
    """Revision ids from `alembic current` / `alembic heads` output."""
    return set(_REV.findall(text))


def check_alembic(rep: Report, root: Path) -> None:
    alembic = root / "venv" / "bin" / "alembic"
    cur = run([str(alembic), "current"], cwd=root, timeout=120)
    heads = run([str(alembic), "heads"], cwd=root, timeout=120)
    if cur.returncode or heads.returncode:
        err = (cur.stderr if cur.returncode else heads.stderr).strip().splitlines()
        rep.add("alembic", UNKNOWN, "current vs heads", (err or ["failed"])[-1][:200])
        return
    c, h = revisions(cur.stdout), revisions(heads.stdout)
    if not h:
        rep.add("alembic", UNKNOWN, "current vs heads", "could not parse `alembic heads`")
    elif c == h:
        rep.add("alembic", OK, ", ".join(sorted(h)), "at head")
    else:
        rep.add("alembic", PROBLEM, f"current {','.join(sorted(c)) or 'none'} -> "
                f"head {','.join(sorted(h))}",
                "migration pending OR schema already ahead of the stamp: diagnose before "
                "upgrading (devserver skill), then rerun; --skip-migration-check to start anyway")


# --- the run --------------------------------------------------------------------------

def preflight(rep: Report, root: Path, https: bool) -> None:
    py = root / "pyproject.toml"
    if not py.is_file() or not re.search(r'(?m)^name\s*=\s*"weatherbrief"', py.read_text()):
        rep.add("checkout", PROBLEM, str(root), "not a weatherbrief checkout (pyproject.toml)")
        return
    rep.add("checkout", OK, str(root))
    venv_py = root / "venv" / "bin" / "python"
    if not venv_py.exists():
        hint = ("python3 -m venv venv && venv/bin/pip install -e '.[dev]'"
                if root.name == "main" else "create it with scripts/ops/worktree_init.py")
        rep.add("venv", PROBLEM, str(root / "venv"), f"missing -- {hint}; never ../main/venv")
        return
    r = run([str(venv_py), "-c", "import weatherbrief; print(weatherbrief.__file__)"], cwd="/")
    got = r.stdout.strip()
    if r.returncode or not got.startswith(str(root) + os.sep):
        rep.add("editable install", PROBLEM, got or r.stderr.strip()[-150:],
                f"must resolve under {root}/src -- reinstall with `venv/bin/pip install -e "
                "'.[dev]'` from this checkout (not auto-fixed: a mis-wired venv is a clue)")
    else:
        rep.add("editable install", OK, got)
    if (root / ".env").is_file():
        rep.add(".env", OK, str(root / ".env"))
    else:
        rep.add(".env", PROBLEM, str(root / ".env"),
                "missing -- main: restore it (backed up in ~/scripts/config); worktree: "
                "scripts/ops/worktree_init.py copies it")
    if https:
        certs = [CERT_DIR / "privkey.pem", CERT_DIR / "fullchain.pem"]
        if all(os.access(c, os.R_OK) for c in certs):
            rep.add("TLS cert", OK, str(CERT_DIR))
        else:
            rep.add("TLS cert", PROBLEM, str(CERT_DIR), "not readable -- the user runs: "
                    f"sudo chmod -R a+rX /usr/local/etc/letsencrypt/{{live,archive}}/ro-z.me")


def start(root: Path, session: str, port: int, https: bool) -> None:
    venv = root / "venv" / "bin"
    if https:
        cmd = (f"{venv}/uvicorn weatherbrief.api.app:app --reload --host ::1 --port {port} "
               f"--ssl-keyfile {CERT_DIR}/privkey.pem --ssl-certfile {CERT_DIR}/fullchain.pem")
    else:
        cmd = f"{venv}/uvicorn weatherbrief.api.app:app --reload --port {port}"
    tmux("new-session", "-d", "-s", session, "-c", str(root))
    tmux("set-environment", "-t", f"={session}", "WB_ROOT", str(root))
    tmux("set-environment", "-t", f"={session}", "WB_PORT", str(port))
    tmux("send-keys", "-t", f"={session}:0.0", cmd, "Enter")
    tmux("split-window", "-h", "-t", f"={session}:0", "-c", str(root / "web"))
    tmux("send-keys", "-t", f"={session}:0.1", "npm run dev", "Enter")


def wait_healthy(rep: Report, session: str, url: str, seconds: int) -> None:
    deadline, detail = time.time() + seconds, ""
    while time.time() < deadline:
        ok, detail = health(url)
        if ok:
            rep.add("health", OK, url, detail, export="URL")
            return
        time.sleep(1)
    pane = tmux("capture-pane", "-p", "-t", f"={session}:0.0").stdout.strip().splitlines()
    rep.add("health", PROBLEM, url, f"no 200 after {seconds}s ({detail}); uvicorn pane: "
            + " | ".join(pane[-4:])[-300:])


def cmd_status() -> int:
    names = wb_sessions()
    if not names:
        print("no wb-* sessions running")
        return 0
    for name in names:
        root, port = session_info(name)
        url = (f"https://{HTTPS_HOST}:{HTTPS_PORT}" if name == HTTPS_SESSION
               else f"http://localhost:{port}" if port else "port unknown")
        ok, detail = health(url + "/health") if port or name == HTTPS_SESSION else (False, "")
        print(f"{name:<24} {root or '?':<60} {url}  ({'up' if ok else detail or 'unknown'})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--https", "--simulator", dest="https", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-migration-check", action="store_true")
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--wait", type=int, default=45, help="seconds to wait for /health")
    args = ap.parse_args(argv)

    if args.status:
        return cmd_status()
    top = run(["git", "rev-parse", "--show-toplevel"], cwd=args.root or Path.cwd())
    root = Path(top.stdout.strip()) if top.returncode == 0 else (args.root or Path.cwd())
    root = root.resolve()
    session = HTTPS_SESSION if args.https else f"wb-{root.name}"
    rep = Report(f"devserver {session} ({root})")

    if args.stop:
        if not session_exists(session):
            rep.add("stop", SKIP, session, "not running")
        elif args.dry_run:
            rep.add("stop", SKIP, session, "dry run: would kill it")
        else:
            tmux("kill-session", "-t", f"={session}")
            rep.add("stop", OK, session, "killed")
        print(rep.render())
        return rep.exit_code

    sticky = None
    if session_exists(session):
        owner, port = session_info(session)
        if owner == str(root):
            url = (f"https://{HTTPS_HOST}:{HTTPS_PORT}" if args.https
                   else f"http://localhost:{port}" if port else None)
            if url:
                ok, detail = health(url + "/health")
                rep.add("session", OK if ok else PROBLEM, session,
                        f"already running here, not restarted; {url} {detail}")
                if ok:
                    rep.values["URL"] = url
            else:
                rep.add("session", UNKNOWN, session, "already running here; port unknown "
                        "(started before devserver.py) -- `--stop` then rerun to adopt it")
            print(rep.render())
            return rep.exit_code
        if not args.https:
            rep.add("session", PROBLEM, session, f"name taken by another checkout ({owner})")
            print(rep.render())
            return rep.exit_code
        sticky = None  # wb-https serves another checkout: move it here (below)
        rep.add("session", SKIP if args.dry_run else OK, session,
                f"{'would move' if args.dry_run else 'moving'} from {owner or '?'} to here")

    preflight(rep, root, args.https)
    if rep.exit_code == 1:
        print(rep.render())
        return 1
    if args.skip_migration_check:
        rep.add("alembic", SKIP, "", "--skip-migration-check")
    else:
        check_alembic(rep, root)
        if rep.exit_code == 1:
            print(rep.render())
            return 1

    port = HTTPS_PORT if args.https else pick_port(root.name, sticky)
    if port is None:
        rep.add("port", PROBLEM, f"{WORKTREE_PORTS.start}-{WORKTREE_PORTS.stop - 1}",
                "all in use")
        print(rep.render())
        return 1
    url = f"https://{HTTPS_HOST}:{port}" if args.https else f"http://localhost:{port}"
    if args.dry_run:
        rep.add("start", SKIP, url, f"dry run: would start tmux {session}")
    else:
        if args.https and session_exists(session):
            tmux("kill-session", "-t", f"={session}")
            time.sleep(1)
        if port_in_use(port):
            rep.add("port", PROBLEM, str(port), "already in use by something outside tmux")
            print(rep.render())
            return 1
        start(root, session, port, args.https)
        wait_healthy(rep, session, url + "/health", args.wait)

    if not args.https and session_exists(HTTPS_SESSION):
        owner, _ = session_info(HTTPS_SESSION)
        if owner and owner != str(root):
            rep.add("wb-https", SKIP, owner, "the iOS simulator URL serves that checkout, "
                    "not this one; `devserver.py --https` here to move it")
    print(rep.render())
    if not args.dry_run and rep.exit_code != 1:
        print(f"\nattach: tmux attach -t {session}   (pane 0 uvicorn --reload, pane 1 esbuild)")
    return rep.exit_code


if __name__ == "__main__":
    sys.exit(main())
