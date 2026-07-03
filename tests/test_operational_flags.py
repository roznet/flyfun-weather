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
