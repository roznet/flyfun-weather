"""Tests for the cross-border operational-friction flag (#344).

The flag models what the pilot is *not prepared for*: a formality the filed
``origin → destination`` didn't carry (graded by the alternate's absolute
formalities), clearing into a different country than filed, or a field that
can't process the arrival (not a POE). Membership rules live in
``euro_aip.borders``; POE only ever adds the facility note, never a downgrade.
"""

from weatherbrief.analysis.operational_flags import (
    CROSS_BORDER_CODE,
    cross_border_flag,
)


class TestUnpreparedFormality:
    """Reason 1: the alternate needs an axis the filed flight didn't. Absolute
    grade — both axes red, one amber."""

    def test_domestic_to_uk_is_red(self):
        # FR→FR filed, divert to the UK: customs + immigration, unplanned → red.
        flag = cross_border_flag("FR", "FR", "GB", alt_is_poe=True)
        assert flag is not None
        assert flag.code == CROSS_BORDER_CODE
        assert flag.severity == "red"
        assert "customs and immigration" in flag.detail
        assert "from France" in flag.detail

    def test_switzerland_bound_diverting_to_uk_is_red(self):
        # CH→FR filed (customs-only prep); divert to UK adds immigration AND it's
        # a full third-country border → red by absolute grade (decision: red even
        # though only one axis is newly required).
        flag = cross_border_flag("CH", "FR", "GB", alt_is_poe=True)
        assert flag.severity == "red"
        assert "from Switzerland" in flag.detail

    def test_domestic_ch_to_france_customs_only_is_amber(self):
        # CH→CH filed, divert to France: customs only (both Schengen) → amber.
        flag = cross_border_flag("CH", "CH", "FR", alt_is_poe=False)
        assert flag.severity == "amber"
        assert "customs applies" in flag.detail

    def test_domestic_fr_to_ireland_immigration_only_is_amber(self):
        # FR→FR filed, divert to Ireland: immigration only (both EU customs) → amber.
        flag = cross_border_flag("FR", "FR", "IE", alt_is_poe=False)
        assert flag.severity == "amber"
        assert "immigration/passport control applies" in flag.detail


class TestPreparedButDifferentCountry:
    """Reason 2: prepared for the border, but the alternate is a different
    country than filed → amber (Q1 soften)."""

    def test_prepared_intl_but_different_country_is_amber_not_red(self):
        # UK→FR filed (prepared for the full EU-entry border); divert to a German
        # POE. No NEW axis, so it softens to amber even though UK→DE is a full
        # border — the friction is "wrong country", not "unprepared".
        flag = cross_border_flag("GB", "FR", "DE", alt_is_poe=True)
        assert flag.severity == "amber"  # NOT red
        assert "Germany" in flag.detail
        assert "France" in flag.detail
        assert "different country" in flag.detail

    def test_same_country_as_filed_poe_no_flag(self):
        # UK→FR filed, divert to a French POE: prepared, same country, can clear → no flag.
        assert cross_border_flag("GB", "FR", "FR", alt_is_poe=True) is None


class TestFacilityGap:
    """Reason 3: the alternate needs a formality but isn't a listed POE → amber
    uncertainty (never an assertion that customs is unavailable)."""

    def test_prepared_intl_but_alt_not_poe_is_amber(self):
        # UK→FR filed, divert to a French field that is NOT a POE → amber "verify".
        flag = cross_border_flag("GB", "FR", "FR", alt_is_poe=False)
        assert flag.severity == "amber"
        assert "not listed as a point of entry" in flag.detail
        assert "could not be verified" in flag.detail


class TestNoFlag:
    def test_domestic_same_country_no_flag(self):
        assert cross_border_flag("FR", "FR", "FR", alt_is_poe=True) is None

    def test_intra_eu_different_country_no_formality_no_flag(self):
        # Q2: FR→DE filed, divert to Belgium — all Schengen + EU customs, zero
        # formalities. "Different country" alone (no obligation) must NOT flag.
        assert cross_border_flag("FR", "DE", "BE", alt_is_poe=True) is None


class TestKnownLimitations:
    def test_cta_ie_gb_over_flags(self):
        # euro_aip models Schengen + EU-customs only, not the IE↔GB Common Travel
        # Area, so IE→GB reads as a full border → red. Accepted over-warn (#344).
        flag = cross_border_flag("IE", "IE", "GB", alt_is_poe=True)
        assert flag is not None
        assert flag.severity == "red"


class TestMissingCountry:
    def test_missing_origin_no_flag(self):
        assert cross_border_flag(None, "FR", "GB", alt_is_poe=False) is None

    def test_missing_destination_no_flag(self):
        assert cross_border_flag("FR", None, "GB", alt_is_poe=False) is None

    def test_missing_alt_no_flag(self):
        assert cross_border_flag("FR", "FR", None, alt_is_poe=False) is None

    def test_empty_string_no_flag(self):
        assert cross_border_flag("FR", "FR", "", alt_is_poe=False) is None


class TestWording:
    def test_case_insensitive(self):
        assert cross_border_flag("fr", "fr", "gb", alt_is_poe=True).severity == "red"

    def test_expands_iso_codes_to_country_names(self):
        flag = cross_border_flag("CH", "CH", "IT", alt_is_poe=False)
        assert "field in Italy" in flag.detail
        assert "from Switzerland" in flag.detail
        assert "this IT field" not in flag.detail

    def test_expands_multiword_country_name(self):
        flag = cross_border_flag("FR", "FR", "GB", alt_is_poe=True)
        assert "field in United Kingdom" in flag.detail


class TestPoeOverlayModulatesMessageNotSeverity:
    def test_poe_gives_reassurance_wording(self):
        flag = cross_border_flag("FR", "FR", "GB", alt_is_poe=True)
        assert flag.severity == "red"  # severity unchanged by POE
        assert "point of entry" in flag.detail
        assert "could not be verified" not in flag.detail

    def test_non_poe_gives_uncertainty_note(self):
        flag = cross_border_flag("FR", "FR", "GB", alt_is_poe=False)
        assert flag.severity == "red"  # NOT downgraded / upgraded by POE
        assert "could not be verified" in flag.detail

    def test_poe_status_never_changes_severity(self):
        assert (
            cross_border_flag("FR", "FR", "GB", alt_is_poe=True).severity
            == cross_border_flag("FR", "FR", "GB", alt_is_poe=False).severity
        )


class TestTextDigestRendering:
    """The plain-text digest surfaces the flag with an explicit severity word
    (PR #349 review — the tag loop had no direct coverage)."""

    def test_cross_border_tag_rendered_with_severity(self):
        from weatherbrief.digest.text import _format_route_alternates
        from weatherbrief.models.alternates import AlternateAirport, RouteAlternates

        flag = cross_border_flag("FR", "FR", "GB", alt_is_poe=True)  # red
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
