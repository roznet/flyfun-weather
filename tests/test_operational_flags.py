"""Tests for the cross-border operational-friction flag (#344).

Severity comes purely from the country-pair (Schengen / EU-customs-union
membership, owned by ``euro_aip.borders``); point-of-entry status modulates
only the wording. These tests pin the acceptance criteria from the issue.
"""

from weatherbrief.analysis.operational_flags import (
    CROSS_BORDER_CODE,
    cross_border_flag,
)


class TestSeverityFromCountryPair:
    def test_fr_to_gb_is_red(self):
        # LFAT (FR) dest + EGMD (GB) alt: customs + immigration → red.
        flag = cross_border_flag("FR", "GB", alt_is_poe=True)
        assert flag is not None
        assert flag.code == CROSS_BORDER_CODE
        assert flag.severity == "red"
        assert "customs and immigration" in flag.detail

    def test_fr_to_ch_customs_only_is_amber(self):
        # Switzerland: Schengen (no immigration) but outside EU customs → amber.
        flag = cross_border_flag("FR", "CH", alt_is_poe=False)
        assert flag is not None
        assert flag.severity == "amber"
        assert "customs applies" in flag.detail

    def test_fr_to_ie_immigration_only_is_amber(self):
        # Ireland: EU customs (no customs) but outside Schengen → amber.
        flag = cross_border_flag("FR", "IE", alt_is_poe=False)
        assert flag is not None
        assert flag.severity == "amber"
        assert "immigration/passport control applies" in flag.detail

    def test_fr_to_de_no_flag(self):
        # Both blocs shared → no friction, no flag.
        assert cross_border_flag("FR", "DE", alt_is_poe=False) is None

    def test_same_country_no_flag(self):
        assert cross_border_flag("FR", "FR", alt_is_poe=False) is None

    def test_channel_islands_is_red(self):
        # Jersey is outside both blocs → full customs + immigration border.
        flag = cross_border_flag("FR", "JE", alt_is_poe=False)
        assert flag is not None
        assert flag.severity == "red"

    def test_case_insensitive(self):
        assert cross_border_flag("fr", "gb", alt_is_poe=True).severity == "red"


class TestMissingCountry:
    def test_missing_alt_country_no_flag(self):
        assert cross_border_flag("FR", None, alt_is_poe=False) is None

    def test_missing_dest_country_no_flag(self):
        assert cross_border_flag(None, "GB", alt_is_poe=False) is None

    def test_empty_string_no_flag(self):
        assert cross_border_flag("FR", "", alt_is_poe=False) is None


class TestPoeOverlayModulatesMessageNotSeverity:
    def test_poe_gives_reassurance_wording(self):
        flag = cross_border_flag("FR", "GB", alt_is_poe=True)
        assert flag.severity == "red"  # severity unchanged by POE
        assert "point of entry" in flag.detail
        assert "could not be verified" not in flag.detail

    def test_non_poe_gives_uncertainty_note_not_downgrade(self):
        # Requested behaviour: CH↔FR amber + non-POE → "could not verify
        # customs requirement", NOT an assertion that it's unavailable.
        flag = cross_border_flag("FR", "CH", alt_is_poe=False)
        assert flag.severity == "amber"  # NOT downgraded
        assert "could not be verified" in flag.detail
        assert "not listed as a point of entry" in flag.detail

    def test_poe_status_never_changes_severity(self):
        assert (
            cross_border_flag("FR", "GB", alt_is_poe=True).severity
            == cross_border_flag("FR", "GB", alt_is_poe=False).severity
        )


class TestTextDigestRendering:
    """The plain-text digest surfaces the flag with an explicit severity word
    (PR #349 review — the tag loop had no direct coverage)."""

    def test_cross_border_tag_rendered_with_severity(self):
        from weatherbrief.analysis.operational_flags import cross_border_flag
        from weatherbrief.digest.text import _format_route_alternates
        from weatherbrief.models.alternates import AlternateAirport, RouteAlternates

        flag = cross_border_flag("FR", "GB", alt_is_poe=True)  # red
        assert flag is not None
        alt = RouteAlternates(
            destination_icao="LFAT",
            destination_category="VFR",
            corridor_nm=40.0,
            radius_nm=50.0,
            candidates_evaluated=1,
            alternates=[
                AlternateAirport(
                    icao="EGMD", lat=50.96, lon=0.94,
                    distance_from_dest_nm=40.0, position="before",
                    flight_category="VFR",
                    operational_flags=[flag],
                )
            ],
        )
        lines = _format_route_alternates(alt)
        egmd_line = next(ln for ln in lines if ln.strip().startswith("EGMD:"))
        # Constant label + explicit severity word for the colour-less reader.
        assert "cross-border (red)" in egmd_line

    def test_no_tag_when_no_flags(self):
        from weatherbrief.digest.text import _format_route_alternates
        from weatherbrief.models.alternates import AlternateAirport, RouteAlternates

        alt = RouteAlternates(
            destination_icao="LFAT", destination_category="VFR",
            corridor_nm=40.0, radius_nm=50.0, candidates_evaluated=1,
            alternates=[
                AlternateAirport(
                    icao="LFAC", lat=50.9, lon=1.9,
                    distance_from_dest_nm=30.0, position="after",
                    flight_category="VFR",
                )
            ],
        )
        lines = _format_route_alternates(alt)
        assert not any("cross-border" in ln.lower() for ln in lines)
