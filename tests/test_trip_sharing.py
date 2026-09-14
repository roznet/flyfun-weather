"""Opening a shared trip from another account (#602 sharing).

The rule under test is deliberately narrow: **a trip is readable by a non-owner
exactly when every one of its legs is.** There is no trip-level privacy switch
and no trip share permission — `flights.private`, the switch that already
decides whether a shared leg link resolves, decides this too.

All-or-nothing rather than "show the legs that are public" because a trip is a
conjunctive chain. Its headline names the leg that decides the trip, and
computed over a visible subset that headline is not merely incomplete but wrong
in the dangerous direction: a private red return leg leaves the recipient
reading a green chain. See designs/flight-trips.md.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.db.models import FlightRow
from weatherbrief.models import Flight
from weatherbrief.storage.flights import save_flight

_NOW = datetime.now(timezone.utc)
_OWNER = DEV_USER_ID
_FRIEND = "friend"


@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    for user_id, name in ((_OWNER, "Alice Pilot"), (_FRIEND, "Bob Passenger")):
        s.add(UserRow(
            id=user_id, provider="local", provider_sub=user_id,
            email=f"{user_id}@localhost", display_name=name, approved=True,
        ))
    s.flush()
    for user_id in (_OWNER, _FRIEND):
        s.add(UserPreferencesRow(user_id=user_id))
    s.commit()
    s.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def make_client(app_db, tmp_path, monkeypatch):
    """A client factory keyed on user id, so one test can be two accounts.

    Both clients share the session factory, hence the same DB: "owner shares,
    friend opens" is the whole scenario and it cannot be expressed with a single
    authenticated identity.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test")

    def _build(user_id: str) -> TestClient:
        app = create_app()

        def _override_get_db():
            s = app_db()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[current_user_id] = lambda: user_id
        return TestClient(app, raise_server_exceptions=False)

    return _build


@pytest.fixture
def owner(make_client):
    return make_client(_OWNER)


@pytest.fixture
def friend(make_client):
    return make_client(_FRIEND)


def _make_flight(session, waypoints: list[str], days_ahead: float, hour: int = 9) -> Flight:
    dep = (_NOW + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0,
    )
    route_name = "_".join(w.lower() for w in waypoints)
    h = hashlib.sha256(json.dumps(
        {"alt": 8000, "ceil": 18000, "dur": 1.0, "time": dep.strftime("%H:%M"),
         "route": route_name, "user": _OWNER},
        sort_keys=True,
    ).encode()).hexdigest()[:6]
    flight = Flight(
        id=f"{route_name}-{dep.strftime('%Y-%m-%d')}-{h}",
        user_id=_OWNER,
        route_name=route_name,
        waypoints=waypoints,
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=1.0,
        created_at=_NOW,
    )
    save_flight(session, flight, _OWNER)
    return flight


@pytest.fixture
def chain(app_db):
    """Fri EGTF→LSGS, Sun LSGS→LFAT→EGTF — the design's worked example."""
    s = app_db()
    out = _make_flight(s, ["EGTF", "LSGS"], 3)
    mid = _make_flight(s, ["LSGS", "LFAT"], 5, hour=10)
    back = _make_flight(s, ["LFAT", "EGTF"], 5, hour=13)
    s.commit()
    s.close()
    return [out, mid, back]


@pytest.fixture
def trip(owner, chain):
    return owner.post(
        "/api/trips", json={"flight_ids": [f.id for f in chain]},
    ).json()


def _set_private(app_db, flight_id: str, value: bool = True) -> None:
    s = app_db()
    s.get(FlightRow, flight_id).private = value
    s.commit()
    s.close()


class TestReadingASharedTrip:
    def test_a_friend_can_open_a_trip_whose_legs_are_all_public(
        self, friend, trip, chain,
    ):
        """The bug this feature fixes: the link used to 404 for everyone else."""
        r = friend.get(f"/api/trips/{trip['id']}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == "viewer"
        assert [leg["flight_id"] for leg in body["summary"]["legs"]] == [
            f.id for f in chain
        ]
        assert body["summary"]["chain_label"] == "EGTF → LSGS → LFAT → EGTF"

    def test_the_summary_endpoint_is_readable_too(self, friend, trip):
        r = friend.get(f"/api/trips/{trip['id']}/summary")
        assert r.status_code == 200, r.text
        assert r.json()["total_legs"] == 3

    def test_one_private_leg_hides_the_whole_trip(self, friend, trip, chain, app_db):
        """All-or-nothing. A partial chain would be worse than no chain.

        The trip *name* is derived from the chain ("EGTF → LSGS → LFAT → EGTF"),
        so serving anything at all here would leak the route of the very leg
        that was just made private.
        """
        _set_private(app_db, chain[2].id)
        assert friend.get(f"/api/trips/{trip['id']}").status_code == 404
        assert friend.get(f"/api/trips/{trip['id']}/summary").status_code == 404

    def test_the_owner_still_sees_their_trip_with_a_private_leg(
        self, owner, trip, chain, app_db,
    ):
        _set_private(app_db, chain[2].id)
        r = owner.get(f"/api/trips/{trip['id']}")
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "owner"
        # …and is told the link would not resolve, since only they can fix it.
        assert r.json()["is_shareable"] is False

    def test_an_empty_trip_is_not_shareable(self, owner, friend):
        """No legs means nothing to share, and a bare name is not a briefing."""
        empty = owner.post("/api/trips", json={"flight_ids": [], "name": "Later"}).json()
        assert owner.get(f"/api/trips/{empty['id']}").status_code == 200
        assert friend.get(f"/api/trips/{empty['id']}").status_code == 404

    def test_the_viewer_payload_withholds_the_owners_own_business(
        self, owner, friend, trip,
    ):
        owner.patch(
            f"/api/trips/{trip['id']}",
            json={"notes": "book the hotel", "auto_refresh": True,
                  "notify_override": "mute"},
        )
        body = friend.get(f"/api/trips/{trip['id']}").json()
        assert body["notes"] is None
        assert body["auto_refresh"] is False
        assert body["notify_override"] == "default"
        assert body["refresh"] is None
        # The share code is the owner's to hand out, not the recipient's to pass on.
        assert body["share_code"] is None

    def test_the_ai_paragraph_is_not_served_to_a_viewer(
        self, owner, friend, trip, app_db,
    ):
        """The owner paid for it under their own AI-digest consent.

        The recipient never agreed to it, and the deterministic headline beside
        it says the same thing — so a viewer gets the headline and no paragraph.
        """
        from weatherbrief.db.models import FlightTripRow

        s = app_db()
        row = s.get(FlightTripRow, trip["id"])
        row.ai_summary_text = "Sunday is the leg to watch."
        row.ai_summary_at = _NOW
        s.commit()
        s.close()

        assert owner.get(f"/api/trips/{trip['id']}").json()["ai_summary"] is not None
        assert friend.get(f"/api/trips/{trip['id']}").json()["ai_summary"] is None

    def test_the_owners_display_name_is_carried_but_never_their_email(
        self, friend, trip,
    ):
        body = friend.get(f"/api/trips/{trip['id']}").json()
        assert body["owner_display_name"] == "Alice Pilot"
        assert "localhost" not in json.dumps(body)


class TestTripList:
    def test_the_list_reports_shareability_per_trip(self, owner, chain, app_db):
        """Batched for the whole list, but still each trip's own answer.

        ``list_trips`` resolves shareability once via ``shareable_trip_ids``
        instead of a grouped query per trip; the risk of that change is a single
        verdict smeared across the list, so pin two trips that disagree.
        """
        shareable = owner.post(
            "/api/trips", json={"flight_ids": [chain[0].id], "name": "Open"},
        ).json()
        closed = owner.post(
            "/api/trips", json={"flight_ids": [chain[1].id], "name": "Closed"},
        ).json()
        _set_private(app_db, chain[1].id)

        by_id = {t["id"]: t for t in owner.get("/api/trips").json()}
        assert by_id[shareable["id"]]["is_shareable"] is True
        assert by_id[closed["id"]]["is_shareable"] is False

    def test_an_empty_trip_is_listed_as_not_shareable(self, owner):
        empty = owner.post("/api/trips", json={"flight_ids": [], "name": "Later"}).json()
        listed = {t["id"]: t for t in owner.get("/api/trips").json()}
        assert listed[empty["id"]]["is_shareable"] is False


class TestWritesStayWithTheOwner:
    @pytest.mark.parametrize("method,suffix,body", [
        ("patch", "", {"name": "mine now"}),
        ("delete", "", None),
        ("post", "/legs", {"flight_ids": []}),
        ("post", "/refresh", None),
        ("get", "/refresh/status", None),
        ("post", "/ai-summary", None),
    ])
    def test_a_viewer_cannot_write_to_a_trip_they_can_read(
        self, friend, trip, method, suffix, body,
    ):
        """Readable is not writable, and refresh is the sharp one.

        A trip refresh spends the owner's money and is admitted against the
        *owner's* per-user refresh slots, so a viewer-triggered chain would lock
        the owner out of refreshing their own flights.
        """
        path = f"/api/trips/{trip['id']}{suffix}"
        r = getattr(friend, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 404, f"{method.upper()} {path} -> {r.status_code}"

    def test_a_viewer_cannot_unlink_a_leg(self, friend, trip, chain):
        r = friend.delete(f"/api/trips/{trip['id']}/legs/{chain[0].id}")
        assert r.status_code == 404


class TestShareCode:
    def test_a_new_trip_gets_a_share_code(self, trip):
        assert trip["share_code"]
        assert len(trip["share_code"]) == 8

    def test_the_short_link_redirects_to_the_trip_page(self, owner, trip):
        r = owner.get(f"/t/{trip['share_code']}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == f"/trip.html?id={trip['id']}"

    def test_an_unknown_code_is_a_404(self, owner):
        assert owner.get("/t/zzzzzzzz", follow_redirects=False).status_code == 404
        # Shape-rejected before it ever reaches the DB.
        assert owner.get("/t/!!", follow_redirects=False).status_code == 404

    def test_by_share_resolves_for_a_friend(self, friend, trip):
        r = friend.get(f"/api/trips/by-share/{trip['share_code']}")
        assert r.status_code == 200, r.text
        assert r.json()["id"] == trip["id"]
        assert r.json()["role"] == "viewer"

    def test_by_share_is_a_404_once_a_leg_goes_private(
        self, friend, trip, chain, app_db,
    ):
        """Holding the code is not access — the leg switch still decides."""
        _set_private(app_db, chain[1].id)
        r = friend.get(f"/api/trips/by-share/{trip['share_code']}")
        assert r.status_code == 404

    def test_a_trip_created_before_sharing_existed_still_shares(
        self, owner, chain, app_db,
    ):
        """Codes are minted lazily on read, so no row is stranded without one."""
        from weatherbrief.db.models import FlightTripRow

        created = owner.post(
            "/api/trips", json={"flight_ids": [f.id for f in chain]},
        ).json()
        s = app_db()
        s.get(FlightTripRow, created["id"]).share_code = None
        s.commit()
        s.close()

        code = owner.get(f"/api/trips/{created['id']}").json()["share_code"]
        assert code
        assert owner.get(
            f"/t/{code}", follow_redirects=False,
        ).headers["location"] == f"/trip.html?id={created['id']}"


class TestATripThatVanishesMidRequest:
    def test_a_trip_deleted_while_minting_is_a_404_not_a_500(
        self, owner, chain, app_db, monkeypatch,
    ):
        """The narrow window inside ``ensure_share_code``, answered honestly.

        Its conditional UPDATE matches nothing and the row is still code-less
        only when the trip was deleted between this request reading it and
        minting. Letting the storage ``KeyError`` propagate would be a 500,
        which says the server broke; nothing did. 404 is what every other
        missing-trip path answers.
        """
        from weatherbrief.db.models import FlightTripRow
        import weatherbrief.storage.trips as trip_storage

        created = owner.post(
            "/api/trips", json={"flight_ids": [f.id for f in chain]},
        ).json()
        s = app_db()
        s.get(FlightTripRow, created["id"]).share_code = None
        s.commit()
        s.close()

        real_allocate = trip_storage.allocate_share_code

        def _delete_then_allocate(session):
            # Stands in for a concurrent DELETE landing between the read and
            # the mint — the only way to reach the raise.
            session.execute(
                FlightTripRow.__table__.delete().where(
                    FlightTripRow.id == created["id"]
                )
            )
            session.flush()
            return real_allocate(session)

        monkeypatch.setattr(
            trip_storage, "allocate_share_code", _delete_then_allocate,
        )

        r = owner.get(f"/api/trips/{created['id']}")
        assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"


    def test_one_vanished_trip_does_not_fail_the_whole_list(
        self, owner, chain, app_db, monkeypatch,
    ):
        """A trip deleted mid-request drops out; it does not 404 the list.

        `GET /api/trips` renders each trip in turn, and the 404-on-vanish that
        is right for a *single* trip would, inside the list, let one trip pruned
        by another tab (or by `prune_empty_trips` on a flight delete) take the
        caller's other, perfectly healthy trips down with it.
        """
        from weatherbrief.db.models import FlightTripRow
        import weatherbrief.storage.trips as trip_storage

        doomed = owner.post(
            "/api/trips", json={"flight_ids": [chain[0].id], "name": "Doomed"},
        ).json()
        survivor = owner.post(
            "/api/trips", json={"flight_ids": [chain[1].id], "name": "Survivor"},
        ).json()

        s = app_db()
        s.get(FlightTripRow, doomed["id"]).share_code = None
        s.commit()
        s.close()

        real_allocate = trip_storage.allocate_share_code

        def _delete_doomed_then_allocate(session):
            session.execute(
                FlightTripRow.__table__.delete().where(
                    FlightTripRow.id == doomed["id"]
                )
            )
            session.flush()
            return real_allocate(session)

        monkeypatch.setattr(
            trip_storage, "allocate_share_code", _delete_doomed_then_allocate,
        )

        r = owner.get("/api/trips")
        assert r.status_code == 200, f"the list 404'd: {r.text}"
        listed = {t["id"] for t in r.json()}
        assert doomed["id"] not in listed
        assert survivor["id"] in listed


class TestFollowingASharedTrip:
    def test_subscribing_adds_every_leg_to_the_friends_list(
        self, friend, trip, chain,
    ):
        r = friend.post(f"/api/trips/{trip['id']}/subscribe")
        assert r.status_code == 200, r.text
        assert r.json() == {
            "trip_id": trip["id"], "changed": 3, "total_legs": 3,
            "is_subscribed": True,
        }
        listed = {f["id"] for f in friend.get("/api/flights").json()}
        assert listed == {f.id for f in chain}

    def test_subscribing_twice_is_a_success_with_nothing_changed(
        self, friend, trip,
    ):
        friend.post(f"/api/trips/{trip['id']}/subscribe")
        r = friend.post(f"/api/trips/{trip['id']}/subscribe")
        assert r.status_code == 200
        assert r.json()["changed"] == 0
        assert r.json()["is_subscribed"] is True

    def test_the_trip_reports_whether_the_viewer_already_follows_it(
        self, friend, trip,
    ):
        assert friend.get(f"/api/trips/{trip['id']}").json()["is_subscribed"] is False
        friend.post(f"/api/trips/{trip['id']}/subscribe")
        assert friend.get(f"/api/trips/{trip['id']}").json()["is_subscribed"] is True

    def test_a_leg_added_after_subscribing_leaves_the_trip_unfollowed(
        self, owner, friend, trip, chain, app_db,
    ):
        """Membership is a snapshot, and the flag says so rather than lying.

        There is no trip subscription row to keep in step — following a trip is
        a loop over its legs — so a leg added later is genuinely not followed,
        and the button comes back un-pressed instead of silently missing a leg.
        """
        friend.post(f"/api/trips/{trip['id']}/subscribe")
        s = app_db()
        extra = _make_flight(s, ["EGTF", "EGHI"], 6)
        s.commit()
        s.close()
        owner.post(f"/api/trips/{trip['id']}/legs", json={"flight_ids": [extra.id]})

        assert friend.get(f"/api/trips/{trip['id']}").json()["is_subscribed"] is False

    def test_unsubscribing_drops_every_leg(self, friend, trip):
        friend.post(f"/api/trips/{trip['id']}/subscribe")
        r = friend.delete(f"/api/trips/{trip['id']}/subscribe")
        assert r.status_code == 200, r.text
        assert r.json()["changed"] == 3
        assert friend.get("/api/flights").json() == []

    def test_a_viewer_can_let_go_of_a_trip_that_went_private(
        self, friend, trip, chain, app_db,
    ):
        """Unsubscribe is deliberately not gated on shareability.

        Otherwise the legs of a trip the owner has since closed would be stuck
        in the recipient's list with no way to remove them.
        """
        friend.post(f"/api/trips/{trip['id']}/subscribe")
        _set_private(app_db, chain[0].id)
        assert friend.get(f"/api/trips/{trip['id']}").status_code == 404
        assert friend.delete(f"/api/trips/{trip['id']}/subscribe").status_code == 200

    def test_the_owner_cannot_subscribe_to_their_own_trip(self, owner, trip):
        r = owner.post(f"/api/trips/{trip['id']}/subscribe")
        assert r.status_code == 409

    def test_unsubscribe_is_not_an_existence_oracle(
        self, friend, trip, chain, app_db,
    ):
        """A stranger holding only a trip id learns nothing from DELETE.

        Unsubscribe is ungated on *shareability* so a follower can let go of a
        trip that went private — but ungated on shareability is not ungated.
        Answering on leg count alone would make this the one route that
        confirms a trip exists, and how many legs it has, to a caller that
        ``GET`` on the same id correctly tells nothing.
        """
        _set_private(app_db, chain[0].id)
        assert friend.get(f"/api/trips/{trip['id']}").status_code == 404
        # Never followed it, and cannot see it: same answer as the GET.
        r = friend.delete(f"/api/trips/{trip['id']}/subscribe")
        assert r.status_code == 404
        assert "total_legs" not in r.text

    def test_a_shareable_trip_can_be_dropped_without_ever_following_it(
        self, friend, trip,
    ):
        """The gate is "may I see this, or do I follow it" — not "do I follow it".

        A trip the viewer can read is one they are entitled to act on, so an
        unsubscribe that removes nothing still answers honestly rather than
        pretending the trip is missing.
        """
        r = friend.delete(f"/api/trips/{trip['id']}/subscribe")
        assert r.status_code == 200
        assert r.json()["changed"] == 0


class TestShareCodeMinting:
    def test_a_concurrent_first_read_cannot_hand_out_a_losing_code(
        self, owner, chain, app_db,
    ):
        """Two first-reads race; both must return the code that actually landed.

        The mint used to be read-modify-write: both requests saw NULL, both
        generated a code, and the loser still *returned* its own. A link built
        from that code is pasted into a message and 404s for the recipient
        forever. The conditional UPDATE means exactly one writer wins and the
        loser reads back the winner's code.

        **What this cannot cover**: the loser's re-read is ``FOR UPDATE``
        because MySQL at REPEATABLE READ would otherwise serve it from a
        snapshot predating the winner's commit. SQLite ignores ``FOR UPDATE``
        and has no snapshot to be stale, so this passes either way — dropping
        the lock would regress production only. The reasoning lives in
        ``ensure_share_code``'s docstring; treat it as load-bearing rather than
        assuming a green suite has checked it.
        """
        from weatherbrief.db.models import FlightTripRow
        from weatherbrief.storage.trips import ensure_share_code

        created = owner.post(
            "/api/trips", json={"flight_ids": [f.id for f in chain]},
        ).json()

        # Two sessions, each holding its own instance of a row with no code —
        # the state both racers start from.
        s1, s2 = app_db(), app_db()
        s1.get(FlightTripRow, created["id"]).share_code = None
        s1.commit()

        row1 = s1.get(FlightTripRow, created["id"])
        row2 = s2.get(FlightTripRow, created["id"])
        assert row1.share_code is None and row2.share_code is None

        first = ensure_share_code(s1, row1)
        s1.commit()
        second = ensure_share_code(s2, row2)
        s2.commit()

        assert first == second, "the loser handed out a code that never landed"

        s3 = app_db()
        assert s3.get(FlightTripRow, created["id"]).share_code == first
        for session in (s1, s2, s3):
            session.close()

        # And the code that was returned is the one the link resolves.
        assert owner.get(
            f"/t/{first}", follow_redirects=False,
        ).headers["location"] == f"/trip.html?id={created['id']}"


class TestFindingTheTripFromASharedLeg:
    def test_a_subscribed_leg_carries_a_trip_badge_back_to_the_chain(
        self, friend, trip, chain,
    ):
        """Without this a shared leg is a dead end: the recipient can read the
        briefing they were sent and has no route to the chain it belongs to,
        which is the entire reason the trip exists."""
        friend.post(f"/api/trips/{trip['id']}/subscribe")
        listed = {f["id"]: f for f in friend.get("/api/flights").json()}
        badge = listed[chain[1].id]["trip"]
        assert badge is not None
        assert badge["id"] == trip["id"]
        assert (badge["position"], badge["total"]) == (2, 3)
        # The owner's switch, never reported as the viewer's to flip.
        assert badge["auto_refresh"] is False

    def test_the_single_flight_endpoint_carries_it_too(self, friend, trip, chain):
        friend.post(f"/api/trips/{trip['id']}/subscribe")
        body = friend.get(f"/api/flights/{chain[0].id}").json()
        assert body["trip"]["id"] == trip["id"]

    def test_no_badge_when_the_trip_would_not_open(
        self, friend, trip, chain, app_db,
    ):
        """A badge linking to a 404 is worse than no badge."""
        friend.post(f"/api/trips/{trip['id']}/subscribe")
        _set_private(app_db, chain[2].id)
        listed = {f["id"]: f for f in friend.get("/api/flights").json()}
        assert listed[chain[0].id]["trip"] is None
        assert friend.get(f"/api/flights/{chain[0].id}").json()["trip"] is None
