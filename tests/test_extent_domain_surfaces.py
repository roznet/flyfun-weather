"""`domain_nm` / `affected_domain` survive the server-side surfaces (#571 review).

The Swift tests cover the wire format thoroughly, but nothing asserted that the
Python code *producing* it actually emits these fields — `connectors/views.py`
was changed in this PR specifically to serialize them, and `prompt_builder.py`'s
outlier qualifier is the literal mountain_wind digest bug the issue opens with.
A regression dropping either would have passed the whole suite.
"""

from __future__ import annotations

from weatherbrief.connectors.views import advisory_detail, summarize_advisories
from weatherbrief.digest.prompt_builder import _format_route_advisories_context
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
)


def _mountain_wind_model() -> dict:
    """A domain-scoped per-model result, in the dict shape the views consume."""
    return {
        "model": "icon",
        "status": "red",
        "detail": "Mountain wave risk over 132nm/190nm of high terrain (69%)",
        "affected_pct": 69.4,
        "affected_nm": 131.8,
        "total_nm": 582.0,
        "domain_nm": 190.0,
        "affected_domain": "of high terrain",
    }


class TestMcpAndRestViews:
    def test_detail_view_emits_the_domain_fields(self):
        out = advisory_detail(
            {"advisory_id": "mountain_wind", "per_model": [_mountain_wind_model()]},
            None,
        )
        m = out["per_model"][0]
        assert m["domain_nm"] == 190.0
        assert m["affected_domain"] == "of high terrain"
        # The route length is still published and is NOT the denominator.
        assert m["total_nm"] == 582.0

    def test_summary_view_names_a_non_route_denominator(self):
        out = summarize_advisories(
            {"advisories": [
                {"advisory_id": "mountain_wind", "aggregate_status": "red",
                 "aggregate_detail": "d", "per_model": [_mountain_wind_model()]}
            ], "catalog": []}
        )
        entry = next(a for a in out if a["id"] == "mountain_wind")
        assert entry["per_model"][0]["affected_domain"] == "of high terrain"

    def test_a_route_domain_advisory_omits_the_qualifier(self):
        plain = {**_mountain_wind_model(), "affected_domain": None}
        out = advisory_detail(
            {"advisory_id": "turbulence", "per_model": [plain]}, None,
        )
        assert "affected_domain" not in out["per_model"][0]


class TestDigestPrompt:
    """The outlier line is where the ~4x overstatement actually reached the LLM."""

    def _result(self, affected_domain):
        return RouteAdvisoryResult(
            advisory_id="mountain_wind",
            aggregate_status=AdvisoryStatus.AMBER,
            aggregate_detail="Mountain wave risk",
            per_model=[
                ModelAdvisoryResult(
                    model="ecmwf", status=AdvisoryStatus.AMBER, detail="d",
                    affected_pct=40.0,
                ),
                ModelAdvisoryResult(
                    model="icon", status=AdvisoryStatus.RED, detail="d",
                    affected_pct=93.0, affected_domain=affected_domain,
                ),
            ],
        )

    def _text(self, affected_domain):
        manifest = RouteAdvisoriesManifest(
            advisories=[self._result(affected_domain)],
            catalog=[AdvisoryCatalogEntry(
                id="mountain_wind", name="Mountain Wind",
                short_description="", description="", category="turbulence",
            )],
        )
        return _format_route_advisories_context(manifest)

    def test_a_domain_scoped_outlier_names_its_denominator(self):
        text = self._text("of high terrain")
        assert "93% of high terrain affected" in text
        # Not the doubled preposition an earlier round shipped.
        assert "of of" not in text

    def test_a_route_scoped_outlier_reads_as_before(self):
        text = self._text(None)
        assert "93% affected" in text
        assert "of high terrain" not in text
