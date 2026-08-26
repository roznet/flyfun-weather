"""The corpus states its own cruise-altitude coverage (#578).

Validating #539 across 201 staging packs gave 8 escalations and 1 de-escalation,
which reads as "this change only ever raises risk". It is an artifact of the
corpus: 71% of it flies below 10,000 ft, where the change cannot tighten by
construction, and of the 15 packs at/above 16,000 ft, 11 already read "Smooth
ride expected" — nothing to de-escalate. The de-escalating half is real and
large; it simply has almost no representation.

So a replay reports the altitude distribution of what it replayed, and these
tests pin the shape of that report — including the count that actually matters:
how many high packs carry a flagged baseline.
"""

from __future__ import annotations

import json

import pytest

from weatherbrief.eval_workbench import corpus


@pytest.fixture
def area(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_CORPUS_DIR", str(tmp_path))
    return tmp_path


def _pack(corpus_id: str, cruise_ft: int, turbulence: str | None = None):
    """Write a corpus pack; ``turbulence`` is its saved baseline status."""
    corpus.save_corpus_meta(corpus.CorpusMeta(
        corpus_id=corpus_id,
        route="EGTF -> LFAT",
        target_date="2026-03-14",
        fetch_date="2026-03-07",
        departure_time="2026-03-14T09:00:00+00:00",
        fetch_timestamp="2026-03-07T06:30:00+00:00",
        days_out=7,
        cruise_altitude_ft=cruise_ft,
    ))
    if turbulence is not None:
        (corpus.pack_path(corpus_id) / "route_advisories.json").write_text(
            json.dumps({"advisories": [{
                "advisory_id": "turbulence",
                "aggregate_status": turbulence,
                "per_model": [{"model": "gfs", "status": turbulence}],
            }]})
        )


def test_bands_count_every_pack_once(area):
    _pack("low_a", 3000)
    _pack("low_b", 3500)
    _pack("mid", 9000)
    _pack("aloft", 17000)

    prof = corpus.altitude_profile()
    assert prof["total"] == 4
    assert dict(((lo, hi), n) for lo, hi, n in prof["bands"]) == {
        (2000, 4000): 2, (8000, 10000): 1, (16000, 18000): 1,
    }
    assert sum(n for _, _, n in prof["bands"]) == prof["total"]


def test_the_two_headline_counts(area):
    """The two numbers a reader needs: how much is low, how much is aloft."""
    for i in range(7):
        _pack(f"low_{i}", 6000)
    _pack("aloft_a", 16000)
    _pack("aloft_b", 26000)

    prof = corpus.altitude_profile()
    assert prof["below_low_level_ceiling"] == 7
    assert prof["aloft"] == 2, "the floor is inclusive — 16,000 ft counts"


def test_only_a_flagged_baseline_can_de_escalate(area):
    """An aloft pack that already reads "smooth" measures nothing on the way down.

    This is the count that explains #539's one-sided result, so it is the count
    the profile reports — not just how many high packs exist.
    """
    _pack("aloft_smooth", 18000, turbulence="green")
    _pack("aloft_rough", 18000, turbulence="amber")
    _pack("aloft_no_baseline", 18000)
    _pack("low_rough", 5000, turbulence="red")

    prof = corpus.altitude_profile()
    assert prof["aloft"] == 3
    assert prof["aloft_with_flagged_turbulence"] == 1


def test_the_printed_block_carries_the_caveat(area):
    """The profile is printed to be read — the numbers alone are just a table."""
    _pack("low", 4000)
    _pack("aloft", 17000)

    block = corpus.format_altitude_profile()
    assert "2 pack(s) replayed" in block
    assert "1 at/above 16000 ft" in block
    assert "does not cover" in block, "the caveat is the point of printing it"


def test_an_empty_selection_says_so(area):
    assert corpus.format_altitude_profile([]) == "Cruise-altitude profile: no packs."
