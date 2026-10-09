#!/usr/bin/env python3
"""Transport for prod-briefings-review: every ssh, container stream and scp in one place.

Each command resolves the hosts itself (scripts/ops/hosts.py, from deploy/hosts.json),
uses unique temp names on the droplet, in the container and on the Mac mini, and
removes them even when a step fails. Analysis stays in review.py / briefings.py.

    prod-check                          container start, image euro-aip, server HEAD
    br ARGS...                          run briefings.py in the container (health,
                                        digest-check, ...): copied in fresh each time
    logs SINCE UNTIL OUT [--baseline DAY BASE_OUT]
                                        app log for [SINCE, UNTIL) UTC days; baseline = the
                                        "Pipeline timing" lines of one other day
    live SINCE OUT_DIR                  live files changed since SINCE ("YYYY-MM-DD[ HH:MM]")
                                        plus the pack briefing.json files the replay needs
    sample SINCE UNTIL OUT_DIR [--n 12] [--record RECORD] [--days 7]
                                        seen-list from the record -> ranked sample -> export,
                                        unpacked to OUT_DIR/digests (+ OUT_DIR/briefings_sample.json)
    mini observed|cells|airport-radar IN OUT [--node NAME]
                                        run on_mini.py on the node against its archive; only
                                        the small result comes back (observed.json, cells/,
                                        radar.json)

Status lines go to stderr as `ok|problem|unknown  what  (evidence)`; analysis output
(br, sample) streams to stdout. Exit 0 ok, 1 a step failed, 2 a host could not be
reached (e.g. the lan_only mini from another network).
"""
from __future__ import annotations

import argparse
import json
import secrets
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import hosts  # noqa: E402

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
SCP = ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


class Failed(Exception):
    def __init__(self, what: str, evidence: str, code: int = 1):
        super().__init__(what)
        self.what, self.evidence, self.code = what, evidence, code


def status(state: str, what: str, evidence: str = "") -> None:
    print(f"{state:<8} {what}" + (f"  ({evidence})" if evidence else ""), file=sys.stderr)


def run(cmd: list[str], *, what: str, stdin=None, stdout=None, timeout=900,
        runner=subprocess.run) -> subprocess.CompletedProcess:
    r = runner(cmd, input=stdin, stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE,
               timeout=timeout)
    if r.returncode != 0:
        err = r.stderr.decode() if isinstance(r.stderr, bytes) else (r.stderr or "")
        raise Failed(what, " ".join(err.split())[-300:] or f"exit {r.returncode}",
                     2 if r.returncode == 255 else 1)
    return r


def text(r: subprocess.CompletedProcess) -> str:
    return r.stdout.decode() if isinstance(r.stdout, bytes) else (r.stdout or "")


def tag() -> str:
    return "pbr-" + secrets.token_hex(4)


# --- droplet ------------------------------------------------------------------------

def server():
    v = hosts.server_values("SERVER_SSH", "HOST_DATA_DIR", "SERVER_CONTAINER")
    return v["SERVER_SSH"], v["HOST_DATA_DIR"], v["SERVER_CONTAINER"], v.get("SERVER_HEAD", "?")


def cmd_prod_check(a) -> None:
    ssh, _, ctr, head = server()
    q = shlex.quote
    r = run([*SSH, ssh, f"docker inspect -f '{{{{.State.StartedAt}}}}' {q(ctr)} && "
             f"docker exec {q(ctr)} python -c 'import euro_aip; print(euro_aip.__version__)'"],
            what="prod-check")
    started, euro = (text(r).split() + ["?", "?"])[:2]
    print(f"server_head={head}\ncontainer_started={started}\neuro_aip={euro}")
    status("ok", "prod-check", f"HEAD {head}, container up since {started}, euro-aip {euro}")


def cleanup(ssh: str, ctr: str, paths: list[str]) -> None:
    """Remove temp files from the container, loudly if it fails."""
    q = " ".join(shlex.quote(p) for p in paths)
    r = subprocess.run([*SSH, ssh, f"docker exec {shlex.quote(ctr)} rm -f {q}"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        status("unknown", "cleanup", f"left behind in the container: {q}: "
               f"{r.stderr.strip()[-150:]}")


def put(ssh: str, ctr: str, data: bytes, name: str) -> None:
    """Stream bytes into a container file. `brice` is in the docker group, so this
    needs no host temp file, and the file is owned by the app user (removable)."""
    run([*SSH, ssh, f"docker exec -i {shlex.quote(ctr)} sh -c {shlex.quote('cat > ' + name)}"],
        what=f"put {name}", stdin=data)


def get(ssh: str, ctr: str, name: str, local: Path) -> None:
    with local.open("wb") as fh:
        run([*SSH, ssh, f"docker exec {shlex.quote(ctr)} cat {shlex.quote(name)}"],
            what=f"get {name}", stdout=fh)


def with_container_file(ssh: str, ctr: str, local: Path, body) -> None:
    """Stream `local` into the container under a unique /tmp name, call
    body(path), then remove it whatever happened."""
    name = f"/tmp/{tag()}-{local.name}"
    try:
        put(ssh, ctr, local.read_bytes(), name)
        body(name)
    finally:
        cleanup(ssh, ctr, [name])


def cmd_br(a) -> None:
    ssh, _, ctr, _ = server()

    def body(path):
        cmd = " ".join(shlex.quote(x) for x in ["docker", "exec", ctr, "python", path, *a.args])
        r = subprocess.run([*SSH, ssh, cmd], timeout=1800)
        if r.returncode != 0:
            raise Failed("briefings.py " + " ".join(a.args[:1]), f"exit {r.returncode}")
    with_container_file(ssh, ctr, HERE / "briefings.py", body)
    status("ok", "briefings.py " + " ".join(a.args[:3]))


def cmd_logs(a) -> None:
    ssh, *_ = server()
    q = shlex.quote
    out = Path(a.out)
    with out.open("wb") as fh:
        run([*SSH, ssh, f"journalctl CONTAINER_NAME=weatherbrief --since {q(a.since + ' 00:00')} "
             f"--until {q(a.until + ' 00:00')} --utc -o cat"], what="journalctl", stdout=fh)
    n = sum(1 for _ in out.open("rb"))
    if n == 0:
        raise Failed("logs", f"no lines for {a.since}..{a.until} -- wrong window?", 2)
    status("ok", f"logs -> {out}", f"{n} lines")
    if a.baseline:
        day, base_out = a.baseline
        nxt = _next_day(day)
        with Path(base_out).open("wb") as fh:
            run([*SSH, ssh, f"journalctl CONTAINER_NAME=weatherbrief --since {q(day + ' 00:00')} "
                 f"--until {q(nxt + ' 00:00')} --utc -o cat | grep 'Pipeline timing' || true"],
                what="journalctl baseline", stdout=fh)
        m = sum(1 for _ in Path(base_out).open("rb"))
        status("ok" if m else "unknown", f"baseline -> {base_out}",
               f"{m} Pipeline timing lines for {day}")


def _next_day(day: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def cmd_live(a) -> None:
    ssh, data, *_ = server()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    q = shlex.quote
    find = (f"cd {q(data + '/packs')} && find . -maxdepth 3 \\( -name live_history.jsonl "
            f"-o -name live_meta.json -o -name live.json \\) -newermt {q(a.since)}")
    names = text(run([*SSH, ssh, find], what="find live files")).split()
    if not names:
        status("ok", "live", f"no live files changed since {a.since} -- nothing to review")
        return
    _pull_tar(ssh, data + "/packs", names, out, "live files")
    r = run([sys.executable, str(HERE / "review.py"), "briefing-list", str(out)],
            what="review.py briefing-list")
    briefings = text(r).split()
    if briefings:
        _pull_tar(ssh, data + "/packs", briefings, out, "pack briefing.json")
    flights = len(list(out.glob("**/live_history.jsonl")))
    status("ok", f"live -> {out}", f"{flights} flights, {len(names)} live files, "
           f"{len(briefings)} briefing.json")


def _pull_tar(ssh: str, base: str, names: list[str], out: Path, what: str) -> None:
    listing = "\n".join(names).encode()
    with tempfile.TemporaryFile() as buf:
        run([*SSH, ssh, f"cd {shlex.quote(base)} && tar czf - -T -"], what=f"tar {what}",
            stdin=listing, stdout=buf)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:gz") as t:
            t.extractall(out, filter="data")


def cmd_sample(a) -> None:
    ssh, _, ctr, _ = server()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    seen_file = out / "seen.json"
    seen_args = ["seen", a.record, "--days", str(a.days)] if a.record else None
    if seen_args and Path(a.record).exists():
        r = run([sys.executable, str(HERE / "briefings.py"), *seen_args], what="briefings.py seen")
        seen_file.write_text(text(r))
    else:
        seen_file.write_text("[]")
        status("ok", "seen", "no record yet -- nothing to down-rank")
    q = shlex.quote
    t = tag()
    sample_c, tgz_c = f"/tmp/{t}-sample.json", f"/tmp/{t}-export.tgz"

    def body(br_path):
        def inner(seen_path):
            cmd = " ".join(q(x) for x in ["docker", "exec", ctr, "python", br_path, "sample",
                                          a.since, a.until, "--n", str(a.n), "--seen", seen_path,
                                          "--out", sample_c])
            if subprocess.run([*SSH, ssh, cmd], timeout=1800).returncode != 0:
                raise Failed("briefings.py sample", "see output above")
            run([*SSH, ssh, f"docker exec {q(ctr)} python {q(br_path)} export {q(sample_c)} "
                 f"{q(tgz_c)} >&2"], what="export")
            get(ssh, ctr, tgz_c, out / Path(tgz_c).name)
            get(ssh, ctr, sample_c, out / Path(sample_c).name)
        with_container_file(ssh, ctr, seen_file, inner)
    try:
        with_container_file(ssh, ctr, HERE / "briefings.py", body)
    finally:
        cleanup(ssh, ctr, [sample_c, tgz_c])
    tgz, sample = out / Path(tgz_c).name, out / Path(sample_c).name
    digests = out / "digests"
    digests.mkdir(exist_ok=True)
    with tarfile.open(tgz) as t2:
        t2.extractall(digests, filter="data")
    sample.replace(out / "briefings_sample.json")
    tgz.unlink()
    picks = json.loads((out / "briefings_sample.json").read_text())
    core = sum(1 for p in picks if p.get("tier") == "core")
    status("ok", f"sample -> {digests}", f"{len(picks)} packs ({core} core); "
           f"picks in {out / 'briefings_sample.json'}")


# --- Mac mini ------------------------------------------------------------------------

MINI_JOBS = {
    # job: (result name on the node, how it comes back)
    "observed": ("observed.json", "gzip"),
    "airport-radar": ("radar.json", "plain"),
    "cells": ("cells", "tar"),
}


def cmd_mini(a, runner=subprocess.run) -> None:
    v = hosts.node_values("NODE_SSH", "NODE_VENV", name=a.node)
    node, py = v["NODE_SSH"], v["NODE_VENV"] + "/bin/python"
    result, how = MINI_JOBS[a.job]
    q = shlex.quote
    work = text(run([*SSH, node, f"mktemp -d /tmp/{tag()}.XXXX"], what="mktemp on node",
                    runner=runner)).strip()
    if not work.startswith("/tmp/"):
        raise Failed("mktemp on node", f"unexpected path {work!r}")
    try:
        inp = Path(a.input)
        run([*SCP, str(inp), str(HERE / "on_mini.py"), f"{node}:{work}/"],
            what="scp to node", runner=runner)
        job = (f"cd {q(work)} && {q(py)} on_mini.py {a.job} {q(inp.name)} {result}")
        if how == "gzip":
            job += f" && gzip -f {result}"
        elif how == "tar":
            job += f" && tar czf {result}.tgz {result}"
        run([*SSH, node, job], what=f"on_mini.py {a.job}", timeout=3600, runner=runner)
        out = Path(a.output)
        if how == "plain":
            run([*SCP, f"{node}:{work}/{result}", str(out)], what="scp result", runner=runner)
        elif how == "gzip":
            import gzip
            gz = out.with_name(out.name + ".gz")
            run([*SCP, f"{node}:{work}/{result}.gz", str(gz)], what="scp result", runner=runner)
            out.write_bytes(gzip.decompress(gz.read_bytes()))
            gz.unlink()
        else:
            out.mkdir(parents=True, exist_ok=True)
            tgz = out.parent / f"{out.name}.tgz"
            run([*SCP, f"{node}:{work}/{result}.tgz", str(tgz)], what="scp result", runner=runner)
            with tarfile.open(tgz) as t:  # the archive holds "cells/..."; land it under OUT
                for m in t.getmembers():
                    m.name = m.name.split("/", 1)[1] if "/" in m.name else ""
                t.extractall(out, members=[m for m in t.getmembers() if m.name],
                             filter="data")
            tgz.unlink()
    finally:
        runner([*SSH, node, f"rm -rf {q(work)}"], capture_output=True, timeout=60)
    status("ok", f"mini {a.job} -> {a.output}", f"on {v['NODE_NAME']}, {work} removed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prod-check")
    p = sub.add_parser("br")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p = sub.add_parser("logs")
    p.add_argument("since"), p.add_argument("until"), p.add_argument("out")
    p.add_argument("--baseline", nargs=2, metavar=("DAY", "OUT"))
    p = sub.add_parser("live")
    p.add_argument("since"), p.add_argument("out_dir")
    p = sub.add_parser("sample")
    p.add_argument("since"), p.add_argument("until"), p.add_argument("out_dir")
    p.add_argument("--n", type=int, default=12)
    p.add_argument("--record")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("mini")
    p.add_argument("job", choices=sorted(MINI_JOBS))
    p.add_argument("input"), p.add_argument("output")
    p.add_argument("--node")
    a = ap.parse_args(argv)
    fn = {"prod-check": cmd_prod_check, "br": cmd_br, "logs": cmd_logs, "live": cmd_live,
          "sample": cmd_sample, "mini": cmd_mini}[a.cmd]
    try:
        fn(a)
    except Failed as exc:
        status("problem" if exc.code == 1 else "unknown", exc.what, exc.evidence)
        return exc.code
    except subprocess.TimeoutExpired as exc:
        status("problem", "timeout", " ".join(map(str, exc.cmd))[:120])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
