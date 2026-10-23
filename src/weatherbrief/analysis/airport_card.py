"""The airport card's readings (#880): what the departure / arrival card draws,
decided once here so web, iOS and Android only lay it out.

Every helper is pure and reads fields the METAR/TAF table already has
(``AirportObservation``): no new threshold, no new meteorology.

- **Category meter**: where a ceiling or visibility sits on four equal bands
  LIFR | IFR | MVFR | VFR, with the band edges of
  ``analysis/airport_conditions`` (500 / 1,000 / 3,000 ft; 1 SM / 3 SM /
  8,000 m), piecewise-linear inside a band, the top band ending at 5,000 ft /
  10 km.
- **Sky text** ("BKN 1,200 ft", "CAVOK") read from the METAR body.
- **Present weather in words** ("-DZ" → "Light drizzle") with an icon kind
  the clients map to their own symbol set.
- **Short wind** ("290/15G25", "VRB 03", "Calm") and **visibility**
  ("10 km+", "6 km", "800 m").
- **Crosswind** on the best runway end, gust shown only past
  ``GUST_DISPLAY_MIN_EXCESS_KT`` (the rule every wind string already uses).
- **Trend**: the latest METAR's category against the report before it.
"""

from __future__ import annotations

import re

from weatherbrief.analysis.airport_conditions import (
    _CEIL_IFR,
    _CEIL_LIFR,
    _CEIL_MVFR,
    _VIS_IFR_M,
    _VIS_LIFR_M,
    _VIS_MVFR_M,
    GUST_DISPLAY_MIN_EXCESS_KT,
)

#: The meter's VFR band ends here; anything above sits at the right edge.
METER_CEILING_TOP_FT = 5000
METER_VISIBILITY_TOP_M = 10000

#: Best (VFR) first, as the classifier ranks them.
_CATEGORY_RANK = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


def _meter(value: float, edges: tuple[float, float, float], top: float) -> float:
    """``value`` on four equal bands [0, e1) [e1, e2) [e2, e3) [e3, top], 0..1."""
    bounds = (0.0, *edges, top)
    for i in range(4):
        lo, hi = bounds[i], bounds[i + 1]
        if value < hi:
            return round((i + (max(value, lo) - lo) / (hi - lo)) / 4.0, 3)
    return 1.0


def ceiling_meter(ceiling_ft: int | None, *, has_metar: bool) -> float | None:
    """The ceiling's place on the meter, 0 (LIFR edge) to 1 (VFR top). No
    ceiling in a METAR (CAVOK, FEW/SCT only) is the top; no METAR is None."""
    if not has_metar:
        return None
    if ceiling_ft is None:
        return 1.0
    return _meter(ceiling_ft, (_CEIL_LIFR, _CEIL_IFR, _CEIL_MVFR), METER_CEILING_TOP_FT)


def visibility_meter(visibility_m: int | None) -> float | None:
    """The visibility's place on the meter, None when not reported."""
    if visibility_m is None:
        return None
    return _meter(visibility_m, (_VIS_LIFR_M, _VIS_IFR_M, _VIS_MVFR_M), METER_VISIBILITY_TOP_M)


def visibility_text(visibility_m: int | None) -> str | None:
    """"10 km+" (9999 and up), "6 km" (5 km and up), else "800 m"."""
    if visibility_m is None:
        return None
    if visibility_m >= 9999:
        return "10 km+"
    if visibility_m >= 5000:
        return f"{round(visibility_m / 1000)} km"
    return f"{visibility_m:,} m"


def wind_short(direction: int | None, speed: int | None, gust: int | None) -> str | None:
    """The card's wind: "290/15G25", "VRB 03", "Calm". The reported gust is
    shown as reported (a METAR only reports one well above the mean)."""
    if speed is None:
        return None
    if speed == 0:
        return "Calm"
    g = f"G{gust:02d}" if gust else ""
    if direction is None:
        return f"VRB {speed:02d}{g}"
    return f"{direction:03d}/{speed:02d}{g}"


# --- Sky ----------------------------------------------------------------------

#: The METAR's trend / remarks start: nothing after it is the observation.
_TREND = re.compile(r"\s(?:TEMPO|BECMG|NOSIG|RMK)\b.*$", re.S)
_LAYER = re.compile(r"^(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?(?:/{3})?$")
_VV = re.compile(r"^VV(\d{3}|///)$")
_NO_CLOUD = {"NSC": "No significant cloud", "SKC": "Sky clear", "CLR": "Sky clear", "NCD": "No cloud detected"}


def _ft(hundreds: str) -> str:
    return f"{int(hundreds) * 100:,} ft"


def sky_text(metar_raw: str | None) -> tuple[str | None, bool]:
    """(the ceiling row's words, CAVOK) from the METAR body.

    The ceiling layer (lowest BKN/OVC, or the vertical visibility) when there
    is one: "BKN 1,200 ft", "OVC 300 ft CB", "VV 100 ft"; else the lowest
    layer ("SCT 2,500 ft"), "CAVOK" or the no-cloud code in words. None when
    the body says nothing about the sky.
    """
    if not metar_raw:
        return None, False
    body = _TREND.sub("", " ".join(metar_raw.split()))
    layers: list[tuple[str, str, str]] = []
    for group in body.split()[1:]:
        if group == "CAVOK":
            return "CAVOK", True
        if group in _NO_CLOUD:
            return _NO_CLOUD[group], False
        if m := _VV.match(group):
            return ("VV " + _ft(m.group(1)) if m.group(1) != "///" else "Sky obscured"), False
        if m := _LAYER.match(group):
            layers.append((m.group(1), m.group(2), m.group(3) or ""))
    if not layers:
        return None, False
    ceiling = next((layer for layer in layers if layer[0] in ("BKN", "OVC")), layers[0])
    cover, height, cloud = ceiling
    return f"{cover} {_ft(height)}" + (f" {cloud}" if cloud else ""), False


# --- Present weather ------------------------------------------------------------

_PHENOMENA = {
    "DZ": "drizzle", "RA": "rain", "SN": "snow", "SG": "snow grains", "IC": "ice crystals",
    "PL": "ice pellets", "GR": "hail", "GS": "small hail", "UP": "precipitation",
    "BR": "mist", "FG": "fog", "FU": "smoke", "VA": "volcanic ash", "DU": "dust", "SA": "sand",
    "HZ": "haze", "PY": "spray", "PO": "dust whirls", "SQ": "squalls", "FC": "funnel cloud",
    "SS": "sandstorm", "DS": "duststorm",
}
_PRECIP = ("DZ", "RA", "SN", "SG", "IC", "PL", "GR", "GS", "UP")
_DESCRIPTORS = ("MI", "BC", "PR", "DR", "BL", "SH", "TS", "FZ")
#: The icon kind, most telling first: the first that applies wins.
_ICON_ORDER = (
    ("thunder", ("TS",)), ("hail", ("GR", "GS")), ("snow", ("SN", "SG", "PL", "IC")),
    ("freezing", ("FZ",)), ("showers", ("SH",)), ("rain", ("RA", "UP")), ("drizzle", ("DZ",)),
    ("fog", ("FG",)), ("mist", ("BR",)), ("haze", ("HZ",)), ("smoke", ("FU", "VA")),
    ("dust", ("DU", "SA", "SS", "DS", "PO")), ("squall", ("SQ",)), ("funnel", ("FC",)),
)
_FOG_DESCRIPTOR = {"MI": "Shallow fog", "BC": "Fog patches", "PR": "Partial fog", "FZ": "Freezing fog"}


def _split(code: str) -> tuple[str, list[str], list[str]] | None:
    """(intensity "-"/"+"/"VC"/"", descriptors, phenomena), None if not a
    weather group."""
    rest = code
    intensity = ""
    if rest.startswith(("-", "+")):
        intensity, rest = rest[0], rest[1:]
    elif rest.startswith("VC"):
        intensity, rest = "VC", rest[2:]
    descriptors: list[str] = []
    while rest[:2] in _DESCRIPTORS:
        descriptors.append(rest[:2])
        rest = rest[2:]
    phenomena = [rest[i:i + 2] for i in range(0, len(rest), 2)]
    if len(rest) % 2 or any(p not in _PHENOMENA for p in phenomena) or not (descriptors or phenomena):
        return None
    return intensity, descriptors, phenomena


def _words(intensity: str, descriptors: list[str], phenomena: list[str]) -> str:
    near = intensity == "VC"
    strength = {"-": "light ", "+": "heavy "}.get(intensity, "")
    precip = [p for p in phenomena if p in _PRECIP]
    other = [p for p in phenomena if p not in _PRECIP]
    if "FC" in other and intensity == "+":
        return "Tornado"
    freezing = "FZ" in descriptors
    names = [("freezing " if freezing else "") + _PHENOMENA[p] for p in precip]
    precip_words = " and ".join(names)
    if "SH" in descriptors and precip:
        precip_words += " showers"
    if "TS" in descriptors:
        if near:
            return "Thunder nearby"
        return "Thunderstorm" + (f" with {strength}{precip_words}" if precip else "")
    if precip:
        text = (strength + precip_words).capitalize()
        if "BL" in descriptors:
            text = "Blowing " + precip_words
        elif "DR" in descriptors:
            text = "Drifting " + precip_words
        return text + (" nearby" if near else "")
    if "SH" in descriptors:
        return "Showers nearby" if near else "Showers"
    if other == ["FG"] and descriptors and descriptors[0] in _FOG_DESCRIPTOR:
        return _FOG_DESCRIPTOR[descriptors[0]]
    words = " and ".join(_PHENOMENA[p] for p in other)
    if "BL" in descriptors:
        words = "blowing " + words
    elif "DR" in descriptors:
        words = "drifting " + words
    text = (strength + words).capitalize()
    return text + (" nearby" if near else "")


def weather_words(codes: list[str]) -> list[dict]:
    """``[{"code": "-DZ", "text": "Light drizzle", "icon": "drizzle"}, …]``
    for the METAR's present weather. An unknown group keeps its code as the
    text with the ``"other"`` icon, never dropped."""
    out = []
    for code in codes:
        code = code.strip().upper()
        if not code:
            continue
        parts = _split(code)
        if parts is None:
            out.append({"code": code, "text": code, "icon": "other"})
            continue
        intensity, descriptors, phenomena = parts
        tokens = set(descriptors) | set(phenomena)
        icon = next((kind for kind, keys in _ICON_ORDER if tokens & set(keys)), "other")
        out.append({"code": code, "text": _words(intensity, descriptors, phenomena), "icon": icon})
    return out


# --- Crosswind and trend ----------------------------------------------------------


def crosswind_reading(picture, source: str = "metar") -> dict | None:
    """The card's crosswind on the best end of ``source``'s wind (``metar`` /
    ``taf``) from the runway picture: ``{"runway", "side", "kt", "gust_kt",
    "text"}`` with ``text`` "10G16 kt". ``side`` is where the wind comes
    from ("left" / "right" / "" when straight down the runway); the gust is
    kept only ``GUST_DISPLAY_MIN_EXCESS_KT`` above the steady component. None
    without a best end (VRB, calm, no runway data)."""
    if picture is None:
        return None
    sample = next((w for w in picture.winds if w.wind.source == source), None)
    if sample is None or sample.best_end is None or sample.wind.calm:
        return None
    end = next((e for e in sample.ends if e.ident == sample.best_end), None)
    if end is None:
        return None
    kt = round(abs(end.crosswind_kt))
    gust = round(abs(end.gust_crosswind_kt)) if end.gust_crosswind_kt is not None else None
    if gust is not None and gust - kt < GUST_DISPLAY_MIN_EXCESS_KT:
        gust = None
    side = end.side if kt > 0 or gust else ""
    return {
        "runway": end.ident, "side": side, "kt": kt, "gust_kt": gust,
        "text": f"{kt}" + (f"G{gust}" if gust is not None else "") + " kt",
    }


def metar_trend(now: str | None, before: str | None) -> str | None:
    """"better" / "worse" when the category moved since the report before,
    None when it did not or either is unknown."""
    a, b = _CATEGORY_RANK.get((now or "").upper()), _CATEGORY_RANK.get((before or "").upper())
    if a is None or b is None or a == b:
        return None
    return "better" if a < b else "worse"
