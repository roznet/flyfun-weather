"""Central declaration of the engine grading-method defaults (#403).

The three engine methods — icing / cloud / convective — had **no declared
default**. When a profile omitted them, ``tasks/advise.py::_resolve_analyses``
fell through falsy checks to DD icing / DD cloud / thermo convective, while the
settings page has been *displaying* ``ogimet_nwp`` / ``square_nwp`` / ``nwp`` as
its placeholders. A never-saved profile therefore graded on something different
from what its own UI claimed.

This constant is the single source of truth: it is resolved from ``None`` at
evaluation time (so absence means "follow the default") and exposed to the client
via the advisory-catalog endpoint, so the UI default and the runtime default can
never drift again.

Values match the settings-page placeholders (and the GRAMET-aligned account-level
defaults in ``api/preferences.py`` / migration ``001_method_defaults_v2``), so
this is behaviour-preserving for every profile that carries explicit method keys.
The only profiles it changes are the ~6% with *no* explicit keys, which flip from
the old DD/DD/thermo fall-through to NWP — a fix, and the one intentional
behaviour change in #402.
"""

from __future__ import annotations

#: The engine grading-method defaults, keyed by the profile-settings field name.
#: ``cloud_method`` carries the composed ``<style>_<source>`` form the settings
#: page persists; only the ``*_nwp`` source is meaningful to backend resolution
#: (see ``_cloud_source_from_method``), the style is a frontend concern.
ENGINE_METHOD_DEFAULTS: dict[str, str] = {
    "icing_method": "ogimet_nwp",
    "cloud_method": "square_nwp",
    "convective_method": "nwp",
}
