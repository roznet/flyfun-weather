#!/usr/bin/env python3
"""Run the read-only checks for every live experiment and print one short summary.

The list lives in scripts/ops/experiments.toml (what to run, where, what good
looks like, and when the owner wants to decide). This script resolves each host
through hosts.py, runs the commands over ssh, keeps only the lines the entry asks
for, and prints a compact block per experiment, so a status question costs one
command instead of an agent rebuilding ssh and SQL by hand.

    experiments.py                  run every entry
    experiments.py icon-ch1-alps    run one (or several) by id
    experiments.py --list           show the registry and decision dates, run nothing
    experiments.py --dry-run        print the resolved commands, run nothing
    experiments.py --json           machine-readable results

Every command must be read-only; the registry says so and this script never
writes anywhere. Cloud sessions have no host access: hosts report "could not
tell" there. Exit codes follow opscheck: 0 all ok, 1 a command failed, 2 a host
could not be reached. Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402

REGISTRY = Path(__file__).resolve().parent / "experiments.toml"
HOSTS = ("server", "node", "local")
REQUIRED = ("title", "host", "commands")
DEFAULT_MAX_LINES = 25
TIMEOUT_S = 300


class RegistryError(Exception):
    pass


@dataclass
class Entry:
    id: str
    title: str
    host: str
    commands: list[str]
    keep: list[str] = field(default_factory=list)
    max_lines: int = DEFAULT_MAX_LINES
    node: str | None = None
    issue: int | None = None
    decide_by: dt.date | None = None
    decision: str = ""
    good: str = ""
    thread: str = ""

    def days_left(self, today: dt.date) -> int | None:
        return None if self.decide_by is None else (self.decide_by - today).days

    def due_label(self, today: dt.date) -> str:
        days = self.days_left(today)
        if days is None:
            return "standing check"
        if days < 0:
            return f"OVERDUE by {-days} d: decide or remove"
        return "decide today" if days == 0 else f"decide in {days} d ({self.decide_by})"


def load_registry(path: Path = REGISTRY) -> list[Entry]:
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError(f"{path}: {exc}") from None
    entries = []
    for key, val in raw.items():
        if not isinstance(val, dict):
            raise RegistryError(f"{key}: expected a [table]")
        missing = [f for f in REQUIRED if f not in val]
        if missing:
            raise RegistryError(f"{key}: missing {', '.join(missing)}")
        if val["host"] not in HOSTS:
            raise RegistryError(f"{key}: host must be one of {', '.join(HOSTS)}")
        if not val["commands"] or not all(isinstance(c, str) for c in val["commands"]):
            raise RegistryError(f"{key}: commands must be a non-empty list of strings")
        unknown = set(val) - (set(Entry.__dataclass_fields__) - {"id"})  # id is the table name
        if unknown:
            raise RegistryError(f"{key}: unknown field(s) {', '.join(sorted(unknown))}")
        decide = val.get("decide_by")
        if isinstance(decide, str):
            try:
                decide = dt.date.fromisoformat(decide)
            except ValueError:
                raise RegistryError(f"{key}: decide_by must be YYYY-MM-DD") from None
        for pattern in val.get("keep", []):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RegistryError(f"{key}: bad keep regex {pattern!r}: {exc}") from None
        entries.append(Entry(id=key, **{**{k: v for k, v in val.items() if k != "decide_by"},
                                        "decide_by": decide}))
    return entries


PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def render(command: str, values: dict[str, str]) -> str:
    """Fill {NAME} placeholders from hosts.py values; a missing one is an error."""
    missing = sorted({m for m in PLACEHOLDER.findall(command) if m not in values})
    if missing:
        raise KeyError(", ".join(missing))
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], command)


def filter_output(text: str, keep: list[str], max_lines: int) -> list[str]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if keep:
        pats = [re.compile(p) for p in keep]
        lines = lines[:1] + [ln for ln in lines[1:] if any(p.search(ln) for p in pats)]
    if len(lines) > max_lines:
        cut = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... {cut} more line(s) cut (max_lines={max_lines})"]
    return lines


@dataclass
class Result:
    id: str
    title: str
    due: str
    status: str  # ok | failed | unreachable | dry-run
    lines: list[str] = field(default_factory=list)
    good: str = ""
    decision: str = ""
    standing: bool = False


def resolve_host(entry: Entry, cache: dict, runner=subprocess.run) -> tuple[str | None, dict[str, str]]:
    """(ssh target or None for local, values). Raises SystemExit if unresolvable."""
    key = (entry.host, entry.node)
    if key not in cache:
        if entry.host == "server":
            vals = hosts.server_values("SERVER_SSH", runner=runner)
            cache[key] = (vals["SERVER_SSH"], vals)
        elif entry.host == "node":
            vals = hosts.node_values("NODE_SSH", name=entry.node, runner=runner)
            cache[key] = (vals["NODE_SSH"], vals)
        else:
            cache[key] = (None, hosts.build_reports("local", None, hosts.DEFAULT_HOSTS_FILE)[0].values)
    return cache[key]


def run_entry(entry: Entry, today: dt.date, cache: dict, dry_run: bool = False,
              runner=subprocess.run) -> Result:
    res = Result(entry.id, entry.title, entry.due_label(today), "ok",
                 good=entry.good, decision=entry.decision, standing=entry.decide_by is None)
    try:
        ssh, values = resolve_host(entry, cache, runner)
    except SystemExit as exc:
        res.status, res.lines = "unreachable", [str(exc).splitlines()[0]]
        return res
    for command in entry.commands:
        try:
            cmd = render(command, values)
        except KeyError as exc:
            res.status = "failed"
            res.lines.append(f"$ {command[:80]}\n  unresolved placeholder(s): {exc}")
            continue
        argv = ["ssh", "-o", "ConnectTimeout=15", ssh, cmd] if ssh else ["bash", "-c", cmd]
        if dry_run:
            res.status = "dry-run"
            res.lines.append(" ".join(shlex.quote(a) for a in argv))
            continue
        try:
            proc = runner(argv, capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            res.status = "failed"
            res.lines.append(f"$ {command[:80]}\n  timed out after {TIMEOUT_S} s")
            continue
        res.lines.append(f"$ {command.split('&&')[-1].strip()[:100]}")
        res.lines += ["  " + ln for ln in filter_output(proc.stdout, entry.keep, entry.max_lines)]
        if ssh and proc.returncode == 255:
            # ssh's own failure (refused, timeout, host key): could not tell, not broken
            res.status = "unreachable"
            err = [ln for ln in proc.stderr.splitlines() if ln.strip()][-1:]
            res.lines += ["  ssh could not reach the host"] + ["  ! " + ln for ln in err]
            break
        if proc.returncode != 0:
            res.status = "failed"
            err = [ln for ln in proc.stderr.splitlines() if ln.strip()][-3:]
            res.lines += [f"  exit {proc.returncode}"] + ["  ! " + ln for ln in err]
    return res


def format_result(r: Result) -> str:
    out = [f"== {r.title} [{r.id}]", f"   {r.status} · {r.due}"]
    out += ["   " + ln for block in r.lines for ln in block.splitlines()]
    if r.good:
        out.append(f"   good looks like: {r.good}")
    if r.decision and not r.standing:
        out.append(f"   decision: {r.decision}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ids", nargs="*", help="experiment ids (default: all)")
    ap.add_argument("--list", action="store_true", help="show the registry, run nothing")
    ap.add_argument("--dry-run", action="store_true", help="print commands, run nothing")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--registry", type=Path, default=REGISTRY)
    args = ap.parse_args(argv)

    try:
        entries = load_registry(args.registry)
    except RegistryError as exc:
        print(f"experiments: {exc}", file=sys.stderr)
        return 1
    unknown = set(args.ids) - {e.id for e in entries}
    if unknown:
        print(f"experiments: unknown id(s) {', '.join(sorted(unknown))}; "
              f"known: {', '.join(e.id for e in entries)}", file=sys.stderr)
        return 1
    chosen = [e for e in entries if not args.ids or e.id in args.ids]
    today = dt.datetime.now(dt.timezone.utc).date()

    if args.list:
        for e in sorted(chosen, key=lambda e: (e.decide_by is None, e.decide_by or dt.date.max)):
            print(f"{e.id:<26} {e.host:<7} {e.due_label(today):<34} {e.title}")
        return 0

    cache: dict = {}
    results = [run_entry(e, today, cache, args.dry_run) for e in chosen]
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print("\n\n".join(format_result(r) for r in results))
    statuses = {r.status for r in results}
    return 1 if "failed" in statuses else 2 if "unreachable" in statuses else 0


if __name__ == "__main__":
    sys.exit(main())
