"""Import every ``euro_aip`` module weatherbrief uses, once, on one thread.

weatherbrief imports ``euro_aip`` lazily, inside functions. On a fresh process
the first imports therefore race across whichever background threads run
first, and ``euro_aip.briefing`` has an import cycle that two threads can take
in opposite order: ``briefing/__init__`` eagerly imports ``sources.*``, which
needs ``briefing.weather.parser``, while a thread already inside
``briefing.weather`` needs the parent package. Python then raises
``_DeadlockError`` in one thread and a ``KeyError`` in the other (#680).

Calling :func:`warm_euro_aip_imports` from the app lifespan and from the
``weatherbrief.verify`` CLI (whose standalone cycle runs thread pools), before
any other thread starts, makes every later lazy import a ``sys.modules``
lookup. The long-term fix belongs in euro_aip (stop eagerly
importing ``sources.*`` from ``briefing/__init__``); this stays useful as a
guard against the next cycle either way.

``tests/test_euro_aip_imports.py`` fails when a new ``euro_aip`` import in
``src/weatherbrief`` isn't covered by :data:`EURO_AIP_MODULES`.
"""

from __future__ import annotations

import importlib
import logging
import time

logger = logging.getLogger(__name__)

# Package first: its __init__ walks the briefing cycle (sources -> weather)
# in the one order, on this thread.
EURO_AIP_MODULES: tuple[str, ...] = (
    "euro_aip",
    "euro_aip.briefing",
    "euro_aip.briefing.models.flight_exchange",
    "euro_aip.briefing.models.route",
    "euro_aip.briefing.sources.autorouter_gramet",
    "euro_aip.briefing.sources.avwx",
    "euro_aip.briefing.weather.analysis",
    "euro_aip.briefing.weather.models",
    "euro_aip.briefing.weather.parser",
    "euro_aip.briefing.weather.route_sigmet",
    "euro_aip.briefing.weather.route_weather",
    "euro_aip.borders",
    "euro_aip.models.field15",
    "euro_aip.models.navpoint",
    "euro_aip.models.route_resolver",
    "euro_aip.storage.database_storage",
    "euro_aip.utils.autorouter_credentials",
    "euro_aip.utils.country_mapper",
    "euro_aip.utils.dms_parser",
    "euro_aip.utils.geometry",
    "euro_aip.utils.solar",
)


def warm_euro_aip_imports() -> None:
    """Import :data:`EURO_AIP_MODULES` on the calling thread.

    Best effort: a module that fails to import is logged and skipped, so
    startup behaves no worse than before — the lazy import at the call site
    will raise the same error where it always did.
    """
    started = time.monotonic()
    failed = []
    for name in EURO_AIP_MODULES:
        try:
            importlib.import_module(name)
        except Exception:
            failed.append(name)
            logger.exception("euro_aip import warm-up failed for %s", name)
    logger.info(
        "euro_aip import warm-up: %d/%d modules in %.2fs",
        len(EURO_AIP_MODULES) - len(failed),
        len(EURO_AIP_MODULES),
        time.monotonic() - started,
    )
