"""Visibility and fog derived from model fields (#979).

Phase 0 (this package today): the model-agnostic inputs the derivation
methods will need, recorded on every forecast snapshot so the methods can be
fitted and scored against METARs later, including against DWD's own ICON
visibility while it is still published (until 2026-11-30).
"""

from weatherbrief.analysis.visibility.inputs import (
    LOWEST_LEVEL_RAW_KEYS,
    VISIBILITY_INPUT_COLUMNS,
    icon_lowest_level_columns,
    level_850_columns,
    snapshot_input_columns,
)

__all__ = [
    "LOWEST_LEVEL_RAW_KEYS",
    "VISIBILITY_INPUT_COLUMNS",
    "icon_lowest_level_columns",
    "level_850_columns",
    "snapshot_input_columns",
]
