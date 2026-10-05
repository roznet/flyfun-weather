"""euro_aip import warm-up (#680): coverage of the module list, and the race."""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

from weatherbrief.euro_aip_imports import EURO_AIP_MODULES, warm_euro_aip_imports

SRC = Path(__file__).resolve().parents[1] / "src" / "weatherbrief"


def _euro_aip_imports_in_src() -> dict[str, list[str]]:
    """Map every euro_aip module imported under src/weatherbrief to its files."""
    found: dict[str, list[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                if name == "euro_aip" or name.startswith("euro_aip."):
                    found.setdefault(name, []).append(str(path.relative_to(SRC)))
    return found


def test_every_euro_aip_import_is_warmed():
    """A new lazy euro_aip import must be added to EURO_AIP_MODULES."""
    imported = _euro_aip_imports_in_src()
    assert imported, "scan found no euro_aip imports — SRC path is wrong"
    missing = {m: files for m, files in imported.items() if m not in EURO_AIP_MODULES}
    assert not missing, f"add to EURO_AIP_MODULES: {missing}"


def test_warm_list_has_no_stale_entries():
    """Every listed module still exists and is still used by weatherbrief."""
    stale = set(EURO_AIP_MODULES) - set(_euro_aip_imports_in_src())
    assert not stale, f"no longer imported by weatherbrief: {sorted(stale)}"
    warm_euro_aip_imports()
    assert all(m in sys.modules for m in EURO_AIP_MODULES)


_RACE = textwrap.dedent(
    """
    import threading
    from weatherbrief.euro_aip_imports import warm_euro_aip_imports
    warm_euro_aip_imports()

    barrier = threading.Barrier(2)
    errors = []

    def run(stmt):
        barrier.wait()
        try:
            exec(stmt, {})
        except BaseException as exc:
            errors.append(repr(exc))

    threads = [
        threading.Thread(target=run, args=(
            "from euro_aip.models.route_resolver import RouteResolver",)),
        threading.Thread(target=run, args=(
            "from euro_aip.briefing.weather.route_weather import RouteWeatherService",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("ERRORS:" + "|".join(errors) if errors else "OK")
    """
)


def test_warmed_imports_survive_cold_thread_race():
    """The #680 race, in a fresh interpreter, after the warm-up.

    Without the warm-up this exact script fails every time on euro_aip 0.19.0
    (``_DeadlockError`` in one thread, ``KeyError`` in the other).
    """
    result = subprocess.run(
        [sys.executable, "-c", _RACE], capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "OK", result.stdout
