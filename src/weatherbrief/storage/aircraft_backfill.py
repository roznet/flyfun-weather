"""One-time backfill behind migration 103 (#777): every user gets a default
aircraft, and cruise speed moves off the profile onto the aircraft.

Kept out of the migration file so the read-only dry-run script
(``scripts/aircraft_backfill_dryrun.py``) runs the *same* plan the migration
applies. Raw SQL over a plain connection (no ORM), so it neither depends on
the current ORM schema nor writes anything until :func:`apply_plan`.

Steps, per user:

1. **Default aircraft.** Has a default → keep it (and un-flag any extra
   defaults, keeping the oldest). Has aircraft but none default → promote the
   oldest. Has none → a placeholder from :func:`placeholder_fields`.
2. **Gap-fill speed.** An aircraft with no usable cruise speed takes the cruise
   IAS its flights resolved to before (they fell back to their profile's
   ``speed_kt``), the most common one when they disagree; failing that the
   default profile's ``speed_kt``, then any profile's. This keeps those
   flights' results unchanged once the profile fallback is gone.
3. **Strip** ``speed_kt`` from every profile's ``settings_json``.

:func:`cruise_ias_report` then compares every flight's cruise IAS before and
after (the "identical results" proof). A flight can differ only when one
aircraft served flights under profiles with different speeds, or a flight with
no aircraft used a profile speed that disagrees with the user's real default
aircraft — both are reported, not "fixed", since fixing them would mean
inventing aircraft.

"Before" mirrors the pre-#777 refresh path (``_prepare_refresh``): aircraft
speed, else the flight's profile ``speed_kt`` where a NULL ``profile_id`` reads
the user's default profile and a missing / foreign profile reads nothing.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import sqlalchemy as sa

from weatherbrief.storage.aircraft import placeholder_fields


def _usable(speed) -> float | None:
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@dataclass
class _Aircraft:
    id: int | str  # str key ("new:<user>") for a placeholder not inserted yet
    user_id: str
    speed: float | None
    is_default: bool
    created_at: object
    new_fields: dict | None = None  # set for a placeholder to insert


@dataclass
class BackfillPlan:
    placeholders: dict[str, dict] = field(default_factory=dict)  # user_id -> columns
    promote: list[int] = field(default_factory=list)  # aircraft ids made default
    undefault: list[int] = field(default_factory=list)  # extra defaults cleared
    speed_fill: dict[int | str, int] = field(default_factory=dict)  # aircraft key -> kt
    profile_strip: dict[int, str] = field(default_factory=dict)  # profile id -> new json
    before_ias: dict[str, float | None] = field(default_factory=dict)  # flight -> kt
    after_ias: dict[str, float | None] = field(default_factory=dict)

    @property
    def mismatches(self) -> list[tuple[str, float | None, float | None]]:
        return [
            (fid, self.before_ias[fid], self.after_ias.get(fid))
            for fid in sorted(self.before_ias)
            if self.before_ias[fid] != self.after_ias.get(fid)
        ]

    def summary(self) -> str:
        return (
            f"{len(self.placeholders)} placeholder aircraft created, "
            f"{len(self.promote)} promoted to default, "
            f"{len(self.undefault)} extra defaults cleared, "
            f"{len(self.speed_fill)} speeds gap-filled, "
            f"{len(self.profile_strip)} profiles stripped of speed_kt; "
            f"cruise IAS unchanged for {len(self.before_ias) - len(self.mismatches)}"
            f"/{len(self.before_ias)} flights"
        )


def _load_settings(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def plan_backfill(conn) -> BackfillPlan:
    """Compute the backfill without writing anything."""
    users = [r[0] for r in conn.execute(sa.text("SELECT id FROM users")).fetchall()]
    aircraft: dict[int | str, _Aircraft] = {}
    for aid, uid, speed, is_default, created_at in conn.execute(sa.text(
        "SELECT id, user_id, cruise_speed_kt, is_default, created_at FROM user_aircraft"
    )).fetchall():
        aircraft[aid] = _Aircraft(aid, uid, _usable(speed), bool(is_default), created_at)

    profiles: dict[int, tuple[str, bool, dict, str | None]] = {}
    for pid, uid, is_default, raw in conn.execute(sa.text(
        "SELECT id, user_id, is_default, settings_json FROM flight_profiles"
    )).fetchall():
        profiles[pid] = (uid, bool(is_default), _load_settings(raw), raw)

    flights = conn.execute(sa.text(
        "SELECT id, user_id, profile_id, aircraft_id FROM flights"
    )).fetchall()

    default_profile: dict[str, dict] = {}
    for pid in sorted(profiles):
        uid, is_default, settings, _ = profiles[pid]
        if is_default and uid not in default_profile:
            default_profile[uid] = settings

    plan = BackfillPlan()

    # --- before: the pre-#777 resolution --------------------------------
    def profile_speed(uid: str, pid: int | None) -> float | None:
        if pid is None:
            return _usable(default_profile.get(uid, {}).get("speed_kt"))
        prof = profiles.get(pid)
        if prof is None or prof[0] != uid:
            return None
        return _usable(prof[2].get("speed_kt"))

    for fid, uid, pid, aid in flights:
        ac = aircraft.get(aid) if aid else None
        plan.before_ias[fid] = (ac.speed if ac else None) or profile_speed(uid, pid)

    # --- 1. a default aircraft per user ---------------------------------
    by_user: dict[str, list[_Aircraft]] = {}
    for ac in aircraft.values():
        by_user.setdefault(ac.user_id, []).append(ac)
    default_of: dict[str, _Aircraft] = {}
    for uid in users:
        # Same order as the runtime (ensure/get_default_aircraft): created_at
        # as the DB returns it, then id for ties.
        owned = sorted(by_user.get(uid, []), key=lambda a: (a.created_at is None, a.created_at or 0, a.id))
        defaults = [a for a in owned if a.is_default]
        if defaults:
            default_of[uid] = defaults[0]
            plan.undefault.extend(a.id for a in defaults[1:])
        elif owned:
            default_of[uid] = owned[0]
            plan.promote.append(owned[0].id)
        else:
            fields = placeholder_fields(default_profile.get(uid))
            key = f"new:{uid}"
            ac = _Aircraft(key, uid, _usable(fields["cruise_speed_kt"]), True, None, fields)
            aircraft[key] = ac
            default_of[uid] = ac
            plan.placeholders[uid] = fields

    def resolved(uid: str, aid: int | None) -> _Aircraft | None:
        ac = aircraft.get(aid) if aid else None
        return ac if ac is not None else default_of.get(uid)

    # --- 2. gap-fill speeds from what their flights used ----------------
    served: dict[int | str, list[float]] = {}
    for fid, uid, _pid, aid in flights:
        ac = resolved(uid, aid)
        if ac is not None and plan.before_ias[fid] is not None:
            served.setdefault(ac.id, []).append(plan.before_ias[fid])
    for key, ac in aircraft.items():
        if ac.speed is not None:
            continue
        speed = None
        counts = Counter(served.get(key, []))
        if counts:
            speed = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        if speed is None:
            speed = _usable(default_profile.get(ac.user_id, {}).get("speed_kt"))
        if speed is None:
            for pid in sorted(profiles):
                uid, _, settings, _ = profiles[pid]
                if uid == ac.user_id and _usable(settings.get("speed_kt")) is not None:
                    speed = _usable(settings["speed_kt"])
                    break
        if speed is not None:
            ac.speed = float(int(speed))
            if ac.new_fields is not None:
                ac.new_fields["cruise_speed_kt"] = int(speed)
            else:
                plan.speed_fill[key] = int(speed)

    # --- 3. strip speed_kt from profiles --------------------------------
    for pid, (_uid, _d, settings, _raw) in profiles.items():
        if "speed_kt" in settings:
            stripped = {k: v for k, v in settings.items() if k != "speed_kt"}
            plan.profile_strip[pid] = json.dumps(stripped)

    # --- after -----------------------------------------------------------
    for fid, uid, _pid, aid in flights:
        ac = resolved(uid, aid)
        plan.after_ias[fid] = ac.speed if ac else None
    return plan


def apply_plan(conn, plan: BackfillPlan) -> None:
    """Write *plan*. Needs ``user_aircraft.is_placeholder`` to exist."""
    for aid in plan.undefault:
        conn.execute(sa.text("UPDATE user_aircraft SET is_default = :f WHERE id = :id"),
                     {"f": False, "id": aid})
    for aid in plan.promote:
        conn.execute(sa.text("UPDATE user_aircraft SET is_default = :t WHERE id = :id"),
                     {"t": True, "id": aid})
    for aid, speed in plan.speed_fill.items():
        conn.execute(sa.text("UPDATE user_aircraft SET cruise_speed_kt = :s WHERE id = :id"),
                     {"s": speed, "id": aid})
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for uid, cols in plan.placeholders.items():
        conn.execute(
            sa.text(
                "INSERT INTO user_aircraft (user_id, icao_type, tail_number, nickname, "
                "is_ifr, is_fiki, cruise_speed_kt, ceiling_ft, is_default, "
                "is_placeholder, created_at) VALUES (:user_id, :icao_type, "
                ":tail_number, :nickname, :is_ifr, :is_fiki, :cruise_speed_kt, "
                ":ceiling_ft, :is_default, :is_placeholder, :created_at)"
            ),
            {**cols, "user_id": uid, "created_at": now},
        )
    # ``updated_at`` deliberately untouched (as 079): a system correction, not
    # a user edit.
    for pid, raw in plan.profile_strip.items():
        conn.execute(sa.text("UPDATE flight_profiles SET settings_json = :s WHERE id = :id"),
                     {"s": raw, "id": pid})


def cruise_ias_after(conn) -> dict[str, float | None]:
    """Each flight's cruise IAS as the post-#777 code resolves it, read from the DB."""
    aircraft = {
        aid: (uid, _usable(speed))
        for aid, uid, speed in conn.execute(sa.text(
            "SELECT id, user_id, cruise_speed_kt FROM user_aircraft"
        )).fetchall()
    }
    default_speed: dict[str, float | None] = {}
    for aid, uid, speed, _ in sorted(
        conn.execute(sa.text(
            "SELECT id, user_id, cruise_speed_kt, is_default FROM user_aircraft "
            "WHERE is_default = :t"
        ), {"t": True}).fetchall()
    ):
        default_speed.setdefault(uid, _usable(speed))
    out: dict[str, float | None] = {}
    for fid, uid, aid in conn.execute(sa.text(
        "SELECT id, user_id, aircraft_id FROM flights"
    )).fetchall():
        if aid and aid in aircraft:
            out[fid] = aircraft[aid][1]
        else:
            out[fid] = default_speed.get(uid)
    return out


def format_report(plan: BackfillPlan, *, limit: int = 20) -> str:
    lines = [plan.summary()]
    mism = plan.mismatches
    if mism:
        lines.append(f"{len(mism)} flights change cruise IAS (first {min(limit, len(mism))}):")
        lines.extend(f"  {fid}: {b} -> {a}" for fid, b, a in mism[:limit])
    return "\n".join(lines)
