#!/usr/bin/env python3
"""Maintenance switch and status page for weather.flyfun.aero.

Caddy serves a status page whenever the app can't answer (backend down: 502 /
504) or the maintenance switch is on (503): status.json to API clients and the
apps, status.html to browsers, both from ~/www/weather-status on the droplet.
See handle_errors in deploy/weather.flyfun.aero.caddy. This script writes those
two files and the switch:

    maintenance.py on --until 14:30            switch on, "back around 14:30Z"
    maintenance.py on --until +45m --message "Upgrading the database."
    maintenance.py on --until 14:30 --keep-app-up
                                               message only; the app keeps serving
                                               and the page shows if it goes down
    maintenance.py off                         switch off, back to the generic
                                               "temporarily unavailable" text
    maintenance.py status                      what is on the droplet now

`off` is also the first-time install: it creates the directory and the default
files. Every write is followed by a check against the public URL, so a switch
that did not take effect is reported as a problem, not as ok.

--until takes HH:MM (UTC, next occurrence), +NNm / +Nh, or an ISO timestamp.
--dry-run prints what would be written and touches nothing.

status.json contract (read by the iOS, Android and web clients):
    service_status   "maintenance" | "unavailable"
    detail           the sentence to show the user (FastAPI's error key, so app
                     builds that predate the banner still show it): message
                     plus the estimate
    message          the message alone, without the estimate
    until            ISO-8601 UTC estimate, or null
    updated_at       when this file was written

Exit codes (see opscheck.py): 0 ok, 1 a problem, 2 could not tell.
Stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402
from opscheck import OK, PROBLEM, SKIP, UNKNOWN, WARN, Report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "deploy" / "weather-status" / "status.html"
REMOTE_DIR = "~/www/weather-status"  # Caddy root: /home/brice/www/weather-status
FLAG = "MAINTENANCE_ON"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

DEFAULT_MAINTENANCE = "FlyFun Weather is down for planned maintenance."
DEFAULT_UNAVAILABLE = ("FlyFun Weather is temporarily unavailable. "
                       "Please try again in a few minutes.")


# --- pure: what to write -------------------------------------------------------

def parse_until(text: str, now: datetime) -> datetime:
    """HH:MM (UTC, next occurrence), +NNm / +Nh, or ISO-8601 -> aware UTC datetime."""
    text = text.strip()
    if m := re.fullmatch(r"(\d{1,2}):(\d{2})Z?", text, re.IGNORECASE):
        hour, minute = int(m[1]), int(m[2])
        if hour > 23 or minute > 59:
            raise ValueError(f"not a time: {text!r}")
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return at if at > now else at + timedelta(days=1)
    if m := re.fullmatch(r"\+(\d+)\s*([mh])", text, re.IGNORECASE):
        unit = timedelta(minutes=1) if m[2].lower() == "m" else timedelta(hours=1)
        return (now + int(m[1]) * unit).replace(second=0, microsecond=0)
    try:
        at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"--until wants HH:MM, +45m, +2h or ISO-8601, got {text!r}") from None
    if at.tzinfo is None:
        raise ValueError(f"ISO --until needs an offset (e.g. 14:30Z), got {text!r}")
    if at <= now:
        raise ValueError(f"--until {text!r} is in the past")
    return at.astimezone(timezone.utc)


def format_until(until: datetime, now: datetime) -> str:
    """14:30Z today, 'Sat 8 Nov 14:30Z' otherwise."""
    if until.date() == now.date():
        return until.strftime("%H:%MZ")
    return f"{until:%a} {until.day} {until:%b %H:%MZ}"


def build_status(mode: str, now: datetime, message: str | None = None,
                 until: datetime | None = None) -> dict:
    """The status.json payload. mode is 'maintenance' or 'unavailable'."""
    if mode == "maintenance":
        message = (message or DEFAULT_MAINTENANCE).strip()
        detail = message
        if until:
            detail += f" Expected back around {format_until(until, now)}."
    else:
        message = detail = DEFAULT_UNAVAILABLE
    return {
        "service_status": mode,
        "detail": detail,
        "message": message,
        "until": until.strftime("%Y-%m-%dT%H:%M:%SZ") if until else None,
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def render_html(status: dict, now: datetime, template: str) -> str:
    maintenance = status["service_status"] == "maintenance"
    title = "Down for maintenance" if maintenance else "Temporarily unavailable"
    # The page shows the message and the estimate separately.
    message = status["message"]
    until_block = ""
    if status["until"]:
        until = datetime.fromisoformat(status["until"].replace("Z", "+00:00"))
        until_block = ('<p>Expected back around <span class="until" data-until="'
                       f'{html.escape(status["until"])}">'
                       f'{html.escape(format_until(until, now))}</span>.</p>')
    return Template(template).substitute(
        title=html.escape(title), message=html.escape(message), until_block=until_block)


def remote_script(files: dict[str, str], flag_on: bool) -> str:
    """Shell run on the droplet: write each file atomically, then set the flag.

    Switching on writes the content before the flag, so the first 503 already
    carries the new message. Switching off drops the flag first, so the app is
    reachable again before the text goes back to the generic one.
    """
    lines = ["set -e", f"mkdir -p {REMOTE_DIR}", f"cd {REMOTE_DIR}"]
    if not flag_on:
        lines.append(f"rm -f {FLAG}")
    for name, content in files.items():
        b64 = base64.b64encode(content.encode()).decode()
        lines.append(f"printf %s {shlex.quote(b64)} | base64 -d > .{name}.tmp && mv .{name}.tmp {name}")
    if flag_on:
        lines.append(f"touch {FLAG}")
    lines.append(f"ls -la {REMOTE_DIR}")
    return "\n".join(lines) + "\n"


# --- impure: ssh and http ------------------------------------------------------

def ssh_run(ssh: str, script: str, runner=subprocess.run) -> subprocess.CompletedProcess:
    return runner([*SSH, ssh, "bash -s"], input=script, capture_output=True, text=True,
                  timeout=60)


def fetch(url: str, accept: str = "application/json") -> tuple[int | None, str]:
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": "flyfun-ops-maintenance"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, str(exc)


def verify(report: Report, base_url: str, expect_maintenance: bool, fetcher=fetch,
           keep_app_up: bool = False):
    """Check the public API answers the way the switch says it should."""
    code, body = fetcher(base_url.rstrip("/") + "/health")
    if code is None:
        report.add("public check", UNKNOWN, base_url, f"no answer: {body}")
        return
    if expect_maintenance:
        try:
            status = json.loads(body).get("service_status")
        except (ValueError, AttributeError):
            status = None
        if code == 503 and status == "maintenance":
            report.add("public check", OK, "503 maintenance", "/health")
        else:
            report.add("public check", PROBLEM, f"{code}",
                       f"/health should be 503 maintenance, got: {body[:120]!r} "
                       "(is the Caddy handle_errors block deployed?)")
    elif code == 200:
        report.add("public check", OK, "200", "/health answers")
        if keep_app_up:
            report.add("status page", SKIP, "not checkable while the app is up",
                       "it shows only when the app fails; `on` without --keep-app-up "
                       "is what exercises the Caddy block")
            report.add("reminder", WARN, "run `maintenance.py off` when done",
                       "until then any later outage shows this maintenance message")
    else:
        report.add("public check", PROBLEM, f"{code}", f"/health: {body[:120]!r}")


def apply(mode: str, args, now: datetime) -> int:
    until = parse_until(args.until, now) if getattr(args, "until", None) else None
    status = build_status(mode, now, getattr(args, "message", None), until)
    flag_on = mode == "maintenance" and not args.keep_app_up
    files = {"status.json": json.dumps(status, indent=2) + "\n",
             "status.html": render_html(status, now, TEMPLATE.read_text())}
    script = remote_script(files, flag_on)

    report = Report(f"maintenance {args.cmd}")
    report.add("message", OK, status["detail"])
    report.add("switch", OK, "ON — every request gets the page" if flag_on
               else "off — page only when the app can't answer")
    if args.dry_run:
        print(report.render())
        print("\n--- status.json\n" + files["status.json"])
        print("--- remote script (contents base64)\n"
              + re.sub(r"printf %s '?[A-Za-z0-9+/=]+'?", "printf %s <b64>", script))
        return 0

    values = hosts.server_values("SERVER_SSH", "SERVER_URL")
    res = ssh_run(values["SERVER_SSH"], script)
    if res.returncode != 0:
        report.add("upload", PROBLEM, values["SERVER_SSH"], res.stderr.strip()[:200])
        print(report.render())
        return report.exit_code
    report.add("upload", OK, f"{values['SERVER_SSH']}:{REMOTE_DIR}")
    verify(report, values["SERVER_URL"], expect_maintenance=flag_on,
           keep_app_up=mode == "maintenance" and not flag_on)
    print(report.render())
    return report.exit_code


def show_status() -> int:
    values = hosts.server_values("SERVER_SSH", "SERVER_URL")
    res = ssh_run(values["SERVER_SSH"],
                  f"cd {REMOTE_DIR} 2>/dev/null || {{ echo MISSING; exit 0; }}\n"
                  f"test -e {FLAG} && echo FLAG=on || echo FLAG=off\ncat status.json\n")
    report = Report("maintenance status")
    if res.returncode != 0:
        report.add("droplet", UNKNOWN, values["SERVER_SSH"], res.stderr.strip()[:200])
    elif res.stdout.startswith("MISSING"):
        report.add("status dir", PROBLEM, REMOTE_DIR,
                   "not installed: run `maintenance.py off` once")
    else:
        flag, _, body = res.stdout.partition("\n")
        report.add("switch", OK, flag.split("=")[1])
        try:
            status = json.loads(body)
            report.add("message", OK, status.get("detail", ""),
                       f"{status.get('service_status')}, written {status.get('updated_at')}")
        except ValueError:
            report.add("status.json", PROBLEM, "unreadable", body[:120])
        verify(report, values["SERVER_URL"], expect_maintenance=flag == "FLAG=on")
    print(report.render())
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    on = sub.add_parser("on", help="switch maintenance on")
    on.add_argument("--until", help="HH:MM (UTC), +45m, +2h or ISO-8601")
    on.add_argument("--message", help=f"first sentence (default: {DEFAULT_MAINTENANCE!r})")
    on.add_argument("--keep-app-up", action="store_true",
                    help="write the message but leave the app serving")
    on.add_argument("--dry-run", action="store_true")
    off = sub.add_parser("off", help="switch off; also the first-time install")
    off.add_argument("--dry-run", action="store_true")
    off.set_defaults(keep_app_up=False)
    sub.add_parser("status", help="what is on the droplet now")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    try:
        if args.cmd == "on":
            return apply("maintenance", args, now)
        if args.cmd == "off":
            return apply("unavailable", args, now)
        return show_status()
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
