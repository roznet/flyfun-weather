#!/usr/bin/env python3
"""Create a sibling worktree ready for `devserver.py`: venv, npm, .env.

    worktree_init.py BRANCH [--from BASE] [--fork-db] [--no-npm] [--dry-run]

- The worktree goes next to the main checkout (`<parent>/BRANCH`), whichever
  checkout you run this from.
- A new branch forks from **origin/main** by default (fetched first), not local
  main: local main often holds unpushed commits that would otherwise ride into
  the PR. An existing local branch is checked out as is; a branch that exists
  only on origin is checked out tracking it.
- Its own venv with an editable install that must resolve to the worktree's
  src/ (checked with PYTHONPATH cleared), `npm install` in web/, and main's
  .env copied verbatim, so DATA_DIR and the dev DB stay shared with main.
- `--fork-db` instead gives the worktree a private copy of the dev DB (for a
  branch that adds a migration): data/ symlinks to main's except flyfun.db,
  which is copied with SQLite's backup API, and DATA_DIR is repointed. Then it
  checks the app and alembic open the same file.

On a failure it stops and leaves the partial state for inspection; the report
says how to remove it. Never runs alembic and never starts a server.
Exit codes as opscheck.py: 0 ok, 1 problem, 2 could not tell.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opscheck import OK, PROBLEM, SKIP, UNKNOWN, Report, parse_env  # noqa: E402

BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
REL_PATH_RE = re.compile(r"^[A-Z_]+=(\.\.?/|[a-z][a-z0-9_-]*/)", re.M)
DB_FILES = ("flyfun.db", "flyfun.db-shm", "flyfun.db-wal")


class Stop(Exception):
    """A step failed; the report already carries the problem line."""


def run(cmd, cwd=None, env=None, timeout=900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout)


def git(*args, cwd) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=cwd, timeout=120)


def tail(text: str, n: int = 300) -> str:
    return " ".join(text.strip().split())[-n:]


def main_checkout(start: Path) -> Path:
    r = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=start)
    if r.returncode != 0:
        raise SystemExit(f"not inside a git checkout: {start}")
    return Path(r.stdout.strip()).parent


def relative_env_lines(env_text: str) -> list[str]:
    """`.env` values that would resolve against the worktree CWD."""
    return [m.group(0).split("=", 1)[0] for m in REL_PATH_RE.finditer(env_text)]


def existing_worktree(main: Path, branch: str) -> str | None:
    out = git("worktree", "list", "--porcelain", cwd=main).stdout
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line.split(" ", 1)[1]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def clean_env() -> dict[str, str]:
    """The environment for the new venv: no PYTHONPATH/VIRTUAL_ENV leaking in,
    which would make the editable check import main's src instead."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    return env


# --- steps -------------------------------------------------------------------------

def step_worktree(rep: Report, main: Path, branch: str, base: str | None,
                  path: Path, dry: bool) -> bool:
    """Create the worktree; returns True when it created a new branch."""
    if not BRANCH_RE.match(branch) or branch.startswith("-"):
        rep.add("branch name", PROBLEM, branch, "letters, digits, . _ / - only")
        raise Stop
    if (wt := existing_worktree(main, branch)):
        rep.add("worktree", PROBLEM, wt, f"branch {branch} already has a worktree")
        raise Stop
    if path.exists():
        rep.add("worktree", PROBLEM, str(path), "path already exists")
        raise Stop

    local = git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=main)
    if local.returncode == 0:
        cmd, origin = ["worktree", "add", str(path), branch], f"existing local branch {branch}"
        new_branch = False
        if base:
            origin += f" (--from {base} ignored: the branch exists)"
    else:
        fetch = git("fetch", "--quiet", "origin", cwd=main)
        if fetch.returncode != 0:
            rep.add("git fetch", UNKNOWN, "origin", f"fetch failed: {tail(fetch.stderr)}; "
                    "base may be stale")
        new_branch = True
        remote = git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}",
                     cwd=main)
        if remote.returncode == 0 and not base:
            cmd = ["worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}"]
            origin = f"origin/{branch} (tracking)"
        else:
            base = base or "origin/main"
            cmd = ["worktree", "add", "-b", branch, str(path), base]
            origin = f"new branch from {base}"
            if base == "origin/main":
                ahead = git("rev-list", "--count", "origin/main..main", cwd=main).stdout.strip()
                if ahead and ahead != "0":
                    origin += f" (local main has {ahead} unpushed commit(s), not included)"
    if dry:
        rep.add("worktree", SKIP, str(path), f"dry run: git {' '.join(cmd)} -- {origin}")
        return new_branch
    r = git(*cmd, cwd=main)
    if r.returncode != 0:
        rep.add("worktree", PROBLEM, str(path), tail(r.stderr))
        raise Stop
    head = git("rev-parse", "--short", "HEAD", cwd=path).stdout.strip()
    rep.add("worktree", OK, str(path), f"{branch} @ {head}, {origin}", export="WORKTREE")
    return new_branch


def step_venv(rep: Report, main: Path, path: Path) -> None:
    env = clean_env()
    # Same interpreter as main's venv, so every checkout runs one Python version.
    py = main / "venv" / "bin" / "python"
    steps = [
        ("venv", [str(py) if py.exists() else sys.executable, "-m", "venv", "venv"]),
        ("pip upgrade", ["venv/bin/python", "-m", "pip", "install", "-q", "--upgrade", "pip"]),
        ("pip install", ["venv/bin/python", "-m", "pip", "install", "-q", "-e", ".[dev]"]),
    ]
    for name, cmd in steps:
        r = run(cmd, cwd=path, env=env)
        if r.returncode != 0:
            rep.add(name, PROBLEM, " ".join(cmd[-3:]), tail(r.stderr or r.stdout))
            raise Stop
    ver = run(["venv/bin/python", "--version"], cwd=path).stdout.strip()
    rep.add("venv", OK, str(path / "venv"), ver)
    r = run([str(path / "venv/bin/python"), "-c",
             "import weatherbrief; print(weatherbrief.__file__)"],
            cwd="/", env=env)  # cwd=/ so the worktree's own dir can't shadow the install
    got = r.stdout.strip()
    if r.returncode != 0:
        rep.add("editable install", PROBLEM, "weatherbrief", tail(r.stderr))
        raise Stop
    if not got.startswith(str(path) + os.sep):
        rep.add("editable install", PROBLEM, got, f"must resolve under {path}/src")
        raise Stop
    rep.add("editable install", OK, got)


def step_npm(rep: Report, path: Path) -> None:
    if not shutil.which("npm"):
        rep.add("npm install", UNKNOWN, "web/", "npm not on PATH")
        return
    r = run(["npm", "install", "--no-audit", "--no-fund"], cwd=path / "web")
    if r.returncode != 0:
        rep.add("npm install", PROBLEM, "web/", tail(r.stderr or r.stdout))
        raise Stop
    rep.add("npm install", OK, str(path / "web"))


def step_env(rep: Report, main: Path, path: Path) -> None:
    src = main / ".env"
    if not src.is_file():
        rep.add(".env", PROBLEM, str(src), "main has no .env to copy")
        raise Stop
    shutil.copy2(src, path / ".env")
    rel = relative_env_lines(src.read_text())
    if rel:
        rep.add(".env", UNKNOWN, "copied from main",
                f"relative values resolve against the worktree: {', '.join(rel)} "
                "-- not rewritten, the user owns .env")
    else:
        rep.add(".env", OK, "copied from main", "no relative paths; DATA_DIR shared with main")


def step_migrations(rep: Report, main: Path, branch: str) -> bool:
    r = git("diff", "--name-only", "origin/main..." + branch, "--", "alembic/versions",
            cwd=main)
    files = [f for f in r.stdout.split() if f.endswith(".py")]
    if r.returncode != 0:
        rep.add("migrations", UNKNOWN, branch, tail(r.stderr))
        return False
    if files:
        rep.add("migrations", UNKNOWN, ", ".join(Path(f).name for f in files),
                "branch adds migrations and the dev DB is shared with main: "
                "re-run with --fork-db before running alembic or pytest")
        return True
    rep.add("migrations", OK, "none vs origin/main")
    return False


def step_fork_db(rep: Report, main: Path, path: Path) -> None:
    main_env = parse_env((main / ".env").read_text(), environ={})
    src_data = Path(main_env.get("DATA_DIR", ""))
    src_db = src_data / "flyfun.db"
    if not src_db.is_file():
        rep.add("fork DB", PROBLEM, str(src_db), "main's dev DB not found via its DATA_DIR")
        raise Stop
    data = path / "data"
    data.mkdir(exist_ok=True)  # the repo tracks data/.gitkeep, so it exists but is empty
    stray = [f.name for f in data.iterdir() if f.name != ".gitkeep"]
    if stray:
        rep.add("fork DB", PROBLEM, str(data), f"not empty: {', '.join(stray[:5])}")
        raise Stop
    for f in src_data.iterdir():
        if f.name not in DB_FILES and f.name != ".gitkeep":
            (data / f.name).symlink_to(f)
    # The backup API copies a consistent snapshot including un-checkpointed WAL
    # pages; a plain cp of flyfun.db alone can miss committed data.
    with sqlite3.connect(f"file:{src_db}?mode=ro", uri=True) as s, \
            sqlite3.connect(data / "flyfun.db") as d:
        s.backup(d)
    env_file = path / ".env"
    text = env_file.read_text()
    new, n = re.subn(r"(?m)^DATA_DIR=.*$", f"DATA_DIR={data}", text)
    if n != 1:
        rep.add("fork DB", PROBLEM, str(env_file), f"expected one DATA_DIR line, found {n}")
        raise Stop
    env_file.write_text(new)
    size = (data / "flyfun.db").stat().st_size / 1e6
    rep.add("fork DB", OK, str(data / "flyfun.db"), f"{size:.0f} MB copy; other data/ entries "
            "symlinked to main's")

    # The trap this guards: alembic honours DATABASE_URL, the dev app ignores it.
    probe = ("from dotenv import load_dotenv; load_dotenv('.env', override=True); import os;"
             "from flyfun_common.db import get_engine; print(get_engine().url.database);"
             "print(os.environ.get('DATABASE_URL') or "
             "os.path.join(os.environ['DATA_DIR'], 'flyfun.db'))")
    r = run([str(path / "venv/bin/python"), "-c", probe], cwd=path, env=clean_env(), timeout=120)
    lines = r.stdout.strip().splitlines()
    if r.returncode != 0 or len(lines) < 2:
        rep.add("fork DB check", UNKNOWN, "app vs alembic", tail(r.stderr or r.stdout))
        return
    app, alembic = (os.path.realpath(x.replace("sqlite:///", "")) for x in lines[-2:])
    if app == alembic and app.startswith(str(path)):
        rep.add("fork DB check", OK, app, "app and alembic open the same private file")
    else:
        rep.add("fork DB check", PROBLEM, f"app={app} alembic={alembic}",
                "half-applied fork: fix before running anything")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("branch")
    ap.add_argument("--from", dest="base", help="base for a NEW branch (default origin/main)")
    ap.add_argument("--fork-db", action="store_true", help="private copy of the dev DB")
    ap.add_argument("--no-npm", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="validate and print, change nothing")
    args = ap.parse_args(argv)

    main_dir = main_checkout(Path.cwd())
    path = main_dir.parent / args.branch.replace("/", "-")
    rep = Report(f"worktree {args.branch} ({path})")
    created = new_branch = False
    try:
        new_branch = step_worktree(rep, main_dir, args.branch, args.base, path, args.dry_run)
        if args.dry_run:
            print(rep.render())
            return rep.exit_code
        created = True
        step_venv(rep, main_dir, path)
        if not args.no_npm:
            step_npm(rep, path)
        step_env(rep, main_dir, path)
        has_migrations = step_migrations(rep, main_dir, args.branch)
        if args.fork_db:
            step_fork_db(rep, main_dir, path)
        elif has_migrations:
            pass  # flagged by step_migrations; the user decides
    except Stop:
        pass
    except subprocess.TimeoutExpired as exc:
        rep.add("timeout", PROBLEM, " ".join(map(str, exc.cmd))[:80], f"after {exc.timeout}s")
    except OSError as exc:  # report it like any other failed step, never a bare traceback
        rep.add("error", PROBLEM, type(exc).__name__, str(exc))
    print(rep.render())
    if rep.exit_code == 1 and created:
        drop = f" && git branch -D {args.branch}" if new_branch else ""
        print(f"\nStopped; partial state left for inspection. To remove it:\n"
              f"  git worktree remove --force {path}{drop}")
    elif rep.exit_code != 1 and not args.dry_run:
        print(f"\nNext: cd {path} && /devserver")
    return rep.exit_code


if __name__ == "__main__":
    sys.exit(main())
