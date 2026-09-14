"""Model run, lead and valid time of a DWD ICON forecast chart, read off the image.

DWD's hobbymet forecast charts print their timing in the bottom band:

    VT: 12 UTC Fr. 18 Sept. [ICON 2026-09-14 00 UTC + 108 h]

``VT`` is the valid time; the bracket holds the ICON run the chart was drawn
from and its lead. Nothing in the HTTP response carries the run, and the
cycle-dir key is not it either: that key is the *analysis* chart's
Last-Modified, which rolls through 12Z/18Z, while every forecast chart observed
(2026-09-09..14) came from the 00Z ICON run, published ~03:30-06:00 UTC.
Pairing the two put valid times 12-18h late.

Primary source: OCR the bracket with Tesseract. The ``VT:`` prefix is not
parsed — it carries no year and its hour reads as ``OO`` — so the valid time is
computed as run + lead. Fallback: the chart's own Last-Modified floored to 00Z,
which agreed with OCR on all 40 cached charts checked.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# The stamp line in the 800x653 ICON chart's bottom band. The crop stops short
# of the colour legend so its labels don't bleed into the text.
_BAND_BOX = (0, 607, 480, 628)
# At native size Tesseract reads ``00`` as ``OO``; a 3x upscale thresholded to
# pure black/white reads the bracket cleanly.
_UPSCALE = 3
_THRESHOLD = 140
_TESSERACT_CONFIG = "--psm 7"  # a single line of text

STAMP_RE = re.compile(
    r"\[\s*ICON\s+(\d{4}-\d{2}-\d{2})\s+(\d{2})\s*UTC\s*\+\s*(\d{1,3})\s*h\s*\]",
    re.IGNORECASE,
)

# Charts are published a few hours after their run. An OCR'd run later than
# the chart's Last-Modified, or further before it than this, is a misread.
_MAX_PUBLISH_LAG = timedelta(hours=48)

TIME_SOURCE_OCR = "ocr"
TIME_SOURCE_LAST_MODIFIED = "last_modified"

_tesseract_missing_logged = False


@dataclass(frozen=True)
class ForecastStamp:
    """When a forecast chart was run from and when it is valid."""

    init_time: datetime
    lead_h: int
    source: str  # TIME_SOURCE_OCR | TIME_SOURCE_LAST_MODIFIED

    @property
    def valid_time(self) -> datetime:
        return self.init_time + timedelta(hours=self.lead_h)

    def to_meta(self) -> dict[str, object]:
        """The fields stored on the chart's ``meta.json`` entry."""
        return {
            "init_time": _iso_z(self.init_time),
            "lead_h": self.lead_h,
            "valid_time": _iso_z(self.valid_time),
            "time_source": self.source,
        }

    @classmethod
    def from_meta(cls, entry: Mapping | None) -> ForecastStamp | None:
        """Inverse of :meth:`to_meta`; None for an entry written without a stamp."""
        if not entry:
            return None
        try:
            init = datetime.fromisoformat(entry["init_time"])
            lead_h = int(entry["lead_h"])
        except (KeyError, TypeError, ValueError):
            return None
        return cls(init_time=init, lead_h=lead_h, source=str(entry.get("time_source") or ""))


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_stamp_text(text: str | None) -> tuple[datetime, int] | None:
    """``(init_time, lead_h)`` from the stamp line, or None if it doesn't match."""
    if not text:
        return None
    m = STAMP_RE.search(text)
    if not m:
        return None
    try:
        init = datetime.strptime(f"{m[1]} {m[2]}", "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return init, int(m[3])


def ocr_stamp_text(path: Path) -> str | None:
    """OCR the chart's stamp band. None when Tesseract or the image is unavailable."""
    global _tesseract_missing_logged
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        if not _tesseract_missing_logged:
            logger.warning("pytesseract not installed; DWD forecast times fall back to Last-Modified")
            _tesseract_missing_logged = True
        return None

    try:
        with Image.open(path) as im:
            band = im.convert("L").crop(_BAND_BOX)
    except (OSError, ValueError) as e:
        logger.debug("DWD chart stamp: cannot open %s: %s", path, e)
        return None
    band = band.resize((band.width * _UPSCALE, band.height * _UPSCALE), Image.LANCZOS)
    band = band.point(lambda p: 0 if p < _THRESHOLD else 255)

    try:
        return pytesseract.image_to_string(band, config=_TESSERACT_CONFIG)
    except pytesseract.TesseractNotFoundError:
        if not _tesseract_missing_logged:
            logger.warning("tesseract binary not found; DWD forecast times fall back to Last-Modified")
            _tesseract_missing_logged = True
        return None
    except Exception as e:  # noqa: BLE001 — any OCR failure means "use the fallback"
        logger.warning("DWD chart stamp OCR failed for %s: %s", path, e)
        return None


def stamp_from_last_modified(last_modified: datetime, lead_h: int) -> ForecastStamp:
    """Assume the 00Z run of the day the chart was published."""
    init = last_modified.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return ForecastStamp(init_time=init, lead_h=lead_h, source=TIME_SOURCE_LAST_MODIFIED)


def _plausible_run(init: datetime, last_modified: datetime | None) -> bool:
    if init.hour % 6:
        return False
    if last_modified is None:
        return True
    return timedelta(0) <= last_modified - init <= _MAX_PUBLISH_LAG


def read_forecast_stamp(
    path: Path,
    *,
    lead_h: int,
    last_modified: datetime | None,
) -> ForecastStamp | None:
    """The chart's run and lead: OCR first, then its Last-Modified.

    ``lead_h`` is the offset the file name claims. When the image disagrees the
    image wins — it is what the pilot is looking at. None only when OCR fails
    and there is no Last-Modified to fall back on.
    """
    text = ocr_stamp_text(path)
    parsed = parse_stamp_text(text)
    if parsed is not None:
        init, image_lead_h = parsed
        if _plausible_run(init, last_modified):
            if image_lead_h != lead_h:
                logger.warning(
                    "DWD chart %s: image says +%dh, file name says +%dh; using the image",
                    path.name, image_lead_h, lead_h,
                )
            return ForecastStamp(init_time=init, lead_h=image_lead_h, source=TIME_SOURCE_OCR)
        logger.warning(
            "DWD chart %s: OCR'd run %s is implausible against Last-Modified %s; falling back",
            path.name, _iso_z(init), last_modified,
        )
    elif text is not None:
        logger.warning("DWD chart %s: no run stamp in OCR text %r", path.name, text.strip()[:120])

    if last_modified is None:
        return None
    return stamp_from_last_modified(last_modified, lead_h)
