"""Which derived visibility method fills a model's ``visibility_m`` (#979 phase 3).

A model whose own visibility we no longer get (ICON on DWD's native-grid v1
feed, #974: DWD does not publish VIS there) gets one derived method instead,
chosen from the verification scores (``verify visibility-report --promotion``)
and recorded in meteorology-decisions §89. One method per model, never a mix
inside one model's slot, and only where the model's own visibility is missing:
a model that publishes one keeps it.

Changing the method is this one table. The experiment that picked it keeps
scoring every method against METARs, so a better one shows up there first.
"""

from __future__ import annotations

from typing import Any, Mapping

from weatherbrief.analysis.visibility.methods import VISIBILITY_FUNCS, VisibilityInputs

#: Model slot key → derived visibility method, where the model has no
#: visibility of its own. ``rh_icon``: chosen 2026-11-23 on 14 days, 361,902
#: scored hours (§89). It needs only 2 m T and Td, so it covers every ICON
#: hour; it does not see visibility lost to precipitation in unsaturated air
#: (``icon_blend`` does, but needs lowest-model-level inputs the briefing path
#: does not fetch). The same method fills briefings and snapshots (map, area
#: outlook, alternates), so the two never disagree on ICON's visibility.
DERIVED_VISIBILITY_METHOD: dict[str, str] = {
    "icon": "rh_icon",
}


def derived_visibility_method(model: str) -> str | None:
    """The method that stands in for *model*'s visibility, or None."""
    return DERIVED_VISIBILITY_METHOD.get(model)


def derived_visibility_m(model: str, fields: Mapping[str, Any] | object) -> float | None:
    """Derived visibility (m, rounded) for one hour of *model*, or None.

    *fields* is any mapping or object with :class:`VisibilityInputs` names
    (an hourly snapshot dict or row). None when the model has no selected method or the
    method's inputs are missing: the row then stays without visibility and
    grades on ceiling alone, as before.

    The briefing caller passes the step's instantaneous fields only (no
    precipitation rates, no lowest-model-level cloud water): a method put in
    :data:`DERIVED_VISIBILITY_METHOD` that needs those (e.g. ``icon_blend``)
    would return None everywhere in briefings until the caller supplies them.
    The snapshot caller passes the whole snapshot row, lowest-level columns
    included, so it would already compute ``icon_blend``.
    """
    method = derived_visibility_method(model)
    if method is None:
        return None
    inp = VisibilityInputs.from_snapshot(fields)
    # The derived value replaces a missing native one: never feed one in.
    inp.visibility_m = None
    value = VISIBILITY_FUNCS[method](inp)
    return None if value is None else float(round(value))
