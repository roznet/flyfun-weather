"""scripts/ops/maintenance.py: the pure parts (no ssh, no network)."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone

import pytest

from scripts.ops import maintenance
from scripts.ops.opscheck import OK, PROBLEM, UNKNOWN, Report

NOW = datetime(2026, 11, 7, 13, 5, 42, tzinfo=timezone.utc)


def test_until_hhmm_is_next_occurrence_in_utc():
    assert maintenance.parse_until("14:30", NOW) == datetime(2026, 11, 7, 14, 30, tzinfo=timezone.utc)
    assert maintenance.parse_until("14:30Z", NOW).hour == 14
    # Already past today -> tomorrow.
    assert maintenance.parse_until("09:00", NOW) == datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)


def test_until_relative_and_iso():
    assert maintenance.parse_until("+45m", NOW) == datetime(2026, 11, 7, 13, 50, tzinfo=timezone.utc)
    assert maintenance.parse_until("+2h", NOW) == datetime(2026, 11, 7, 15, 5, tzinfo=timezone.utc)
    assert maintenance.parse_until("2026-11-07T15:30:00+01:00", NOW) == datetime(
        2026, 11, 7, 14, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", ["25:00", "14:61", "soon", "2026-11-07T15:30:00"])
def test_until_rejects_garbage_and_naive_iso(bad):
    with pytest.raises(ValueError):
        maintenance.parse_until(bad, NOW)


def test_maintenance_status_carries_detail_and_until():
    until = maintenance.parse_until("14:30", NOW)
    s = maintenance.build_status("maintenance", NOW, None, until)
    assert s["service_status"] == "maintenance"
    assert s["detail"] == ("FlyFun Weather is down for planned maintenance. "
                           "Expected back around 14:30Z.")
    assert s["until"] == "2026-11-07T14:30:00Z"
    # Tomorrow's estimate names the day.
    s = maintenance.build_status("maintenance", NOW, "Upgrading the database.",
                                 maintenance.parse_until("09:00", NOW))
    assert s["detail"] == "Upgrading the database. Expected back around Sun 8 Nov 09:00Z."


def test_unavailable_status_is_generic():
    s = maintenance.build_status("unavailable", NOW)
    assert s == {"service_status": "unavailable", "detail": maintenance.DEFAULT_UNAVAILABLE,
                 "message": maintenance.DEFAULT_UNAVAILABLE,
                 "until": None, "updated_at": "2026-11-07T13:05:42Z"}


def test_message_with_estimate_wording_is_kept_whole_on_the_page():
    msg = "Expected back around lunch, says the DB vendor."
    s = maintenance.build_status("maintenance", NOW, msg, maintenance.parse_until("14:30", NOW))
    assert s["message"] == msg
    page = maintenance.render_html(s, NOW, maintenance.TEMPLATE.read_text())
    assert f"<p>{msg}</p>" in page


def test_html_escapes_message_and_splits_estimate():
    template = maintenance.TEMPLATE.read_text()
    s = maintenance.build_status("maintenance", NOW, "DB <upgrade> & checks.",
                                 maintenance.parse_until("14:30", NOW))
    page = maintenance.render_html(s, NOW, template)
    assert "DB &lt;upgrade&gt; &amp; checks." in page
    assert 'data-until="2026-11-07T14:30:00Z">14:30Z</span>' in page
    assert "Down for maintenance" in page
    assert "$" not in re.sub(r"<script>.*</script>", "", page, flags=re.S)
    page = maintenance.render_html(maintenance.build_status("unavailable", NOW), NOW, template)
    assert "Temporarily unavailable" in page and 'class="until"' not in page


def _decoded(script: str) -> dict[str, str]:
    out = {}
    for b64, name in re.findall(r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d > \.(\S+)\.tmp", script):
        out[name] = base64.b64decode(b64).decode()
    return out


def test_remote_script_orders_flag_around_content():
    files = {"status.json": '{"a": 1}\n', "status.html": "<p>it's</p>\n"}
    on = maintenance.remote_script(files, flag_on=True)
    assert on.index("status.html.tmp") < on.index("touch MAINTENANCE_ON")
    assert "rm -f MAINTENANCE_ON" not in on
    off = maintenance.remote_script(files, flag_on=False)
    assert off.index("rm -f MAINTENANCE_ON") < off.index("status.json.tmp")
    assert "touch" not in off
    assert _decoded(on) == files


def _fetcher(code, body):
    return lambda url, accept="application/json": (code, body)


def test_verify_maintenance_needs_503_with_status():
    r = Report("t")
    maintenance.verify(r, "https://x", True, _fetcher(503, json.dumps({"service_status": "maintenance"})))
    assert r.checks[-1].status == OK
    r = Report("t")
    # Old Caddy config: the app still answers -> the switch did nothing.
    maintenance.verify(r, "https://x", True, _fetcher(200, '{"status":"ok"}'))
    assert r.checks[-1].status == PROBLEM
    r = Report("t")
    maintenance.verify(r, "https://x", True, _fetcher(None, "timed out"))
    assert r.checks[-1].status == UNKNOWN


def test_verify_keep_app_up_says_the_page_is_unchecked():
    r = Report("t")
    maintenance.verify(r, "https://x", False, _fetcher(200, '{"status":"ok"}'), keep_app_up=True)
    assert [c.status for c in r.checks] == [OK, "skip"]


def test_verify_off_needs_the_app():
    r = Report("t")
    maintenance.verify(r, "https://x", False, _fetcher(200, '{"status":"ok"}'))
    assert r.checks[-1].status == OK
    r = Report("t")
    maintenance.verify(r, "https://x", False, _fetcher(503, "{}"))
    assert r.checks[-1].status == PROBLEM


def test_dry_run_touches_nothing(monkeypatch, capsys):
    monkeypatch.setattr(maintenance.hosts, "server_values",
                        lambda *a, **k: pytest.fail("dry run must not resolve hosts"))
    assert maintenance.main(["on", "--until", "+30m", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert '"service_status": "maintenance"' in out and "touch MAINTENANCE_ON" in out
    assert maintenance.main(["off", "--dry-run"]) == 0
    assert "rm -f MAINTENANCE_ON" in capsys.readouterr().out
