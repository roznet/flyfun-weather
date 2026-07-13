"""Central declaration of the engine grading-method defaults (#403).

The three engine methods — icing / cloud / convective — had **no declared
default**. When a profile omitted them, ``tasks/advise.py::_resolve_analyses``
fell through falsy checks to DD icing / DD cloud / thermo convective, while the
settings page has been *displaying* ``ogimet_nwp`` / ``nwp`` / ``nwp`` as its
placeholders. A never-saved profile therefore graded on something different from
what its own UI claimed.

This constant is the single source of truth: it is resolved from ``None`` at
evaluation time (so absence means "follow the default") and exposed to the client
via the advisory-catalog endpoint, so the UI default and the runtime default can
never drift again.

**#410 split** — the cloud grading axis is now ``cloud_source`` (``"dd"`` /
``"nwp"``), a pure grading choice, split from the render *style*
(natural/soft/square) which is a client-only concern (``vizSettings.cloudStyle``
in localStorage). The old ``cloud_method`` fused the two into a
``<style>_<source>`` string, which meant the settings-page "Cloud Style" select
wrote a value no renderer read and the backend threw the style away. A fresh key
name (rather than reusing ``cloud_method`` with bare ``dd``/``nwp`` values) keeps
the profile-level migration unambiguous — the bare form is exactly what the
account-level ``001_method_defaults_v2`` rewrites ``dd → square_nwp``, silently
flipping the source.
"""

from __future__ import annotations

#: The engine grading-method defaults, keyed by the profile-settings field name.
#: ``cloud_source`` is a bare ``"dd"`` / ``"nwp"`` grading choice; the render
#: style is a frontend concern and no longer travels with it (#410).
ENGINE_METHOD_DEFAULTS: dict[str, str] = {
    "icing_method": "ogimet_nwp",
    "cloud_source": "nwp",
    "convective_method": "nwp",
}


def legacy_cloud_source(cloud_method: str | None) -> str | None:
    """Reduce a legacy ``cloud_method`` string to its bare source (#410).

    Profiles written before the #410 split persist a combined
    ``<style>_<source>`` string (e.g. ``soft_nwp``, ``square_dd``,
    ``natural_nwp``) or the even older bare source (``dd`` / ``nwp``). The render
    *style* is a client-only concern; backend cloud resolution only needs the
    source. Any value ending in ``nwp`` selects the NWP source; every other
    non-empty value is a DD form. ``None`` / ``""`` → ``None`` ("no stored
    choice").
    """
    if not cloud_method:
        return None
    return "nwp" if cloud_method.endswith("nwp") else "dd"


def cloud_source_from_settings(settings: dict) -> str | None:
    """Resolve a profile's cloud source, honouring the legacy ``cloud_method``.

    The read-path fallback that lets the #410 profile migration run *after*
    deploy rather than as a flag day: prefer the new ``cloud_source`` key, and
    fall back to reducing a legacy ``cloud_method`` to its source. Returns
    ``None`` when the profile stores neither (→ follow
    :data:`ENGINE_METHOD_DEFAULTS`).
    """
    source = settings.get("cloud_source")
    if source is not None:
        return source
    return legacy_cloud_source(settings.get("cloud_method"))
