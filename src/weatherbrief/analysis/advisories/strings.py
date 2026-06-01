"""Translatable advisory detail strings.

Provides a lightweight ``adv_t(key, locale, **params)`` helper that
evaluators use to produce locale-aware detail text.  Templates use
Python ``str.format()`` style placeholders (``{extent}``, ``{speed}``).

Aviation abbreviations that are NEVER translated:
  VFR, IFR, MVFR, LIFR, IMC, VMC, OVC, BKN, CAT, SLD, FIKI,
  METAR, TAF, FL, kt, ft, nm, sm, %, runway designators, ICAO codes.
"""

from __future__ import annotations

# fmt: off
# ---- string catalog --------------------------------------------------
# Keys are organised by evaluator; shared strings are at the top.
# Each key maps locale codes to a template string.

_STRINGS: dict[str, dict[str, str]] = {

    # --- shared ---
    "no_data": {
        "en": "No data",
        "fr": "Pas de données",
        "de": "Keine Daten",
        "es": "Sin datos",
    },

    # --- turbulence ---
    "turbulence.smooth": {
        "en": "Smooth ride expected",
        "fr": "Vol calme prévu",
        "de": "Ruhiger Flug erwartet",
        "es": "Vuelo tranquilo previsto",
    },
    "turbulence.severe_over": {
        "en": "Severe CAT over {extent}",
        "fr": "CAT sévère sur {extent}",
        "de": "Schwere CAT über {extent}",
        "es": "CAT severa sobre {extent}",
    },
    "turbulence.risk_over": {
        "en": "{risk} over {extent}",
        "fr": "{risk} sur {extent}",
        "de": "{risk} über {extent}",
        "es": "{risk} sobre {extent}",
    },

    # --- flight_category ---
    # Uses "Dep {icao}: {cat}" pattern — Dep/Arr kept as compact labels

    # --- convective ---
    "convective.risk_over": {
        "en": "{risk} convective risk over {extent}",
        "fr": "Risque convectif {risk} sur {extent}",
        "de": "{risk} Konvektionsrisiko über {extent}",
        "es": "Riesgo convectivo {risk} sobre {extent}",
    },
    "convective.none": {
        "en": "No significant convective activity",
        "fr": "Pas d'activité convective significative",
        "de": "Keine signifikante Konvektion",
        "es": "Sin actividad convectiva significativa",
    },
    "convective.below_cruise": {
        "en": "Convective tops below cruise altitude ({count} points)",
        "fr": "Sommets convectifs sous l'altitude de croisière ({count} points)",
        "de": "Konvektionsobergrenzen unter Reiseflughöhe ({count} Punkte)",
        "es": "Topes convectivos bajo altitud de crucero ({count} puntos)",
    },

    # --- airport_wind ---
    "airport_wind.calm": {
        "en": "calm",
        "fr": "calme",
        "de": "windstill",
        "es": "calma",
    },

    # --- mountain_wind ---
    "mountain_wind.no_terrain": {
        "en": "No significant terrain along route",
        "fr": "Pas de relief significatif le long de la route",
        "de": "Kein signifikantes Gelände entlang der Route",
        "es": "Sin relieve significativo a lo largo de la ruta",
    },
    "mountain_wind.severe": {
        "en": "Severe mountain wind ({speed}kt near terrain) over {extent}",
        "fr": "Vent de montagne sévère ({speed}kt près du relief) sur {extent}",
        "de": "Schwerer Bergwind ({speed}kt nahe Gelände) über {extent}",
        "es": "Viento de montaña severo ({speed}kt cerca del terreno) sobre {extent}",
    },
    "mountain_wind.wave_risk": {
        "en": "Mountain wave risk ({speed}kt near terrain) over {extent}",
        "fr": "Risque d'onde de montagne ({speed}kt près du relief) sur {extent}",
        "de": "Gebirgswellenrisiko ({speed}kt nahe Gelände) über {extent}",
        "es": "Riesgo onda de montaña ({speed}kt cerca del terreno) sobre {extent}",
    },
    "mountain_wind.light": {
        "en": "Light winds near terrain ({speed}kt)",
        "fr": "Vents faibles près du relief ({speed}kt)",
        "de": "Schwache Winde nahe Gelände ({speed}kt)",
        "es": "Vientos débiles cerca del terreno ({speed}kt)",
    },

    # --- cloud_top ---
    "cloud_top.reachable": {
        "en": "Cloud tops reachable (max {top}ft, ceiling {ceiling}ft)",
        "fr": "Sommets nuageux accessibles (max {top}ft, plafond {ceiling}ft)",
        "de": "Wolkenobergrenzen erreichbar (max {top}ft, Decke {ceiling}ft)",
        "es": "Topes de nubes accesibles (máx {top}ft, techo {ceiling}ft)",
    },
    "cloud_top.no_layers": {
        "en": "No significant cloud layers",
        "fr": "Pas de couches nuageuses significatives",
        "de": "Keine signifikanten Wolkenschichten",
        "es": "Sin capas de nubes significativas",
    },
    "cloud_top.above_ceiling": {
        "en": "Cloud tops above ceiling over {extent} (max {top}ft)",
        "fr": "Sommets nuageux au-dessus du plafond sur {extent} (max {top}ft)",
        "de": "Wolkenobergrenzen über Decke auf {extent} (max {top}ft)",
        "es": "Topes de nubes sobre techo en {extent} (máx {top}ft)",
    },

    # --- fiki_icing ---
    "fiki.sld_risk": {
        "en": "SLD risk ({where})",
        "fr": "Risque SLD ({where})",
        "de": "SLD-Risiko ({where})",
        "es": "Riesgo SLD ({where})",
    },
    "fiki.severe_icing": {
        "en": "severe icing ({where})",
        "fr": "givrage sévère ({where})",
        "de": "schwere Vereisung ({where})",
        "es": "engelamiento severo ({where})",
    },
    "fiki.transit": {
        "en": "transit: {parts}",
        "fr": "transit : {parts}",
        "de": "Transit: {parts}",
        "es": "tránsito: {parts}",
    },
    "fiki.dep_transit": {
        "en": "dep {thickness}ft",
        "fr": "dép {thickness}ft",
        "de": "Abfl {thickness}ft",
        "es": "sal {thickness}ft",
    },
    "fiki.arr_transit": {
        "en": "arr {thickness}ft",
        "fr": "arr {thickness}ft",
        "de": "Ank {thickness}ft",
        "es": "lleg {thickness}ft",
    },
    "fiki.cruise_clear": {
        "en": "cruise {pct}% clear",
        "fr": "croisière {pct}% dégagé",
        "de": "Reiseflug {pct}% klar",
        "es": "crucero {pct}% despejado",
    },
    "fiki.no_icing": {
        "en": "No icing along route",
        "fr": "Pas de givrage le long de la route",
        "de": "Keine Vereisung entlang der Route",
        "es": "Sin engelamiento a lo largo de la ruta",
    },

    # --- icing_escape ---
    "icing_escape.no_escape": {
        "en": "Icing over {extent}; no warm escape at {count} points",
        "fr": "Givrage sur {extent} ; pas d'échappée chaude à {count} points",
        "de": "Vereisung über {extent}; kein warmer Ausweg an {count} Punkten",
        "es": "Engelamiento sobre {extent}; sin escape cálido en {count} puntos",
    },
    "icing_escape.no_icing": {
        "en": "No icing along route",
        "fr": "Pas de givrage le long de la route",
        "de": "Keine Vereisung entlang der Route",
        "es": "Sin engelamiento a lo largo de la ruta",
    },
    "icing_escape.tight_margin": {
        "en": "Icing over {extent}, tight escape margin",
        "fr": "Givrage sur {extent}, marge d'échappée serrée",
        "de": "Vereisung über {extent}, knappe Fluchtmarge",
        "es": "Engelamiento sobre {extent}, margen de escape ajustado",
    },
    "icing_escape.warm_escape": {
        "en": "Icing over {extent}, warm escape available",
        "fr": "Givrage sur {extent}, échappée chaude disponible",
        "de": "Vereisung über {extent}, warmer Ausweg verfügbar",
        "es": "Engelamiento sobre {extent}, escape cálido disponible",
    },

    # --- vmc_cruise ---
    "vmc_cruise.ovc": {
        "en": "OVC at cruise over {extent}",
        "fr": "OVC en croisière sur {extent}",
        "de": "OVC auf Reiseflughöhe über {extent}",
        "es": "OVC en crucero sobre {extent}",
    },
    "vmc_cruise.imc": {
        "en": "IMC at cruise over {extent}",
        "fr": "IMC en croisière sur {extent}",
        "de": "IMC auf Reiseflughöhe über {extent}",
        "es": "IMC en crucero sobre {extent}",
    },
    "vmc_cruise.mostly_clear": {
        "en": "Mostly clear at cruise, IMC over {extent}",
        "fr": "Majoritairement dégagé en croisière, IMC sur {extent}",
        "de": "Überwiegend klar auf Reiseflughöhe, IMC über {extent}",
        "es": "Mayormente despejado en crucero, IMC sobre {extent}",
    },
    "vmc_cruise.clear": {
        "en": "Clear at cruise altitude",
        "fr": "Dégagé à l'altitude de croisière",
        "de": "Klar auf Reiseflughöhe",
        "es": "Despejado a altitud de crucero",
    },

    # --- model_agreement ---
    "model_agreement.no_data": {
        "en": "No model comparison data",
        "fr": "Pas de données de comparaison de modèles",
        "de": "Keine Modellvergleichsdaten",
        "es": "Sin datos de comparación de modelos",
    },
    "model_agreement.good": {
        "en": "Good agreement across all models",
        "fr": "Bon accord entre tous les modèles",
        "de": "Gute Übereinstimmung aller Modelle",
        "es": "Buena concordancia entre todos los modelos",
    },
    "model_agreement.mostly_good": {
        "en": "Mostly good agreement, moderate divergence over {extent}",
        "fr": "Accord majoritairement bon, divergence modérée sur {extent}",
        "de": "Überwiegend gute Übereinstimmung, moderate Abweichung über {extent}",
        "es": "Concordancia mayormente buena, divergencia moderada sobre {extent}",
    },
    "model_agreement.poor": {
        "en": "Poor model agreement over {extent}",
        "fr": "Mauvais accord des modèles sur {extent}",
        "de": "Schlechte Modellübereinstimmung über {extent}",
        "es": "Mala concordancia de modelos sobre {extent}",
    },

    # --- vfr_feasibility ---
    "vfr.imc_over": {
        "en": "IMC over {extent}",
        "fr": "IMC sur {extent}",
        "de": "IMC über {extent}",
        "es": "IMC sobre {extent}",
    },
    "vfr.imc_marginal": {
        "en": "IMC/marginal clearance over {extent}",
        "fr": "IMC/dégagement marginal sur {extent}",
        "de": "IMC/marginaler Abstand über {extent}",
        "es": "IMC/separación marginal sobre {extent}",
    },
    "vfr.marginal": {
        "en": "Marginal cloud clearance over {extent}",
        "fr": "Dégagement nuageux marginal sur {extent}",
        "de": "Marginaler Wolkenabstand über {extent}",
        "es": "Separación marginal de nubes sobre {extent}",
    },
    "vfr.minor": {
        "en": "Minor clearance issues over {extent}",
        "fr": "Problèmes mineurs de dégagement sur {extent}",
        "de": "Geringfügige Abstandsprobleme über {extent}",
        "es": "Problemas menores de separación sobre {extent}",
    },
    "vfr.throughout": {
        "en": "VFR conditions throughout",
        "fr": "Conditions VFR sur tout le trajet",
        "de": "VFR-Bedingungen auf gesamter Strecke",
        "es": "Condiciones VFR en toda la ruta",
    },
    "vfr.airports_ok": {
        "en": "Airports VFR, no en-route data",
        "fr": "Aéroports VFR, pas de données en route",
        "de": "Flughäfen VFR, keine Streckendaten",
        "es": "Aeropuertos VFR, sin datos en ruta",
    },

    # --- ifr_feasibility ---
    "ifr.lifr_below_min": {
        "en": "{label} {icao} LIFR ceiling {ceiling}ft < {min}ft min",
        "fr": "{label} {icao} LIFR plafond {ceiling}ft < {min}ft min",
        "de": "{label} {icao} LIFR Decke {ceiling}ft < {min}ft min",
        "es": "{label} {icao} LIFR techo {ceiling}ft < {min}ft mín",
    },
    "ifr.lifr": {
        "en": "{label} {icao} LIFR",
        "fr": "{label} {icao} LIFR",
        "de": "{label} {icao} LIFR",
        "es": "{label} {icao} LIFR",
    },
    "ifr.icing_over": {
        "en": "Icing over {extent}",
        "fr": "Givrage sur {extent}",
        "de": "Vereisung über {extent}",
        "es": "Engelamiento sobre {extent}",
    },
    "ifr.conv_over": {
        "en": "{risk} convective over {extent}",
        "fr": "Convectif {risk} sur {extent}",
        "de": "{risk} Konvektion über {extent}",
        "es": "Convectivo {risk} sobre {extent}",
    },
    "ifr.acceptable": {
        "en": "IFR conditions acceptable throughout",
        "fr": "Conditions IFR acceptables sur tout le trajet",
        "de": "IFR-Bedingungen auf gesamter Strecke akzeptabel",
        "es": "Condiciones IFR aceptables en toda la ruta",
    },

    # --- fronts (experimental Hewson front detection, #196) ---
    "fronts.none": {
        "en": "No active fronts along route",
        "fr": "Aucun front actif sur le trajet",
        "de": "Keine aktiven Fronten entlang der Strecke",
        "es": "Sin frentes activos en la ruta",
    },
    "fronts.kind.cold": {
        "en": "cold front",
        "fr": "front froid",
        "de": "Kaltfront",
        "es": "frente frío",
    },
    "fronts.kind.warm": {
        "en": "warm front",
        "fr": "front chaud",
        "de": "Warmfront",
        "es": "frente cálido",
    },
    "fronts.kind.quasi": {
        "en": "quasi-stationary front",
        "fr": "front quasi stationnaire",
        "de": "quasistationäre Front",
        "es": "frente cuasiestacionario",
    },
    "fronts.where.early": {
        "en": "early on the route",
        "fr": "en début de trajet",
        "de": "früh auf der Strecke",
        "es": "al principio de la ruta",
    },
    "fronts.where.mid": {
        "en": "mid-route",
        "fr": "à mi-parcours",
        "de": "auf halber Strecke",
        "es": "a mitad de ruta",
    },
    "fronts.where.late": {
        "en": "late on the route",
        "fr": "en fin de trajet",
        "de": "spät auf der Strecke",
        "es": "al final de la ruta",
    },
    # NOTE: this is an inline prefix spliced before the kind noun in
    # ``fronts.crossing{,_multi}`` ("{prefix}{kind} {where}"), so each value
    # MUST carry its own trailing separator (space / "colon space"). Empty
    # prefix (non-sharp) yields just "{kind} {where}".
    "fronts.sharp": {
        "en": "sharp ",
        "fr": "marqué : ",
        "de": "scharfe ",
        "es": "marcado: ",
    },
    "fronts.crossing": {
        "en": "{prefix}{kind} {where}",
        "fr": "{prefix}{kind} {where}",
        "de": "{prefix}{kind} {where}",
        "es": "{prefix}{kind} {where}",
    },
    "fronts.crossing_multi": {
        "en": "{count} fronts along route — worst: {prefix}{kind} {where}",
        "fr": "{count} fronts sur le trajet — le plus marqué : {prefix}{kind} {where}",
        "de": "{count} Fronten entlang der Strecke — stärkste: {prefix}{kind} {where}",
        "es": "{count} frentes en la ruta — el más fuerte: {prefix}{kind} {where}",
    },
    "fronts.tail.deteriorating": {
        "en": " — deteriorating conditions likely",
        "fr": " — conditions susceptibles de se dégrader",
        "de": " — Verschlechterung wahrscheinlich",
        "es": " — probable deterioro de las condiciones",
    },
    "fronts.tail.improving": {
        "en": " — improving conditions",
        "fr": " — amélioration des conditions",
        "de": " — Wetterbesserung",
        "es": " — mejora de las condiciones",
    },
    "fronts.closing": {
        "en": "Front {dist} km off-track and closing",
        "fr": "Front à {dist} km hors trajet et se rapprochant",
        "de": "Front {dist} km abseits der Strecke und näher kommend",
        "es": "Frente a {dist} km fuera de ruta y acercándose",
    },

    # --- shared airport labels ---
    "airport.dep": {
        "en": "Dep",
        "fr": "Dép",
        "de": "Abfl",
        "es": "Sal",
    },
    "airport.arr": {
        "en": "Arr",
        "fr": "Arr",
        "de": "Ank",
        "es": "Lleg",
    },
}
# fmt: on


def adv_t(key: str, locale: str | None = None, **params: str | int | float) -> str:
    """Translate an advisory detail string.

    Falls back to English if the locale is missing or the key has no
    translation for the requested locale.  Falls back to the raw key
    if neither English nor the requested locale has an entry.
    """
    locale = locale or "en"
    templates = _STRINGS.get(key)
    if templates is None:
        return key
    template = templates.get(locale, templates.get("en", key))
    if params:
        return template.format(**params)
    return template
