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
    "evaluation_failed": {
        "en": "Evaluation failed ({error})",
        "fr": "Échec de l'évaluation ({error})",
        "de": "Auswertung fehlgeschlagen ({error})",
        "es": "Fallo en la evaluación ({error})",
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
    # Inline suffix appended to the per-airport part when terminal-area
    # convective risk is MODERATE or worse.
    "flight_category.conv": {
        "en": ", convective {risk} nearby",
        "fr": ", convectif {risk} à proximité",
        "de": ", Konvektion {risk} in der Nähe",
        "es": ", convectivo {risk} cercano",
    },

    # --- convective ---
    # Headline anchors on MODERATE+ extent (actual convective concern) and names
    # the peak separately, so the severity word (peak) and the extent (MODERATE+
    # coverage) are never conflated into one number (#300). "MODERATE+" is kept
    # as a literal threshold label across locales — like the {peak}/{risk} enum
    # values, which are intentionally not localized.
    "convective.risk_over_mod": {
        "en": "MODERATE+ over {extent} — peak {peak}",
        "fr": "MODERATE+ sur {extent} — pic {peak}",
        "de": "MODERATE+ über {extent} — Spitze {peak}",
        "es": "MODERATE+ sobre {extent} — pico {peak}",
    },
    "convective.risk_over_mod_pct": {
        "en": "MODERATE+ over {pct}% — peak {peak}",
        "fr": "MODERATE+ sur {pct}% — pic {peak}",
        "de": "MODERATE+ über {pct}% — Spitze {peak}",
        "es": "MODERATE+ sobre {pct}% — pico {peak}",
    },
    "convective.risk_over_range": {
        "en": "MODERATE+ over {min}–{max}% across models — peak {peak}",
        "fr": "MODERATE+ sur {min}–{max}% selon les modèles — pic {peak}",
        "de": "MODERATE+ über {min}–{max}% je nach Modell — Spitze {peak}",
        "es": "MODERATE+ sobre {min}–{max}% entre modelos — pico {peak}",
    },
    # LOW-only case, scheme ACTIVELY precipitating: the model's own convective
    # precip is above the firing floor, but its tier is capped below MODERATE
    # because the nwp_precip ladder can't infer depth from rate. Distinct from
    # `convective.favorability` below, which means the opposite (CAPE present,
    # scheme quiet) — using that wording here told a pilot the hardest-raining
    # model on the route was "not firing" (EGTF→LFQA 2026-07-17).
    "convective.realized_low": {
        "en": "Convective showers firing, tops unresolved, over {extent}",
        "fr": "Averses convectives actives, sommets non résolus, sur {extent}",
        "de": "Konvektive Schauer aktiv, Obergrenzen unbestimmt, über {extent}",
        "es": "Chubascos convectivos activos, topes sin resolver, sobre {extent}",
    },
    "convective.realized_low_pct": {
        "en": "Convective showers firing, tops unresolved, over {pct}%",
        "fr": "Averses convectives actives, sommets non résolus, sur {pct}%",
        "de": "Konvektive Schauer aktiv, Obergrenzen unbestimmt, über {pct}%",
        "es": "Chubascos convectivos activos, topes sin resolver, sobre {pct}%",
    },
    "convective.realized_low_range": {
        "en": "Convective showers firing, tops unresolved, over {min}–{max}% across models",
        "fr": "Averses convectives actives, sommets non résolus, sur {min}–{max}% selon les modèles",
        "de": "Konvektive Schauer aktiv, Obergrenzen unbestimmt, über {min}–{max}% je nach Modell",
        "es": "Chubascos convectivos activos, topes sin resolver, sobre {min}–{max}% entre modelos",
    },
    # LOW-only case: CAPE is present (primed) but no MODERATE+ firing — worded so
    # it can't masquerade as active convection (#300).
    "convective.favorability": {
        "en": "Low-end CAPE primed, not firing, over {extent}",
        "fr": "CAPE de bas niveau amorcée, sans déclenchement, sur {extent}",
        "de": "CAPE im unteren Bereich vorhanden, nicht ausgelöst, über {extent}",
        "es": "CAPE de gama baja preparada, sin disparo, sobre {extent}",
    },
    "convective.favorability_pct": {
        "en": "Low-end CAPE primed, not firing, over {pct}%",
        "fr": "CAPE de bas niveau amorcée, sans déclenchement, sur {pct}%",
        "de": "CAPE im unteren Bereich vorhanden, nicht ausgelöst, über {pct}%",
        "es": "CAPE de gama baja preparada, sin disparo, sobre {pct}%",
    },
    "convective.favorability_range": {
        "en": "Low-end CAPE primed, not firing, over {min}–{max}% across models",
        "fr": "CAPE de bas niveau amorcée, sans déclenchement, sur {min}–{max}% selon les modèles",
        "de": "CAPE im unteren Bereich vorhanden, nicht ausgelöst, über {min}–{max}% je nach Modell",
        "es": "CAPE de gama baja preparada, sin disparo, sobre {min}–{max}% entre modelos",
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

    # --- convective_character (VFR avoidability — issue #294) ---
    "convective_character.none": {
        "en": "No significant convective character",
        "fr": "Pas de caractère convectif significatif",
        "de": "Kein signifikanter Konvektionscharakter",
        "es": "Sin carácter convectivo significativo",
    },
    "convective_character.isolated": {
        "en": "Isolated cells — circumnavigable VFR with see-and-avoid, expect possible diversions",
        "fr": "Cellules isolées — contournables en VFR à vue, déroutements possibles",
        "de": "Isolierte Zellen — VFR umfliegbar mit Sicht, mögliche Ausweichmanöver",
        "es": "Células aisladas — sorteables en VFR con ver-y-evitar, posibles desvíos",
    },
    "convective_character.scattered": {
        "en": "Scattered cells — VFR possible but committing; deviations likely",
        "fr": "Cellules éparses — VFR possible mais engageant ; déviations probables",
        "de": "Vereinzelte Zellen — VFR möglich, aber anspruchsvoll; Abweichungen wahrscheinlich",
        "es": "Células dispersas — VFR posible pero comprometido; desviaciones probables",
    },
    "convective_character.widespread": {
        "en": "Widespread convection — no reliable gaps for VFR",
        "fr": "Convection étendue — pas de trouées fiables pour le VFR",
        "de": "Verbreitete Konvektion — keine verlässlichen Lücken für VFR",
        "es": "Convección generalizada — sin huecos fiables para VFR",
    },
    "convective_character.organized": {
        "en": "Organized/frontal convection — VFR impractical",
        "fr": "Convection organisée/frontale — VFR impraticable",
        "de": "Organisierte/frontale Konvektion — VFR nicht praktikabel",
        "es": "Convección organizada/frontal — VFR impracticable",
    },
    "convective_character.embedded": {
        "en": "Embedded convection — cells hidden in cloud, VFR impractical",
        "fr": "Convection noyée — cellules masquées dans les nuages, VFR impraticable",
        "de": "Eingelagerte Konvektion — Zellen in Bewölkung verborgen, VFR nicht praktikabel",
        "es": "Convección embebida — células ocultas en nubes, VFR impracticable",
    },
    "convective_character.unknown": {
        "en": "Convective character indeterminate",
        "fr": "Caractère convectif indéterminé",
        "de": "Konvektionscharakter unbestimmt",
        "es": "Carácter convectivo indeterminado",
    },
    # --- convective_character altitude mitigation (#568) ---
    # Advice only — never changes the grade. Both directions restore see-and-avoid
    # and must read differently: below the deck you see the cells from underneath,
    # above it you see the buildups that penetrate the layer. Climbing is
    # legitimate even when tops are far above the offered level — seeing them is
    # the point, not out-topping them.
    "convective_character.mitigation.climb": {
        "en": "Climbing to {alt:,} ft would put you on top of the deck — buildups visible and circumnavigable",
        "fr": "Monter à {alt:,} ft placerait au-dessus de la couche — bourgeonnements visibles et contournables",
        "de": "Ein Steigflug auf {alt:,} ft läge über der Schicht — Aufquellungen sichtbar und umfliegbar",
        "es": "Ascender a {alt:,} ft situaría sobre la capa — desarrollos visibles y sorteables",
    },
    "convective_character.mitigation.descend": {
        "en": "Descending to {alt:,} ft would put you below the deck — see-and-avoid under the cells",
        "fr": "Descendre à {alt:,} ft placerait sous la couche — vol à vue sous les cellules",
        "de": "Ein Sinkflug auf {alt:,} ft läge unter der Schicht — Sicht auf die Zellen von unten",
        "es": "Descender a {alt:,} ft situaría bajo la capa — ver-y-evitar bajo las células",
    },
    # --- convective_character below-base clearance (#298) ---
    # Annotate-only notes appended to ISOLATED/SCATTERED. Below-base is "more
    # circumnavigable" (see-and-avoid under the cells in VMC), NOT a vertical out
    # the way overflying the tops is — precip shafts / gust fronts live below base.
    "convective_character.depth_unresolved": {
        "en": "cell depth unresolved — below-base clearance not assessable",
        "fr": "profondeur des cellules indéterminée — marge sous la base non évaluable",
        "de": "Zelltiefe unbestimmt — Abstand unter der Basis nicht beurteilbar",
        "es": "profundidad de las células indeterminada — margen bajo la base no evaluable",
    },
    "convective_character.cruise_within_layer": {
        "en": "cruise within the convective layer — not avoidable from below",
        "fr": "croisière dans la couche convective — non contournable par en dessous",
        "de": "Reiseflug in der Konvektionsschicht — von unten nicht umfliegbar",
        "es": "crucero dentro de la capa convectiva — no sorteable por debajo",
    },
    "convective_character.deck_below_cells": {
        "en": "cells above a BKN/OVC layer — below-base see-and-avoid not available",
        "fr": "cellules au-dessus d'une couche BKN/OVC — pas de vol à vue sous la base",
        "de": "Zellen über einer BKN/OVC-Schicht — Sicht-Ausweichen unter der Basis nicht möglich",
        "es": "células sobre una capa BKN/OVC — sin ver-y-evitar bajo la base",
    },
    "convective_character.below_base_clear": {
        "en": "bases ~FL{fl}, ~{margin} ft above cruise — comfortable see-and-avoid room below",
        "fr": "bases ~FL{fl}, ~{margin} ft au-dessus de la croisière — marge confortable pour le vol à vue dessous",
        "de": "Basen ~FL{fl}, ~{margin} ft über Reiseflug — komfortabler Sicht-Ausweichraum darunter",
        "es": "bases ~FL{fl}, ~{margin} ft sobre el crucero — margen cómodo para ver-y-evitar debajo",
    },
    "convective_character.below_base_marginal": {
        "en": "bases ~FL{fl}, ~{margin} ft above cruise (marginal) — more avoidable ~{drop} ft lower",
        "fr": "bases ~FL{fl}, ~{margin} ft au-dessus de la croisière (marginal) — plus contournable ~{drop} ft plus bas",
        "de": "Basen ~FL{fl}, ~{margin} ft über Reiseflug (grenzwertig) — besser umfliegbar ~{drop} ft tiefer",
        "es": "bases ~FL{fl}, ~{margin} ft sobre el crucero (marginal) — más sorteable ~{drop} ft más abajo",
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
    "mountain_wind.wave_confirmed": {
        "en": "Mountain wave conditions ({speed}kt near terrain, {signature}) over {extent}",
        "fr": "Conditions d'onde de montagne ({speed}kt près du relief, {signature}) sur {extent}",
        "de": "Gebirgswellenbedingungen ({speed}kt nahe Gelände, {signature}) über {extent}",
        "es": "Condiciones de onda de montaña ({speed}kt cerca del terreno, {signature}) sobre {extent}",
    },
    # Names the extent's denominator: this advisory measures coverage over the
    # route's mountain points, not the whole route (#571 D3).
    "mountain_wind.of_terrain": {
        "en": "of high terrain",
        "fr": "de relief élevé",
        "de": "des Hochgeländes",
        "es": "de relieve elevado",
    },
    # Inline suffix listing the wave signature(s) seen at strong-wind points.
    "mountain_wind.sig_suffix": {
        "en": " — {signature}",
        "fr": " — {signature}",
        "de": " — {signature}",
        "es": " — {signature}",
    },
    "mountain_wind.sig_inversion": {
        "en": "stable layer at ridge top",
        "fr": "couche stable au sommet des crêtes",
        "de": "stabile Schicht am Kammniveau",
        "es": "capa estable en la cima",
    },
    "mountain_wind.sig_oscillating": {
        "en": "wave-like vertical motion",
        "fr": "mouvement vertical ondulatoire",
        "de": "wellenartige Vertikalbewegung",
        "es": "movimiento vertical ondulatorio",
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
    "icing_escape.mitigation.descend": {
        "en": "Descend to {alt} ft (below the freezing level) to stay clear of icing",
        "fr": "Descendre à {alt} ft (sous l'isotherme 0°C) pour éviter le givrage",
        "de": "Auf {alt} ft sinken (unter die Frostgrenze), um Vereisung zu meiden",
        "es": "Descender a {alt} ft (bajo la isoterma 0°C) para evitar el engelamiento",
    },
    "icing_escape.mitigation.climb": {
        "en": "Climb to {alt} ft (on top) to stay clear of icing",
        "fr": "Monter à {alt} ft (au-dessus) pour éviter le givrage",
        "de": "Auf {alt} ft steigen (obenauf), um Vereisung zu meiden",
        "es": "Ascender a {alt} ft (por encima) para evitar el engelamiento",
    },
    "icing_escape.mitigation.profile": {
        "en": "A climb over then descent below the icing keeps you clear (see profile)",
        "fr": "Une montée au-dessus puis descente sous le givrage vous maintient à l'écart (voir profil)",
        "de": "Ein Steigen über und anschließendes Sinken unter die Vereisung hält Sie frei (siehe Profil)",
        "es": "Ascender por encima y luego descender bajo el engelamiento lo mantiene libre (ver perfil)",
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
    "vfr.corridor_climb": {
        "en": "{cov} layer in climb-out at {icao}",
        "fr": "Couche {cov} en montée à {icao}",
        "de": "{cov}-Schicht im Steigflug bei {icao}",
        "es": "Capa {cov} en ascenso en {icao}",
    },
    "vfr.corridor_descent": {
        "en": "{cov} layer in descent at {icao}",
        "fr": "Couche {cov} en descente à {icao}",
        "de": "{cov}-Schicht im Sinkflug bei {icao}",
        "es": "Capa {cov} en descenso en {icao}",
    },
    "vfr.mitigation.altitude": {
        "en": "VMC available at {alt:,} ft",
        "fr": "VMC possible à {alt:,} ft",
        "de": "VMC verfügbar auf {alt:,} ft",
        "es": "VMC disponible a {alt:,} ft",
    },
    "vfr.mitigation.altitude_marginal": {
        # `cruise_imc` always scans downward from planned cruise, so this altitude is a
        # fly-lower target — "around" reads as an option to act on, not a flat condition
        # (#342 copy). Parallel to the GREEN `vfr.mitigation.altitude` ("VMC available at …").
        "en": "Marginal VMC around {alt:,} ft",
        "fr": "VMC marginale autour de {alt:,} ft",
        "de": "Marginale VMC um {alt:,} ft",
        "es": "VMC marginal en torno a {alt:,} ft",
    },
    "vfr.mitigation.climb_after": {
        "en": "VMC climb to cruise possible after ~{dist} nm from departure",
        "fr": "Montée VMC vers la croisière possible après ~{dist} nm du départ",
        "de": "VMC-Steigflug auf Reiseflughöhe ab ~{dist} nm nach dem Start möglich",
        "es": "Ascenso VMC a crucero posible tras ~{dist} nm desde la salida",
    },
    "vfr.mitigation.descend_before": {
        "en": "VMC descent possible until ~{dist} nm before arrival",
        "fr": "Descente VMC possible jusqu'à ~{dist} nm avant l'arrivée",
        "de": "VMC-Sinkflug bis ~{dist} nm vor der Ankunft möglich",
        "es": "Descenso VMC posible hasta ~{dist} nm antes de la llegada",
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
    # Character EMBEDDED escalated the convective axis (§22): the deck hides the
    # cells, so the see-and-avoid that makes them tolerable VFR is unavailable.
    "ifr.conv_embedded": {
        "en": "{risk} convective over {extent} — embedded in cloud, no visual avoidance",
        "fr": "Convectif {risk} sur {extent} — noyé dans les nuages, pas d'évitement à vue",
        "de": "{risk} Konvektion über {extent} — eingebettet in Bewölkung, keine Sichtumfliegung",
        "es": "Convectivo {risk} sobre {extent} — embebido en nubes, sin evitación visual",
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
    "fronts.tail.convective": {
        "en": " — convective tops to FL{top}, expect build-ups / deviation",
        "fr": " — sommets convectifs au FL{top}, prévoir des développements / déroutements",
        "de": " — konvektive Obergrenzen bis FL{top}, mit Aufbauten / Ausweichen rechnen",
        "es": " — topes convectivos a FL{top}, prever desarrollos / desvíos",
    },
    "fronts.benign": {
        "en": "Air-mass boundary on route, but little active weather on it",
        "fr": "Limite de masse d'air sur la route, mais peu de météo active",
        "de": "Luftmassengrenze auf der Strecke, aber kaum aktives Wetter",
        "es": "Límite de masa de aire en la ruta, pero con poco tiempo activo",
    },

    # --- enroute_precip ---
    "enroute_precip.clear": {
        "en": "No precipitation along route",
        "fr": "Pas de précipitations le long de la route",
        "de": "Kein Niederschlag entlang der Route",
        "es": "Sin precipitación a lo largo de la ruta",
    },
    "enroute_precip.snow": {
        "en": "Snow over {extent} — expect visibility to collapse in showers",
        "fr": "Neige sur {extent} — visibilité s'effondrant dans les averses",
        "de": "Schnee über {extent} — Sicht bricht in Schauern zusammen",
        "es": "Nieve sobre {extent} — visibilidad colapsa en chubascos",
    },
    "enroute_precip.rain": {
        "en": "Moderate+ rain over {extent}",
        "fr": "Pluie modérée ou plus sur {extent}",
        "de": "Mäßiger+ Regen über {extent}",
        "es": "Lluvia moderada o más sobre {extent}",
    },
    "enroute_precip.light": {
        "en": "Light precipitation over {extent}",
        "fr": "Précipitations faibles sur {extent}",
        "de": "Leichter Niederschlag über {extent}",
        "es": "Precipitación ligera sobre {extent}",
    },

    # --- headwind ---
    "headwind.summary": {
        "en": "Avg {mean}kt headwind (max {max}kt) — about +{delta} min vs still air ({pct}%)",
        "fr": "Vent de face moyen {mean}kt (max {max}kt) — environ +{delta} min vs air calme ({pct}%)",
        "de": "Mittlerer Gegenwind {mean}kt (max {max}kt) — etwa +{delta} Min. ggü. ruhiger Luft ({pct}%)",
        "es": "Viento en contra medio {mean}kt (máx {max}kt) — aprox +{delta} min vs aire en calma ({pct}%)",
    },
    "headwind.tailwind": {
        "en": "Avg {mean}kt tailwind — about {delta} min saved",
        "fr": "Vent arrière moyen {mean}kt — environ {delta} min gagnées",
        "de": "Mittlerer Rückenwind {mean}kt — etwa {delta} Min. gespart",
        "es": "Viento en cola medio {mean}kt — aprox {delta} min ahorrados",
    },
    "headwind.neutral": {
        "en": "Light winds aloft — negligible trip impact",
        "fr": "Vents faibles en altitude — impact négligeable sur le trajet",
        "de": "Schwache Höhenwinde — vernachlässigbarer Einfluss",
        "es": "Vientos débiles en altura — impacto insignificante",
    },

    # --- approach_feasibility (#509) ---
    # Phrasing stays in the "directs attention" register — it names the
    # geometry ("wind favours 24, the ILS serves 06") and never issues a
    # usability verdict. Runway designators and approach classes are aviation
    # abbreviations and stay untranslated.
    "approach_feasibility.vfr": {
        "en": "VFR — reachable visually, approach not a factor",
        "fr": "VFR — accessible à vue, l'approche n'est pas un facteur",
        "de": "VFR — visuell erreichbar, Anflug nicht entscheidend",
        "es": "VFR — alcanzable visualmente, la aproximación no es un factor",
    },
    "approach_feasibility.mvfr_ok": {
        "en": "{count} published approach(es) — landing can still be completed visually",
        "fr": "{count} approche(s) publiée(s) — l'atterrissage reste réalisable à vue",
        "de": "{count} veröffentlichte Anflüge — Landung noch visuell möglich",
        "es": "{count} aproximación(es) publicada(s) — el aterrizaje aún puede completarse visualmente",
    },
    "approach_feasibility.mvfr_no_iap": {
        "en": "No published instrument approach — arrival depends on staying visual",
        "fr": "Aucune approche aux instruments publiée — l'arrivée dépend du maintien du vol à vue",
        "de": "Kein veröffentlichter Instrumentenanflug — Ankunft hängt vom Sichtflug ab",
        "es": "Sin aproximación instrumental publicada — la llegada depende de mantener el vuelo visual",
    },
    # --- user-declared unpublished approaches (#510) ---
    # Every one of these names the basis in the line itself: the grade is being
    # softened on the pilot's own assertion, and #510 guardrail 2 makes saying
    # so mandatory — a shared briefing renders the owner's baked-in grades, so
    # this sentence is the only thing that travels with it.
    "approach_feasibility.mvfr_declared": {
        "en": "Your declared unpublished approach at {icao} — landing can still be completed visually",
        "fr": "Votre approche non publiée déclarée à {icao} — l'atterrissage reste réalisable à vue",
        "de": "Ihr angegebener nicht veröffentlichter Anflug in {icao} — Landung noch visuell möglich",
        "es": "Su aproximación no publicada declarada en {icao} — el aterrizaje aún puede completarse visualmente",
    },
    "approach_feasibility.declared_only": {
        "en": "No published instrument approach — graded against your declared unpublished approach at {icao}, which has no published minima, so check your own",
        "fr": "Aucune approche aux instruments publiée — évalué d'après votre approche non publiée déclarée à {icao}, sans minima publiés : vérifiez les vôtres",
        "de": "Kein veröffentlichter Instrumentenanflug — bewertet anhand Ihres angegebenen nicht veröffentlichten Anflugs in {icao}, der keine veröffentlichten Minima hat; prüfen Sie Ihre eigenen",
        "es": "Sin aproximación instrumental publicada — evaluado con su aproximación no publicada declarada en {icao}, que no tiene mínimos publicados: verifique los suyos",
    },
    "approach_feasibility.declared_lifr": {
        "en": "No published instrument approach — your declared unpublished approach at {icao} cannot be credited at LIFR, as it has no published minima to fly to",
        "fr": "Aucune approche aux instruments publiée — votre approche non publiée déclarée à {icao} ne peut être créditée en LIFR, faute de minima publiés",
        "de": "Kein veröffentlichter Instrumentenanflug — Ihr angegebener nicht veröffentlichter Anflug in {icao} kann bei LIFR nicht angerechnet werden, da keine veröffentlichten Minima vorliegen",
        "es": "Sin aproximación instrumental publicada — su aproximación no publicada declarada en {icao} no puede acreditarse en LIFR, al no tener mínimos publicados",
    },
    "approach_feasibility.soften_declared": {
        "en": "you declared an unpublished approach here, so check your own minima",
        "fr": "vous avez déclaré une approche non publiée ici, vérifiez vos propres minima",
        "de": "Sie haben hier einen nicht veröffentlichten Anflug angegeben, prüfen Sie Ihre eigenen Minima",
        "es": "usted declaró una aproximación no publicada aquí, verifique sus propios mínimos",
    },
    "approach_feasibility.no_iap": {
        "en": "No published instrument approach at {icao}",
        "fr": "Aucune approche aux instruments publiée à {icao}",
        "de": "Kein veröffentlichter Instrumentenanflug in {icao}",
        "es": "Sin aproximación instrumental publicada en {icao}",
    },
    "approach_feasibility.no_procedure_data": {
        "en": "No approach procedure data for {icao}",
        "fr": "Aucune donnée de procédure d'approche pour {icao}",
        "de": "Keine Anflugverfahrensdaten für {icao}",
        "es": "Sin datos de procedimientos de aproximación para {icao}",
    },
    "approach_feasibility.below_minima": {
        "en": "Ceiling {ceiling}ft is below the best-case decision height (~{dh}ft) for any approach here",
        "fr": "Plafond {ceiling}ft sous la hauteur de décision la plus favorable (~{dh}ft) de toute approche ici",
        "de": "Untergrenze {ceiling}ft unter der günstigsten Entscheidungshöhe (~{dh}ft) jedes Anflugs hier",
        "es": "Techo {ceiling}ft por debajo de la altura de decisión más favorable (~{dh}ft) de cualquier aproximación aquí",
    },
    "approach_feasibility.below_vis_minima": {
        "en": "Visibility {vis}m is below the best-case minimum (~{min_vis}m) for any approach here",
        "fr": "Visibilité {vis}m sous le minimum le plus favorable (~{min_vis}m) de toute approche ici",
        "de": "Sicht {vis}m unter dem günstigsten Minimum (~{min_vis}m) jedes Anflugs hier",
        "es": "Visibilidad {vis}m por debajo del mínimo más favorable (~{min_vis}m) de cualquier aproximación aquí",
    },
    "approach_feasibility.straight_in": {
        "en": "{approach} serves RWY {runway} — {wind}",
        "fr": "{approach} dessert la RWY {runway} — {wind}",
        "de": "{approach} bedient RWY {runway} — {wind}",
        "es": "{approach} sirve la RWY {runway} — {wind}",
    },
    "approach_feasibility.headwind": {
        "en": "{kt}kt headwind on that runway",
        "fr": "{kt}kt de vent de face sur cette piste",
        "de": "{kt}kt Gegenwind auf dieser Bahn",
        "es": "{kt}kt de viento en contra en esa pista",
    },
    "approach_feasibility.tailwind": {
        "en": "{kt}kt tailwind on that runway",
        "fr": "{kt}kt de vent arrière sur cette piste",
        "de": "{kt}kt Rückenwind auf dieser Bahn",
        "es": "{kt}kt de viento en cola en esa pista",
    },
    # The no-usable-straight-in verdict is a CROSS PRODUCT — what blocks the
    # arrival ("blocked_*") x why it is not RED ("soften_*") — composed as
    # "{cause} — {clause}" by ``_fallback_detail``. Three review rounds on #511
    # each found a different cell of it rendering the wrong sentence while the
    # cells were written out by hand; composing them means a new blocker or a
    # new softening reason costs ONE string, not a whole row or column, and no
    # cell can silently borrow another's wording.
    "approach_feasibility.blocked_minima": {
        "en": "RWY {runway} is clear on wind, but the forecast is below typical {approach} minima (~{dh}ft)",
        "fr": "La RWY {runway} est bonne au vent, mais la prévision est sous les minima {approach} typiques (~{dh}ft)",
        "de": "RWY {runway} ist windseitig frei, aber die Vorhersage liegt unter typischen {approach}-Minima (~{dh}ft)",
        "es": "La RWY {runway} está bien de viento, pero el pronóstico está por debajo de los mínimos {approach} típicos (~{dh}ft)",
    },
    "approach_feasibility.blocked_misaligned": {
        "en": "Wind favours RWY {wind_runway}; approaches serve {served}",
        "fr": "Le vent favorise la RWY {wind_runway} ; les approches desservent {served}",
        "de": "Wind begünstigt RWY {wind_runway}; Anflüge bedienen {served}",
        "es": "El viento favorece la RWY {wind_runway}; las aproximaciones sirven {served}",
    },
    "approach_feasibility.blocked_no_straight_in": {
        "en": "No straight-in approach is published here; wind favours RWY {wind_runway}",
        "fr": "Aucune approche directe publiée ici ; le vent favorise la RWY {wind_runway}",
        "de": "Kein Geradeausanflug hier veröffentlicht; Wind begünstigt RWY {wind_runway}",
        "es": "No hay aproximación directa publicada aquí; el viento favorece la RWY {wind_runway}",
    },
    "approach_feasibility.soften_circling": {
        "en": "plan for circling",
        "fr": "prévoir une MVL",
        "de": "Platzrunde einplanen",
        "es": "prever circling",
    },
    "approach_feasibility.soften_unresolved": {
        "en": "an approach here could not be matched to a runway, so check the plates",
        "fr": "une approche ici n'a pu être rattachée à une piste, vérifier les cartes",
        "de": "ein Anflug hier konnte keiner Bahn zugeordnet werden, Karten prüfen",
        "es": "una aproximación aquí no pudo asociarse a una pista, revisar las cartas",
    },
    "approach_feasibility.soften_no_wind_data": {
        "en": "no wind components for the approach-served runway, so this could not be checked",
        "fr": "pas de composantes de vent pour la piste desservie, vérification impossible",
        "de": "keine Windkomponenten für die angeflogene Bahn, nicht prüfbar",
        "es": "sin componentes de viento para la pista servida, no se pudo comprobar",
    },
    "approach_feasibility.soften_none": {
        "en": "conditions will not support circling",
        "fr": "les conditions ne permettent pas la MVL",
        "de": "die Bedingungen tragen keine Platzrunde",
        "es": "las condiciones no permiten circling",
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

    # --- sun ---
    "sun.no_data": {
        "en": "No sun analysis available",
        "fr": "Pas d'analyse solaire disponible",
        "de": "Keine Sonnenanalyse verfügbar",
        "es": "Sin análisis solar disponible",
    },
    "sun.side_note": {
        "en": "Sun on your {side} for ~{pct}% of the sunlit route",
        "fr": "Soleil à {side} sur ~{pct}% de la route éclairée",
        "de": "Sonne {side} auf ~{pct}% der besonnten Strecke",
        "es": "Sol a su {side} en ~{pct}% de la ruta iluminada",
    },
    "sun.side_none": {
        "en": "Sun stays below the horizon for the whole route — no sun side",
        "fr": "Le soleil reste sous l'horizon toute la route — pas de côté marqué",
        "de": "Sonne bleibt die ganze Strecke unter dem Horizont — keine Sonnenseite",
        "es": "El sol permanece bajo el horizonte toda la ruta — sin lado marcado",
    },
    "sun.ahead_note": {
        "en": "Flying into the sun for ~{pct}% of the sunlit route",
        "fr": "Face au soleil sur ~{pct}% de la route éclairée",
        "de": "Gegen die Sonne auf ~{pct}% der besonnten Strecke",
        "es": "De cara al sol en ~{pct}% de la ruta iluminada",
    },
    "sun.behind_note": {
        "en": "Sun behind you for ~{pct}% of the sunlit route",
        "fr": "Soleil dans le dos sur ~{pct}% de la route éclairée",
        "de": "Sonne im Rücken auf ~{pct}% der besonnten Strecke",
        "es": "Sol a su espalda en ~{pct}% de la ruta iluminada",
    },
    "sun.left": {"en": "left", "fr": "gauche", "de": "links", "es": "izquierda"},
    "sun.right": {"en": "right", "fr": "droite", "de": "rechts", "es": "derecha"},
    "sun.glare_takeoff": {
        "en": "Low sun (~{elev}° up) nearly down RWY {runway} on takeoff — expect glare on the roll",
        "fr": "Soleil bas (~{elev}° de hauteur) presque dans l'axe de la RWY {runway} au décollage — éblouissement probable au roulage",
        "de": "Tiefe Sonne (~{elev}° hoch) fast in Achse der RWY {runway} beim Start — Blendung beim Startlauf erwarten",
        "es": "Sol bajo (~{elev}° de altura) casi en el eje de la RWY {runway} al despegue — deslumbramiento probable en la carrera",
    },
    "sun.glare_landing": {
        "en": "Low sun (~{elev}° up) nearly down RWY {runway} on landing — expect glare on the flare",
        "fr": "Soleil bas (~{elev}° de hauteur) presque dans l'axe de la RWY {runway} à l'atterrissage — éblouissement probable à l'arrondi",
        "de": "Tiefe Sonne (~{elev}° hoch) fast in Achse der RWY {runway} bei der Landung — Blendung beim Abfangen erwarten",
        "es": "Sol bajo (~{elev}° de altura) casi en el eje de la RWY {runway} al aterrizar — deslumbramiento probable en la recogida",
    },
    "sun.near_sunset": {
        "en": "Landing {icao} is near or after sunset — plan for a low-light arrival",
        "fr": "Arrivée {icao} proche ou après le coucher du soleil — prévoir une arrivée en faible lumière",
        "de": "Landung {icao} nahe oder nach Sonnenuntergang — mit einer Ankunft bei wenig Licht rechnen",
        "es": "Aterrizaje en {icao} cerca o tras la puesta de sol — prever una llegada con poca luz",
    },
    "sun.near_sunrise": {
        "en": "Departing {icao} is near or before sunrise — plan for low light",
        "fr": "Départ {icao} proche ou avant le lever du soleil — prévoir une faible lumière",
        "de": "Abflug {icao} nahe oder vor Sonnenaufgang — mit wenig Licht rechnen",
        "es": "Salida de {icao} cerca o antes del amanecer — prever poca luz",
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
