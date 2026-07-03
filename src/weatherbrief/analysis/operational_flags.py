"""Operational-friction flags for divert candidates (issue #344).

Pure logic that turns *non-weather* friction into an :class:`OperationalFlag`.
The first (and currently only) signal is **cross-border**: a divert field that
is weather-better and close can still carry an unplanned international arrival
(customs, immigration, GAR, PPR). "Different country" is too blunt — FR→BE is a
different country with essentially none of that friction, while FR→GB is a hard
customs + immigration border. Grading comes from the country-pair's membership
of the Schengen area (immigration) and the EU customs union (customs), which is
reference data owned by the library (``euro_aip.borders``) so it is not
re-encoded and drifted here.

Design notes:

- **Anchor on the destination's country.** The surprise the flag warns about is
  "planned to land in FR, instead cleared into GB", so severity is read from the
  destination→alternate pair.
- **Severity is purely the country-pair**, never the point-of-entry status:
  both formalities → red, exactly one → amber, neither → no flag.
- **Point-of-entry modulates the *message only*.** ``point_of_entry`` means the
  field handles both customs and immigration, but it is a *subset* of
  customs-capable fields — many airports clear customs without being flagged
  POE. So a non-POE field is an *uncertainty note* ("could not verify"), never a
  severity downgrade.
"""

from __future__ import annotations

from euro_aip.borders import crossing_requirements

from weatherbrief.models.alternates import OperationalFlag

CROSS_BORDER_CODE = "cross_border"


def _formalities_phrase(immigration: bool, customs: bool) -> str:
    """Plain-language description of which formalities the country-pair triggers."""
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
    dest_country: str | None,
    alt_country: str | None,
    alt_is_poe: bool,
) -> OperationalFlag | None:
    """Cross-border operational flag for diverting from ``dest_country`` to an
    alternate in ``alt_country``, or ``None`` when no border friction applies.

    Returns ``None`` when either country is unknown (nothing to compare), when
    the two are the same country, or when neither customs nor immigration is
    required. Otherwise a ``red`` flag (both formalities) or ``amber`` flag
    (exactly one). ``alt_is_poe`` only affects the ``detail`` wording.
    """
    if not dest_country or not alt_country:
        return None

    req = crossing_requirements(dest_country, alt_country)
    if not req.immigration_required and not req.customs_required:
        # Same country, or both blocs shared → no border friction.
        return None

    both = req.immigration_required and req.customs_required
    severity = "red" if both else "amber"
    label = "Cross-border" if both else "Cross-border (partial)"

    dest_cc = dest_country.strip().upper()
    alt_cc = alt_country.strip().upper()
    detail = (
        f"You planned to land in {dest_cc}; diverting to this {alt_cc} field is "
        f"an unplanned international arrival — {_formalities_phrase(req.immigration_required, req.customs_required)}."
        f"{_poe_note(alt_is_poe)}"
    )

    return OperationalFlag(
        code=CROSS_BORDER_CODE,
        label=label,
        detail=detail,
        severity=severity,
    )
