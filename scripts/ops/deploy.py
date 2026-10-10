#!/usr/bin/env python3
"""The mechanical half of /deploy. The skill keeps the confirmation gates and judgement.

    deploy.py preflight                    read-only: what would deploy and is it safe
    deploy.py verify SERVER_SHA LOCAL_SHA [--since ISO]
                                           after the rebuild: is the NEW code live
    deploy.py nodes LOCAL_SHA [--node NAME] [--execute]
                                           update compute nodes (dry run by default)
    deploy.py mark-prod SERVER_SHA LOCAL_SHA [--execute] [--rollback]
                                           move prod / prod-prev (dry run by default)

preflight fetches origin, resolves both anchors (LOCAL_SHA = origin/main, SERVER_SHA = the
server's HEAD; never the local working tree), and in one ssh reads the server's HEAD, disk,
alembic revision, nav.db timestamp and recent standalone-cycle log lines. It lists the
commits and changed areas, says which test suites the change needs (it does not run them),
checks the local alembic head count, rehearses pending migrations on a scratch MySQL
(.claude/skills/deploy/mysql_migration_check.py, from the server's current revision),
compares nav.db, reads the cycle state (deploy-notes §D7) and each node's drift.

verify proves the new code is live, not just a healthy container on the right SHA
(the 2026-08-26 race): server HEAD, container health, the running container's image is
the current build (and built after --since), sha256 of up to 5 files this deploy changed
under src/ configs/ alembic/ inside the container vs origin, alembic at head, an ORM read
of briefing_packs (/health is 200 even when that table is unreadable), and public /health.

Statuses as opscheck.py (ok / problem / unknown / warn / skip); exit 0 ok (warnings
allowed), 1 a problem, 2 could not tell. `--json` on any command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402
from devserver import revisions  # noqa: E402
from opscheck import OK, PROBLEM, SKIP, UNKNOWN, WARN, Report  # noqa: E402

REPO = hosts.REPO_ROOT
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
DISK_WARN_PCT = 80
CYCLE_SOON_S = 300
# Copied to /app/<path> by the Dockerfile (web/dist is built in the image, not in git).
VERIFY_PATHS = ("src/", "configs/", "alembic/", "web/")
PLAYWRIGHT_PATHS = ("web/", "src/weatherbrief/api/", "src/weatherbrief/models/", "configs/")
# The standalone loop's own lines (scheduler.py run_standalone_verification_loop):
# "Standalone <kind> cycle: launching subprocess" ... "Standalone verification cycle
# complete" / "... cycle failed", then "Standalone verification: sleeping Ns". Other loops
# (METAR ingest, forecast fetch, Hewson) also log "sleeping": never match those.
CYCLE_START = re.compile(r"Standalone \w+ cycle: launching subprocess")
CYCLE_END = re.compile(r"Standalone verification cycle (complete|failed)")
SLEEP = re.compile(r"Standalone verification: sleeping (\d+)s")
CYCLE_GREP = "Standalone .* cycle: launching|Standalone verification cycle|Standalone verification: sleeping"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=120)


def remote(ssh: str, script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a bash script on a host via stdin (no quoting through ssh)."""
    return subprocess.run([*SSH, ssh, "bash", "-s"], input=script, capture_output=True,
                          text=True, timeout=timeout)


def sections(out: str) -> dict[str, str]:
    """Split `@@name` delimited output into {name: body}."""
    got, name, buf = {}, None, []
    for line in out.splitlines():
        if line.startswith("@@"):
            if name:
                got[name] = "\n".join(buf).strip()
            name, buf = line[2:].strip(), []
        else:
            buf.append(line)
    if name:
        got[name] = "\n".join(buf).strip()
    return got


def server_info():
    """(values, report) for the server, or raise SystemExit with the reason."""
    return hosts.server_values("SERVER_SSH", "SERVER_PROJECT_DIR", "SERVER_CONTAINER")


def parse_ts(s: str) -> datetime | None:
    """Aware ISO time, tolerating docker's nanoseconds and a trailing Z; naive = UTC."""
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.strip()).replace("Z", "+00:00")
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


_REV_LINE = re.compile(r"^(down_revision|revision)\b[^=]*=\s*(.+)$", re.M)


def heads_at(sha: str) -> set[str]:
    """Alembic head revisions as of a commit, read from git -- not the working tree,
    which may hold migrations that are not part of what deploys."""
    files = git("ls-tree", "--name-only", sha, "alembic/versions/").stdout.split()
    revs, downs = set(), set()
    for f in files:
        if not f.endswith(".py"):
            continue
        for kind, val in _REV_LINE.findall(git("show", f"{sha}:{f}").stdout):
            ids = set(re.findall(r"['\"]([^'\"]+)['\"]", val))
            (revs if kind == "revision" else downs).update(ids)
    return revs - downs


def cycle_state(lines: list[str], now: datetime | None = None) -> tuple[str, str]:
    """(status, evidence) from `docker logs -t` standalone-loop lines (deploy-notes §D7).

    The loop logs a sleeping line before every sleep, so no line at all over the window
    means the log could not be read -- never "idle".
    """
    now = now or datetime.now(timezone.utc)
    rows = []
    for ln in lines:
        ts, _, msg = ln.partition(" ")
        t = parse_ts(ts)
        if t and (CYCLE_START.search(msg) or CYCLE_END.search(msg) or SLEEP.search(msg)):
            rows.append((t, msg))
    if not rows:
        return UNKNOWN, "no standalone-loop line in the log window: cannot tell"
    t, msg = rows[-1]
    mins = (now - t).total_seconds() / 60
    if CYCLE_START.search(msg):
        return WARN, f"a cycle started {mins:.0f} min ago and has not finished: " + msg[-90:]
    if CYCLE_END.search(msg):
        return OK, f"last cycle ended {mins:.0f} min ago"
    left = int(SLEEP.search(msg).group(1)) - (now - t).total_seconds()
    if left < CYCLE_SOON_S:
        return WARN, f"next cycle due in {max(left, 0) / 60:.0f} min"
    return OK, f"idle, next cycle in {left / 3600:.1f} h"


def test_plan(changed: list[str]) -> list[tuple[str, str]]:
    plan = [("pytest", "venv/bin/python -m pytest tests/ --ignore=tests/test_llm_digest.py -q")]
    if any(f.startswith("web/") for f in changed):
        plan.append(("vitest (hard gate)", "cd web && npm test"))
    if any(f.startswith(PLAYWRIGHT_PATHS) for f in changed):
        plan.append(("playwright (warn only)", "cd web && npx playwright test --reporter=line"))
    return plan


# --- preflight -----------------------------------------------------------------------

def cmd_preflight(a) -> Report:
    rep = Report("deploy preflight")
    f = git("fetch", "--quiet", "origin")
    if f.returncode != 0:
        rep.add("git fetch", UNKNOWN, "origin", f.stderr.strip()[-200:])
    local_sha = git("rev-parse", "origin/main").stdout.strip()
    branch = git("branch", "--show-current").stdout.strip()
    rep.add("branch", OK if branch == "main" else WARN, branch or "(detached)",
            "" if branch == "main" else "not on main: the deploy ships origin/main regardless")
    ahead = git("log", "--oneline", "origin/main..HEAD").stdout.strip().splitlines()
    behind = git("log", "--oneline", "HEAD..origin/main").stdout.strip().splitlines()
    if behind:
        # Tests and the MySQL rehearsal run from this checkout: behind origin/main they
        # would test code (and migrations) that is not what deploys.
        rep.add("checkout", PROBLEM, f"{len(behind)} commit(s) behind origin/main",
                "`git pull --rebase` first: local tests and the rehearsal would check old code")
    if ahead:
        rep.add("unpushed", WARN, f"{len(ahead)} local commit(s) not on origin/main",
                "they will NOT deploy unless pushed: " + "; ".join(ahead[:3]))
    dirty = [d for d in git("status", "--short").stdout.splitlines() if d.strip()]
    if dirty:
        rep.add("uncommitted", WARN, f"{len(dirty)} path(s)", ", ".join(d[3:] for d in dirty[:6]))

    try:
        v = server_info()
    except SystemExit as exc:
        rep.add("server", UNKNOWN, "", str(exc).splitlines()[0])
        return rep
    ssh, ctr = v["SERVER_SSH"], v["SERVER_CONTAINER"]
    q = shlex.quote
    script = f"""cd {q(v['SERVER_PROJECT_DIR'])} || exit 3
echo @@head; git rev-parse HEAD
echo @@df; df -P / | tail -1
echo @@alembic; docker exec {q(ctr)} alembic current 2>/dev/null | tail -1
echo @@navdb; docker exec {q(ctr)} python3 -c "import sqlite3,os;print(sqlite3.connect(os.environ['AIRPORTS_DB']).execute(\\"select updated_at from model_metadata where key='statistics'\\").fetchone()[0])" 2>&1 | tail -1
echo @@cycle; docker logs -t --since 26h {q(ctr)} 2>&1 | grep -E {q(CYCLE_GREP)} | tail -20
echo @@end
"""
    r = remote(ssh, script)
    s = sections(r.stdout)
    if "end" not in s:
        rep.add("server", UNKNOWN, ssh, (r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}")
        return rep
    server_sha = s["head"].strip()
    rep.values.update({"SERVER_SHA": server_sha, "LOCAL_SHA": local_sha})

    # What deploys
    if git("cat-file", "-e", server_sha + "^{commit}").returncode != 0:
        rep.add("range", UNKNOWN, f"{server_sha[:8]}..{local_sha[:8]}",
                "server commit unknown locally (force-pushed? unfetched branch?)")
        return rep
    commits = git("log", "--oneline", f"{server_sha}..{local_sha}").stdout.strip().splitlines()
    if server_sha == local_sha:
        rep.add("range", OK, f"{server_sha[:8]} = origin/main", "nothing to deploy: up to date")
        rep.values["NOTHING_TO_DEPLOY"] = "1"
        return rep
    if git("merge-base", "--is-ancestor", server_sha, local_sha).returncode != 0:
        extra = git("log", "--oneline", f"{local_sha}..{server_sha}").stdout.strip().splitlines()
        rep.add("range", PROBLEM, f"server {server_sha[:8]} not an ancestor of origin/main",
                f"server has {len(extra)} commit(s) origin/main lacks (hotfix? rollback?): "
                + "; ".join(extra[:3]))
        return rep
    rep.add("range", OK, f"{server_sha[:8]}..{local_sha[:8]}", f"{len(commits)} commit(s)")
    rep.values["COMMITS"] = "\n".join(commits)
    changed = git("diff", "--name-only", f"{server_sha}..{local_sha}").stdout.split()
    areas = sorted({c.split("/")[0] if "/" in c else c for c in changed})
    rep.add("changed", OK, f"{len(changed)} files", ", ".join(areas))
    for name, cmd in test_plan(changed):
        rep.add("test: " + name, SKIP, cmd, "run it before the confirmation gate")
    if "pyproject.toml" in changed:
        rep.add("dependencies", WARN, "pyproject.toml changed",
                "the image rebuild installs them; compute nodes reinstall on update")

    # Migrations
    h = heads_at(local_sha)
    if not h:
        rep.add("alembic heads", UNKNOWN, "origin/main", "no revisions parsed from alembic/versions")
    else:
        rep.add("alembic heads", OK if len(h) == 1 else PROBLEM, ", ".join(sorted(h)),
                "" if len(h) == 1 else "multiple heads: renumber the lower one (§D9)")
    srv_rev = (revisions(s["alembic"]) or {""}).pop()
    rep.add("server alembic", OK if srv_rev else UNKNOWN, srv_rev or s["alembic"][:80])
    migs = [m for m in git("diff", "--name-only", f"{server_sha}..{local_sha}", "--",
                           "alembic/versions/").stdout.split() if m.endswith(".py")]
    if migs:
        rep.values["MIGRATIONS"] = "1"
        rep.add("migrations", WARN, ", ".join(Path(m).name for m in migs),
                "run `alembic upgrade head` on the server right after the rebuild")
        if srv_rev:
            # --to-rev pins the target to what deploys; the checkout check above makes the
            # migration files read from this working tree the ones at origin/main.
            to_rev = sorted(h)[0] if len(h) == 1 else "head"
            chk = subprocess.run([str(REPO / "venv/bin/python"),
                                  str(REPO / ".claude/skills/deploy/mysql_migration_check.py"),
                                  "--from-rev", srv_rev, "--to-rev", to_rev], cwd=REPO,
                                 capture_output=True, text=True, timeout=600)
            tail = (chk.stdout.strip().splitlines() or [""])[-1]
            if chk.returncode == 1:
                rep.add("MySQL rehearsal", PROBLEM, f"from {srv_rev}", tail[-200:])
            elif "SKIP" in chk.stdout:
                rep.add("MySQL rehearsal", UNKNOWN, "skipped", tail[-150:] +
                        " -- could not rehearse: read the migration by eye")
            elif chk.returncode == 0:
                rep.add("MySQL rehearsal", OK, f"from {srv_rev}", tail[-120:])
            else:
                rep.add("MySQL rehearsal", UNKNOWN, f"exit {chk.returncode}",
                        (chk.stderr or chk.stdout).strip()[-200:])
    else:
        rep.add("migrations", OK, "none in range")

    # Disk
    m = re.search(r"(\d+)%", s["df"])
    if m:
        pct = int(m.group(1))
        rep.add("disk /", WARN if pct >= DISK_WARN_PCT else OK, f"{pct}%",
                "offer `docker builder prune -a -f` (ask first)" if pct >= DISK_WARN_PCT else "")
    else:
        rep.add("disk /", UNKNOWN, s["df"][:80])

    # nav.db
    local_db = hosts.check_local().values.get("LOCAL_AIRPORTS_DB")
    remote_ts = parse_ts(s["navdb"])
    local_ts = None
    if local_db:
        try:
            row = sqlite3.connect(f"file:{local_db}?mode=ro", uri=True).execute(
                "select updated_at from model_metadata where key='statistics'").fetchone()
            local_ts = parse_ts(row[0]) if row else None
        except sqlite3.Error:
            pass
    if not (remote_ts and local_ts):
        rep.add("nav.db", UNKNOWN, f"local={local_ts} server={s['navdb'][:60]}")
    elif local_ts > remote_ts:
        rep.add("nav.db", WARN, f"local {local_ts:%Y-%m-%d} newer than server {remote_ts:%Y-%m-%d}",
                "offer to copy it (skill: airport database)")
    else:
        rep.add("nav.db", OK, f"server {remote_ts:%Y-%m-%d}", "up to date")

    # Standalone cycle
    st, ev = cycle_state([ln for ln in s["cycle"].splitlines() if ln.strip()])
    rep.add("standalone cycle", st, "", ev)

    # Nodes
    for node in hosts.load_hosts(hosts.DEFAULT_HOSTS_FILE).get("nodes", []):
        nr = hosts.check_node(node)
        head = nr.values.get("NODE_HEAD")
        bad = [c for c in nr.checks if c.status in (PROBLEM, UNKNOWN)]
        if not head or bad:
            why = " ".join(bad[0].line().split()) if bad else "no HEAD"
            label = "WRONG MACHINE or broken: never update through it" if any(
                c.status == PROBLEM for c in bad) else "could not check"
            rep.add(f"node {node['name']}", WARN, label, why[-200:])
            continue
        n = git("rev-list", "--count", f"{head}..{local_sha}")
        behind = n.stdout.strip() if n.returncode == 0 else "?"
        if git("merge-base", "--is-ancestor", head, local_sha).returncode != 0:
            behind = "?"
            rep.add(f"node {node['name']}", WARN, f"{head}", "not an ancestor of origin/main "
                    "(local edits or another branch on the node)")
            continue
        rep.add(f"node {node['name']}", OK if behind == "0" else WARN,
                f"{head} on {nr.values.get('NODE_BRANCH', '?')}",
                f"{behind} commit(s) behind origin/main" + (
                    "" if behind == "0" else "; update after the deploy (`deploy.py nodes`)"))
    return rep


# --- verify --------------------------------------------------------------------------

def cmd_verify(a) -> Report:
    rep = Report(f"deploy verify {a.server_sha[:8]}..{a.local_sha[:8]}")
    try:
        v = server_info()
    except SystemExit as exc:
        rep.add("server", UNKNOWN, "", str(exc).splitlines()[0])
        return rep
    ssh, ctr = v["SERVER_SSH"], v["SERVER_CONTAINER"]
    q = shlex.quote
    changed = [p for p in git("diff", "--name-only", f"{a.server_sha}..{a.local_sha}", "--",
                              *VERIFY_PATHS).stdout.split()
               if git("cat-file", "-e", f"{a.local_sha}:{p}").returncode == 0][:5]
    hashes = " ".join(q("/app/" + p) for p in changed)
    script = f"""cd {q(v['SERVER_PROJECT_DIR'])} || exit 3
echo @@head; git rev-parse HEAD
echo @@health; docker inspect -f '{{{{.State.Health.Status}}}}' {q(ctr)}
echo @@image; docker inspect -f '{{{{.Image}}}} {{{{.Config.Image}}}}' {q(ctr)}
echo @@tag; docker image inspect -f '{{{{.Id}}}} {{{{.Created}}}}' "$(docker inspect -f '{{{{.Config.Image}}}}' {q(ctr)})"
echo @@hashes; {"docker exec " + q(ctr) + " sha256sum " + hashes if changed else "true"}
echo @@alembic; docker exec {q(ctr)} alembic current 2>/dev/null | tail -1
echo @@orm; docker exec -i {q(ctr)} python - <<'PY'
from weatherbrief.db.models import BriefingPackRow
from flyfun_common.db import SessionLocal, get_engine
get_engine()
db = SessionLocal()
try:
    db.query(BriefingPackRow).limit(1).all(); print("ORM OK")
except Exception as exc:
    print("ORM FAIL", type(exc).__name__, str(exc)[:200])
finally:
    db.close()
PY
echo @@end
"""
    r = remote(ssh, script, timeout=180)
    s = sections(r.stdout)
    if "end" not in s:
        rep.add("server", UNKNOWN, ssh, (r.stderr or r.stdout).strip()[-200:])
        return rep
    head = s["head"].strip()
    rep.add("server HEAD", OK if head == a.local_sha else PROBLEM, head[:8],
            "" if head == a.local_sha else f"expected {a.local_sha[:8]}: the pull did not land")
    rep.add("container health", OK if s["health"] == "healthy" else PROBLEM, s["health"] or "?")

    running = s["image"].split()[0] if s["image"] else ""
    tag_id, _, created = s["tag"].partition(" ")
    if running and running == tag_id:
        ev = f"image built {created[:19]}"
        st = OK
        since, built = parse_ts(a.since), parse_ts(created)
        if not (since and built):
            st, ev = UNKNOWN, f"cannot compare build time {created[:19]!r} with --since {a.since!r}"
        elif built < since:
            st, ev = PROBLEM, f"image built {created[:19]}, before the deploy started " \
                              f"({a.since}): the build never replaced it"
        rep.add("running image", st, running[7:19], ev)
    else:
        rep.add("running image", PROBLEM, running[7:19] or "?",
                f"container runs an older image than the current tag ({tag_id[7:19]}): recreate it")

    if changed:
        got = {ln.split()[1][len("/app/"):]: ln.split()[0] for ln in s["hashes"].splitlines()
               if len(ln.split()) == 2}
        stale = []
        for p in changed:
            want = hashlib.sha256(subprocess.run(["git", "show", f"{a.local_sha}:{p}"], cwd=REPO,
                                                 capture_output=True).stdout).hexdigest()
            if got.get(p) != want:
                stale.append(p)
        rep.add("code in container", PROBLEM if stale else OK,
                f"{len(changed) - len(stale)}/{len(changed)} changed files match origin",
                ("STALE: " + ", ".join(stale)) if stale else ", ".join(Path(p).name for p in changed))
    else:
        rep.add("code in container", SKIP, "", "no src/configs/alembic file changed in range")

    heads = heads_at(a.local_sha)
    cur = revisions(s["alembic"])
    rep.add("alembic", OK if cur and cur == heads else PROBLEM,
            f"server {','.join(sorted(cur)) or '?'} / head {','.join(sorted(heads)) or '?'}",
            "" if cur == heads else "migrations pending: `alembic upgrade head` (or the "
            "schema and code disagree -- an outage, §D11)")
    rep.add("ORM read", OK if s["orm"].startswith("ORM OK") else PROBLEM, "briefing_packs",
            s["orm"][:200])

    url = (v.get("SERVER_URL") or "").rstrip("/")
    if url:
        try:
            with urllib.request.urlopen(url + "/health", timeout=15) as resp:
                code = resp.status
        except Exception as exc:  # noqa: BLE001 -- any failure is a reportable result
            code = getattr(exc, "code", None) or str(exc)[:80]
        rep.add("public /health", OK if code == 200 else PROBLEM, url, str(code))
    return rep


# --- nodes ---------------------------------------------------------------------------

def cmd_nodes(a) -> Report:
    rep = Report("compute nodes" + ("" if a.execute else " (dry run; --execute to update)"))
    nodes = hosts.load_hosts(hosts.DEFAULT_HOSTS_FILE).get("nodes", [])
    for node in nodes:
        if a.node and node["name"] != a.node:
            continue
        name = node["name"]
        nr = hosts.check_node(node)
        bad = [c for c in nr.checks if c.status in (PROBLEM, UNKNOWN)]
        if bad:
            rep.add(name, UNKNOWN if bad[0].status == UNKNOWN else PROBLEM, "skipped",
                    " ".join(bad[0].line().split())[-200:] + " -- never update through it")
            continue
        repo, venv, head = nr.values["NODE_REPO"], nr.values["NODE_VENV"], nr.values["NODE_HEAD"]
        q = shlex.quote
        probe = remote(node["ssh"], f"""echo @@cycle; pgrep -fl 'weatherbrief.verify standalone' || true
echo @@alembic; cd {q(repo)} && {q(venv)}/bin/alembic current 2>/dev/null | tail -1
echo @@end
""")
        s = sections(probe.stdout)
        if "end" not in s:
            rep.add(name, UNKNOWN, "skipped", (probe.stderr or "probe failed").strip()[-150:])
            continue
        if s["cycle"].strip():
            rep.add(name, WARN, "skipped", "a standalone cycle is running; never pull under "
                    "it -- rerun when it ends: " + s["cycle"][:100])
            continue
        if not revisions(s["alembic"]):
            rep.add(name, PROBLEM, "skipped", "node DB is not alembic-stamped: follow "
                    "deploy-notes §D3 before pulling past a migration")
            continue
        if git("rev-list", "--count", f"{head}..{a.local_sha}").stdout.strip() == "0":
            rep.add(name, OK, head, "already at origin/main")
            continue
        branch = node.get("branch", "main")
        # Pull and migrate in one command so no cycle starts between them (§D3);
        # --ff-only so local edits fail loudly; deps only when pyproject changed.
        update = f"""set -e
if pgrep -f 'flyfun-forecast|weatherbrief.verify standalone' >/dev/null; then echo @@busy; exit 0; fi
cd {q(repo)}
git checkout -q {q(branch)}
before=$(git rev-parse --short HEAD)
git pull -q --ff-only
{q(venv)}/bin/alembic upgrade head >/dev/null
if ! git diff --quiet "$before" HEAD -- pyproject.toml; then
  {q(venv)}/bin/pip install -q -e '.[dev]'; echo "@@deps"; echo reinstalled
fi
echo @@after; git rev-parse --short HEAD; {q(venv)}/bin/alembic current 2>/dev/null | tail -1
echo @@end
"""
        if not a.execute:
            rep.add(name, SKIP, f"{head} -> origin/main", "would: checkout, pull --ff-only, "
                    "alembic upgrade head, pip install if pyproject changed")
            continue
        r = remote(node["ssh"], update, timeout=1200)
        s = sections(r.stdout)
        if "busy" in s:
            rep.add(name, WARN, "skipped", "a forecast cycle started meanwhile; rerun when it ends")
            continue
        if "end" not in s:
            rep.add(name, PROBLEM, f"{head} -> ?", (r.stderr or r.stdout).strip()[-250:])
            continue
        after = s["after"].splitlines()
        new_head = after[0] if after else ""
        new_rev = revisions(after[1]) if len(after) > 1 else set()
        if not new_head or not a.local_sha.startswith(new_head):
            rep.add(name, PROBLEM, f"{head} -> {new_head or '?'}", f"not at origin/main "
                    f"{a.local_sha[:8]} (node branch {branch!r}, or its origin is stale)")
            continue
        if new_rev != heads_at(a.local_sha):
            rep.add(name, PROBLEM, f"{head} -> {new_head}", f"alembic {','.join(new_rev) or '?'}"
                    f" is not origin/main's head {','.join(heads_at(a.local_sha))}")
            continue
        rep.add(name, OK, f"{head} -> {new_head}",
                f"alembic {','.join(new_rev)}; deps {'reinstalled' if 'deps' in s else 'unchanged'}")
        for label in node.get("daemons", []):
            # Long-running services from this checkout keep the old code until restarted;
            # system LaunchDaemons need sudo, which the user runs.
            rep.add(f"{name} daemon", WARN, label, "still runs the old code: "
                    f"`sudo launchctl kickstart -k system/{label}` on the node")
    if not rep.checks:
        if a.node:
            rep.add("nodes", UNKNOWN, a.node, "no such node in hosts.json")
        else:
            rep.add("nodes", SKIP, "", "none configured")
    return rep


# --- mark-prod -----------------------------------------------------------------------

def cmd_mark_prod(a) -> Report:
    rep = Report("mark prod" + ("" if a.execute else " (dry run; --execute to push)"))
    try:
        v = hosts.server_values("SERVER_SSH", "SERVER_PROJECT_DIR")
    except SystemExit as exc:
        rep.add("server", UNKNOWN, "", str(exc).splitlines()[0])
        return rep
    # Server HEAD and /health both lied before (2026-08-26, §D11): only a full verify
    # (running image, code in the container, alembic, ORM read) may move prod.
    ver = cmd_verify(a)
    if ver.exit_code != 0:
        bad = [c for c in ver.checks if c.status in (PROBLEM, UNKNOWN)]
        rep.add("verify", PROBLEM, f"{len(bad)} check(s) failed",
                "; ".join(" ".join(c.line().split()) for c in bad)[:300] + " -- prod not moved")
        return rep
    rep.add("verify", OK, "new code is live", f"{len(ver.checks)} checks")
    moves = [("prod", a.local_sha)]
    if a.server_sha != a.local_sha:
        moves.append(("prod-prev", a.server_sha))
    if not a.execute:
        for b, sha in moves:
            rep.add(b, SKIP, sha[:8], "would move and push")
        return rep
    # Explicit refspecs: no local branch to be "in use by a worktree" or silently stale.
    refspecs = [f"{sha}:refs/heads/{b}" for b, sha in moves]
    push = git("push", "--porcelain", "origin", *refspecs)
    if push.returncode != 0 and re.search(r"non-fast-forward|fetch first|rejected",
                                          push.stderr + push.stdout):
        if not a.rollback:
            rep.add("push", PROBLEM, "non-fast-forward", "expected only on a rollback deploy: "
                    "rerun with --rollback to push with --force-with-lease")
            return rep
        push = git("push", "--porcelain", "--force-with-lease", "origin", *refspecs)
    if push.returncode != 0:
        rep.add("push", PROBLEM, "", push.stderr.strip()[-200:])
        return rep
    for b, sha in moves:
        rep.add(b, OK, sha[:8], "pushed")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight")
    p = sub.add_parser("verify")
    p.add_argument("server_sha"), p.add_argument("local_sha")
    p.add_argument("--since", required=True,
                   help="UTC ISO time just before `docker compose up --build` (image must be newer)")
    p = sub.add_parser("nodes")
    p.add_argument("local_sha"), p.add_argument("--node"), p.add_argument("--execute", action="store_true")
    p = sub.add_parser("mark-prod")
    p.add_argument("server_sha"), p.add_argument("local_sha")
    p.add_argument("--since", required=True, help="as for verify; prod moves only if verify passes")
    p.add_argument("--execute", action="store_true"), p.add_argument("--rollback", action="store_true")
    a = ap.parse_args(argv)
    for sha in ("server_sha", "local_sha"):
        if getattr(a, sha, None):
            full = git("rev-parse", "--verify", "--quiet", getattr(a, sha) + "^{commit}")
            if full.returncode != 0:
                print(f"unknown  {sha}: {getattr(a, sha)} is not a known commit (git fetch?)",
                      file=sys.stderr)
                return 2
            setattr(a, sha, full.stdout.strip())
    try:
        rep = {"preflight": cmd_preflight, "verify": cmd_verify, "nodes": cmd_nodes,
               "mark-prod": cmd_mark_prod}[a.cmd](a)
    except (hosts.ConfigError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"unknown  {a.cmd}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep.as_json(), indent=2))
    else:
        print(rep.render())
        if rep.values.get("COMMITS"):
            print("\ncommits:\n  " + rep.values["COMMITS"].replace("\n", "\n  "))
        if "SERVER_SHA" in rep.values:
            print(f"\nSERVER_SHA={rep.values['SERVER_SHA']}\nLOCAL_SHA={rep.values['LOCAL_SHA']}")
    return rep.exit_code


if __name__ == "__main__":
    sys.exit(main())
