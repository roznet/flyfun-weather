#!/usr/bin/env python3
"""Pull one complete ECMWF GRIB run from the droplet into the local ECMWF_GRIB_DIR.

    sync_ecmwf.py [--run YYYYMMDD_HHz] [--force] [--dry-run] [--dest DIR]
    sync_ecmwf.py --list-stale [--dest DIR]
    sync_ecmwf.py --delete-stale TAG [TAG ...] [--dest DIR]

- Run: `--run`, else the newest complete sentinel (`.ready_<tag>`, never `.partial`) in the
  server's HOST_ECMWF_GRIB_DIR (via scripts/ops/hosts.py). Its sentinel is printed
  (files=N/M, base_time). Already synced locally -> nothing to do unless --force.
- rsync takes only files whose run INIT matches (`brg_*_fc_<RUN_TS>_*`: the valid-time slot
  would also match older runs), plus the sentinel and delivery_config.json, and excludes
  `*.idx` FIRST: cfgrib indexes store the server's absolute path, are rejected locally and,
  being written with an exclusive create, never replaced -- every decode would full-scan
  (~30-40 s vs ~3 s per request).
- Then checks the local file count against the sentinel's files=N, warns on leftover *.idx,
  and lists stale runs (sentinel older than 24 h, never the one just synced) -- it never
  deletes them: `--delete-stale TAG...` does, as a separate step after the user agreed.

Dest: --dest, else ECMWF_GRIB_DIR from this checkout's .env (created if missing), else
~/tmp/ecmwf/data with a note to set it. `--source HOST:DIR` (or a local dir) overrides the
server, for testing. Exit codes as opscheck.py.
"""
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402
from opscheck import OK, PROBLEM, SKIP, UNKNOWN, WARN, Report, parse_env  # noqa: E402

TAG = re.compile(r"^(\d{8})_(\d{2})z(\.partial)?$")
DEFAULT_DEST = Path.home() / "tmp" / "ecmwf" / "data"
STALE_S = 24 * 3600
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def run_ts(tag: str) -> str:
    """20260426_00z -> 20260426T000000Z (the GRIB filename's run-init stamp)."""
    m = TAG.match(tag)
    if not m:
        raise ValueError(f"not a run tag: {tag!r} (want YYYYMMDD_HHz)")
    return f"{m.group(1)}T{m.group(2)}0000Z"


def rsync_filters(tag: str) -> list[str]:
    # Order matters: rsync takes the first matching rule, so the .idx exclude must come
    # before the brg_* include or the indexes ride along.
    return ["--exclude=*.idx", f"--include=brg_*_fc_{run_ts(tag)}_*", f"--include=.ready_{tag}",
            "--include=delivery_config.json", "--exclude=*"]


def sentinel_files(text: str) -> tuple[int, int] | None:
    m = re.search(r"files=(\d+)/(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def run_files(dest: Path, tag: str) -> list[Path]:
    marker = f"_fc_{run_ts(tag)}_"
    return [p for p in dest.glob("brg_*") if marker in p.name and not p.name.endswith(".idx")]


def stale_runs(dest: Path, keep: str | None, now: float | None = None) -> list[tuple[str, float, int, int]]:
    """(tag, age_h, files, bytes) for complete sentinels older than 24 h, except `keep`."""
    now = now or time.time()
    out = []
    for s in sorted(dest.glob(".ready_*z")):
        tag = s.name[len(".ready_"):]
        if tag == keep or not TAG.match(tag):
            continue
        age = now - s.stat().st_mtime
        if age > STALE_S:
            files = run_files(dest, tag)
            out.append((tag, age / 3600, len(files), sum(f.stat().st_size for f in files)))
    return out


def resolve_dest(arg: str | None, rep: Report) -> Path:
    if arg:
        return Path(arg).expanduser()
    env_file = hosts.REPO_ROOT / ".env"
    val = parse_env(env_file.read_text(), environ={}).get("ECMWF_GRIB_DIR", "") \
        if env_file.is_file() else ""
    if val:
        return Path(val).expanduser()
    rep.add("dest", WARN, str(DEFAULT_DEST), "ECMWF_GRIB_DIR not set in .env -- set it to "
            "this path so the app picks the run up")
    return DEFAULT_DEST


def source(a, rep: Report) -> tuple[str | None, str]:
    """(ssh target or None for a local dir, remote dir)."""
    if a.source:
        host, sep, path = a.source.partition(":")
        return (host, path) if sep else (None, a.source)
    try:
        v = hosts.server_values("SERVER_SSH", "HOST_ECMWF_GRIB_DIR")
    except SystemExit as exc:
        rep.add("server", UNKNOWN, "", str(exc).splitlines()[0])
        raise
    return v["SERVER_SSH"], v["HOST_ECMWF_GRIB_DIR"]


def sh(host: str | None, cmd: str) -> subprocess.CompletedProcess:
    argv = [*SSH, host, cmd] if host else ["bash", "-c", cmd]
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def cmd_sync(a, rep: Report, dest: Path) -> None:
    try:
        host, src = source(a, rep)
    except SystemExit:
        return
    q = shlex.quote
    if a.run:
        tag = a.run
        try:
            run_ts(tag)
        except ValueError as exc:
            rep.add("run", PROBLEM, tag, str(exc))
            return
    else:
        r = sh(host, f"cd {q(src)} && ls -1 .ready_*z 2>/dev/null | sort | tail -1")
        if r.returncode == 255:
            rep.add("server", UNKNOWN, host or src, r.stderr.strip()[-200:])
            return
        last = r.stdout.strip()
        if not last:
            rep.add("run", PROBLEM if r.returncode == 0 else UNKNOWN, src,
                    r.stderr.strip()[-150:] or "no complete .ready_*z sentinel on the server")
            return
        tag = last[len(".ready_"):]
    sent = sh(host, f"cat {q(src + '/.ready_' + tag)}")
    if sent.returncode != 0:
        rep.add("run", PROBLEM, tag, f"no sentinel .ready_{tag} on the server")
        return
    want = sentinel_files(sent.stdout)
    base = re.search(r"base_time=(\S+)", sent.stdout)
    rep.add("run", OK, tag, f"files={want[0]}/{want[1]}" if want else "files=? in sentinel",
            export="RUN")
    if base:
        rep.values["BASE_TIME"] = base.group(1)
    if (dest / f".ready_{tag}").exists() and not a.force:
        n = len(run_files(dest, tag))
        rep.add("sync", SKIP, str(dest), f"run {tag} already here ({n} files) -- --force to re-sync")
        return

    dest.mkdir(parents=True, exist_ok=True)
    remote = f"{host}:{src}/" if host else f"{src.rstrip('/')}/"
    cmd = ["rsync", "-a", "--stats", *(["-n"] if a.dry_run else []), *rsync_filters(tag),
           remote, f"{dest}/"]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    took = time.time() - t0
    size = next((ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines()
                 if ln.startswith("Total transferred file size")), "?")
    # 24 = "some files vanished": the watcher can delete one mid-transfer; the count decides.
    if r.returncode not in (0, 24):
        rep.add("rsync", PROBLEM, remote, (r.stderr or r.stdout).strip()[-250:])
        return
    if a.dry_run:
        n = sum(1 for ln in r.stdout.splitlines() if "_fc_" in ln)
        rep.add("rsync", SKIP, f"dry run: {size}", f"{n} run files would transfer")
        return
    rep.add("rsync", OK, f"{size} in {took:.0f}s",
            "exit 24 (a file vanished mid-transfer)" if r.returncode == 24 else "")
    have = len(run_files(dest, tag))
    if want:
        rep.add("file count", OK if have == want[0] else PROBLEM, f"{have} local",
                f"sentinel files={want[0]}/{want[1]}")
    else:
        rep.add("file count", UNKNOWN, f"{have} local", "sentinel has no files=N/M")
    idx = list(dest.glob("*.idx"))
    if idx:
        rep.add("stale indexes", WARN, f"{len(idx)} *.idx in {dest}",
                f"server-path cfgrib indexes force full scans: rm {dest}/*.idx")
    du = subprocess.run(["du", "-sh", str(dest)], capture_output=True, text=True)
    rep.values["DEST_SIZE"] = du.stdout.split()[0] if du.stdout else "?"
    list_stale(rep, dest, keep=tag)


def list_stale(rep: Report, dest: Path, keep: str | None) -> None:
    stale = stale_runs(dest, keep)
    if not stale:
        rep.add("stale runs", OK, "none older than 24 h")
        return
    for tag, age, n, size in stale:
        rep.add("stale run", WARN, tag, f"{age:.0f} h old, {n} files, {size / 1e9:.1f} GB -- "
                f"delete only after the user agrees: --delete-stale {tag}")
    orphans = [p for p in dest.glob("brg_*") if not any(
        f"_fc_{run_ts(s.name[len('.ready_'):])}_" in p.name for s in dest.glob(".ready_*z")
        if TAG.match(s.name[len(".ready_"):]))]
    if orphans:
        rep.add("orphan files", WARN, f"{len(orphans)} brg_* with no sentinel",
                "not deleted by this script; remove by hand if wanted")


def cmd_delete(a, rep: Report, dest: Path) -> None:
    for tag in a.delete_stale:
        if not TAG.match(tag) or tag.endswith(".partial"):
            rep.add("delete", PROBLEM, tag, "not a complete run tag")
            continue
        sentinel = dest / f".ready_{tag}"
        if not sentinel.exists():
            rep.add("delete", PROBLEM, tag, f"no {sentinel.name} in {dest}")
            continue
        files = run_files(dest, tag)
        size = sum(f.stat().st_size for f in files)
        for f in files:
            f.unlink()
        sentinel.unlink()
        rep.add("deleted", OK, tag, f"{len(files)} files, {size / 1e9:.1f} GB")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dest")
    ap.add_argument("--source", help=argparse.SUPPRESS)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--list-stale", action="store_true")
    g.add_argument("--delete-stale", nargs="+", metavar="TAG")
    a = ap.parse_args(argv)
    rep = Report("sync-ecmwf")
    dest = resolve_dest(a.dest, rep)
    rep.values["DEST"] = str(dest)
    if a.delete_stale:
        cmd_delete(a, rep, dest)
    elif a.list_stale:
        list_stale(rep, dest, keep=None)
    else:
        cmd_sync(a, rep, dest)
    print(rep.render())
    if "DEST_SIZE" in rep.values:
        print(f"\n{dest}: {rep.values['DEST_SIZE']} total. Refresh a briefing locally; its "
              "fetch_meta.json should show 'ECMWF GRIB enrichment applied'.")
    return rep.exit_code


if __name__ == "__main__":
    sys.exit(main())
