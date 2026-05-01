"""Process resident-set-size helper, shared by pipeline + GRIB instrumentation."""

from __future__ import annotations

import os
import subprocess
import sys


def current_rss_mb() -> float | None:
    """Return current process RSS in MB, or None if unavailable.

    Linux: reads /proc/self/status (cheap, accurate). macOS: shells out to ps.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["ps", "-o", "rss=", "-p", str(os.getpid())], timeout=1,
            )
            return int(out.strip()) / 1024
        except Exception:
            return None
    return None
