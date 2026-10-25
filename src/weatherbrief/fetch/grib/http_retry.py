"""GET with retry for the GRIB downloaders (NOAA S3, DWD opendata).

One failed byte range used to drop a whole artifact: the GFS GRIB-primary
column (~177 MB per hour, #913) and the HRRR sounding are all-or-nothing, so
a single read timeout among hundreds of range requests cost that hour. This
retries the transient failures (network errors, read timeouts, a body cut
short, 429 and 5xx) a few times with backoff, like
``OpenMeteoClient._get_with_retry`` does for Open-Meteo. A 404 is an answer,
not a failure (DWD publishes steps late, NOAA files land one by one), so it
returns at once for the caller to handle as before.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Sleep before attempt 2, 3, ...: three attempts in all. Module-level so
# tests can zero it.
BACKOFF_S: tuple[float, ...] = (1.0, 2.0)


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """``session.get`` retried on transient failures; the body is read.

    Returns the last response, whatever its status, once it is not a
    retryable one (or the attempts run out on a retryable status). Raises
    the last ``requests.RequestException`` when every attempt failed on the
    network. The body is read inside the retry (``resp.content``), so a
    timeout or reset while streaming the body is retried too.
    """
    attempts = len(BACKOFF_S) + 1
    for attempt in range(attempts):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
            resp.content  # noqa: B018 — read the body inside the retry
        except requests.RequestException as exc:
            if attempt == attempts - 1:
                raise
            logger.info(
                "GRIB GET %s (%s), retry %d/%d: %s",
                type(exc).__name__, (headers or {}).get("Range", "whole file"),
                attempt + 1, attempts - 1, url.rsplit("/", 1)[-1],
            )
        else:
            if resp.status_code not in RETRYABLE_STATUS or attempt == attempts - 1:
                return resp
            logger.info(
                "GRIB GET HTTP %d (%s), retry %d/%d: %s",
                resp.status_code, (headers or {}).get("Range", "whole file"),
                attempt + 1, attempts - 1, url.rsplit("/", 1)[-1],
            )
        time.sleep(BACKOFF_S[attempt])
    raise AssertionError("unreachable")  # pragma: no cover
