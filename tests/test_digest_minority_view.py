"""A worst-of aggregate carried by one model is labelled as a minority view.

The per-model line used to call every model that differed from the aggregate an
"outlier". When GFS alone was RED and the aggregate took the worst, ECMWF and
ICON — the majority — were the "outliers", and the briefer wrote GFS-only LIFR
up as "across models".
"""

from __future__ import annotations

from weatherbrief.digest.prompt_builder import _format_route_advisories_context
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
)

R, A, G = AdvisoryStatus.RED, AdvisoryStatus.AMBER, AdvisoryStatus.GREEN


def _text(aggregate: AdvisoryStatus, **models: AdvisoryStatus) -> str:
    manifest = RouteAdvisoriesManifest(
        advisories=[RouteAdvisoryResult(
            advisory_id="ifr_feasibility",
            aggregate_status=aggregate,
            aggregate_detail="Arr EDFE LIFR ceiling 57ft",
            per_model=[
                ModelAdvisoryResult(model=name, status=status, detail="d")
                for name, status in models.items()
            ],
        )],
        catalog=[AdvisoryCatalogEntry(
            id="ifr_feasibility", name="IFR Feasibility",
            short_description="", description="", category="ifr",
        )],
    )
    return _format_route_advisories_context(manifest)


def test_one_model_against_two_is_a_minority_view():
    text = _text(R, gfs=R, ecmwf=G, icon=G)
    assert "(minority view — only gfs sees RED; ecmwf sees GREEN; icon sees GREEN)" in text
    assert "outlier" not in text


def test_one_dissenter_is_still_an_outlier():
    text = _text(R, gfs=R, ecmwf=R, icon=G)
    assert "(outlier: icon sees GREEN)" in text
    assert "minority" not in text


def test_two_against_three_names_both_agreeing_models():
    text = _text(R, gfs=R, ukmo=R, ecmwf=A, icon=G, meteofrance=G)
    assert "(minority view — only gfs, ukmo see RED;" in text


def test_aggregate_no_model_matches_keeps_the_outlier_list():
    # A floored aggregate (e.g. DD convective) has no minority to name.
    text = _text(R, gfs=A, ecmwf=G)
    assert "(outlier: gfs sees AMBER; ecmwf sees GREEN)" in text
