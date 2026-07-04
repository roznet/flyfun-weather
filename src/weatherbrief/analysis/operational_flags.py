"""Operational-friction flags for divert candidates (issue #344).

Pure logic that turns *non-weather* friction into an :class:`OperationalFlag`.
The first (and currently only) signal is **cross-border**.

The flag models **what the pilot is not prepared for**, not merely "is there a
border." Preparedness is set by the flight actually filed (origin → destination):
if you filed an international arrival you already carry the documents and made the
customs/immigration arrangements, so diverting across a border you had planned
for is *not* the friction. The friction is one of three derived reasons:

- **Unprepared formality** — the alternate needs customs or immigration that the
  filed ``origin → destination`` did not. Graded by the alternate's **absolute**
  formalities: both → ``red``, exactly one → ``amber``. (A domestic French flight
  diverting to the UK; or a Switzerland-bound flight diverting to the UK, where
  immigration is newly required and it is a full third-country border → red.)
- **Different country than filed** — you *are* prepared for the border, but the
  alternate is in a different country than your destination, so your flight plan
  / customs notification points at the wrong authority. ``amber``. (A UK→France
  flight diverting to a German point of entry: prepared for EU entry, just not
  into Germany.)
- **Facility gap** — the alternate needs a formality but is not a listed point of
  entry, so entry facilities cannot be verified. ``amber``, phrased as
  uncertainty (never an assertion that customs is unavailable).

Severity is the worst reason that fires; the wording lists what applies. If none
fire, there is no flag (same country you filed for with the facilities you
planned; or a diversion that stays inside the same bloc as your departure).
Membership rules live in :mod:`euro_aip.borders`, not re-encoded here;
``point_of_entry`` only ever adds the facility note, never a downgrade.

Anchoring is on the **origin (departure) country**, because that is the border a
diversion actually crosses — strictly the *last departure airport*, which for
these single-leg briefings is ``route.origin``.

Known limitation: :mod:`euro_aip.borders` models Schengen + EU-customs only, so
the **IE↔GB Common Travel Area** is not represented — an Ireland↔UK pair reads as
a full customs+immigration border and will over-flag (CTA removes immigration in
practice). Accepted as a conservative over-warn (issue #344).
"""

from __future__ import annotations

from euro_aip.borders import crossing_requirements
from euro_aip.utils.country_mapper import CountryMapper

from weatherbrief.models.alternates import OperationalFlag

CROSS_BORDER_CODE = "cross_border"

# ISO-code → human country name, reusing the library's reference table so we
# don't re-encode it here. Built once (it only assembles a couple of dicts).
_COUNTRY_NAMES = CountryMapper()


def _country_name(cc: str) -> str:
    """Human country name for an ISO-3166-1 alpha-2 code (e.g. "IT" → "Italy"),
    falling back to the upper-cased code when the library doesn't know it."""
    name = _COUNTRY_NAMES.get_country_name(cc)
    return name.title() if name else cc.strip().upper()


def _formalities_phrase(immigration: bool, customs: bool) -> str:
    """Plain-language description of which formalities a country-pair triggers."""
    if immigration and customs:
        return "customs and immigration both apply"
    if customs:
        # customs only → both Schengen (no passport check) but not both in the
        # EU customs union (e.g. FR↔CH).
        return "customs applies (no immigration check — both are in the Schengen area)"
    # immigration only → both in the EU customs union but not both Schengen
    # (e.g. FR↔IE).
    return "immigration/passport control applies (no customs — both are in the EU customs union)"


def _poe_note(alt_is_poe: bool) -> str:
    """Point-of-entry overlay — reassurance vs uncertainty. Never a downgrade."""
    if alt_is_poe:
        return (
            " This field is a point of entry (customs + immigration available; "
            "often PPR — check before diverting)."
        )
    return (
        " This field is not listed as a point of entry, so entry facilities "
        "could not be verified — confirm customs/immigration availability and "
        "any PPR before diverting."
    )


def cross_border_flag(
    origin_country: str | None,
    destination_country: str | None,
    alt_country: str | None,
    alt_is_poe: bool,
) -> OperationalFlag | None:
    """Cross-border operational flag for diverting a flight filed
    ``origin_country → destination_country`` to an alternate in ``alt_country``,
    or ``None`` when no border friction applies.

    See the module docstring for the model. Returns ``None`` when any country is
    unknown (nothing to compare) or when none of the three reasons fires.
    ``alt_is_poe`` never changes severity — it only adds the facility note.
    """
    if not origin_country or not destination_country or not alt_country:
        return None

    planned = crossing_requirements(origin_country, destination_country)
    alt = crossing_requirements(origin_country, alt_country)

    has_formality = alt.customs_required or alt.immigration_required
    # Unprepared: the alternate needs an axis the filed flight did not.
    new_customs = alt.customs_required and not planned.customs_required
    new_immigration = alt.immigration_required and not planned.immigration_required
    unprepared = new_customs or new_immigration
    diff_country = alt_country.strip().upper() != destination_country.strip().upper()
    facility_gap = has_formality and not alt_is_poe

    # Reasons, all derived. Severity is the worst that fires.
    severities: list[str] = []
    if unprepared:
        # Absolute grade of the alternate's own formalities (decision: a full
        # third-country border is red even if only one axis is newly required).
        severities.append("red" if (alt.customs_required and alt.immigration_required) else "amber")
    if diff_country and has_formality and not unprepared:
        # Prepared for the border, but clearing into a different country than filed.
        severities.append("amber")
    if facility_gap:
        severities.append("amber")
    if not severities:
        return None
    severity = "red" if "red" in severities else "amber"

    origin_name = _country_name(origin_country)
    dest_name = _country_name(destination_country)
    alt_name = _country_name(alt_country)
    formalities = _formalities_phrase(alt.immigration_required, alt.customs_required)

    if unprepared:
        lead = (
            f"Diverting to this field in {alt_name} is an unplanned international "
            f"arrival from {origin_name} — {formalities}."
        )
    elif diff_country:
        lead = (
            f"Diverting here clears you into {alt_name}, not {dest_name} as filed — "
            f"{formalities}, but into a different country than your flight plan "
            f"named (notify the authorities in {alt_name})."
        )
    else:  # facility gap only: same country you filed for, but not a listed POE
        lead = f"Arriving here from {origin_name} requires border clearance — {formalities}."

    return OperationalFlag(
        code=CROSS_BORDER_CODE,
        label="Cross-border",
        detail=f"{lead}{_poe_note(alt_is_poe)}",
        severity=severity,
    )
