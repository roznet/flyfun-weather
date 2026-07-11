import { t } from '../i18n/i18n';

export interface AdvisoryMethodLabel {
  short: string;
  description: string;
}

interface AdvisoryMethodDefinition {
  short: string;
  descriptionKey: string;
  fallbackDescription: string;
}

const METHOD_LABELS: Readonly<Record<string, AdvisoryMethodDefinition>> = {
  dewpoint_depression: {
    short: 'DD',
    descriptionKey: 'advisories.methods.dewpoint_depression',
    fallbackDescription: 'Dewpoint-depression cloud method',
  },
  nwp: {
    short: 'NWP',
    descriptionKey: 'advisories.methods.nwp',
    fallbackDescription: 'Model-native numerical weather prediction method',
  },
  nwp_synthesized: {
    short: 'NWP + DD envelope',
    descriptionKey: 'advisories.methods.nwp_synthesized',
    fallbackDescription: 'NWP cloud coverage constrained by a dewpoint-depression envelope',
  },
  nwp_precipitation_profile: {
    short: 'NWP precip',
    descriptionKey: 'advisories.methods.nwp_precipitation_profile',
    fallbackDescription: 'NWP precipitation phase and warm-nose profile',
  },
  ogimet_dd: {
    short: 'Ogimet-DD',
    descriptionKey: 'advisories.methods.ogimet_dd',
    fallbackDescription: 'Ogimet icing index with DD cloud signal',
  },
  ogimet_nwp: {
    short: 'Ogimet-NWP',
    descriptionKey: 'advisories.methods.ogimet_nwp',
    fallbackDescription: 'Ogimet icing index with NWP cloud signal',
  },
  sfip: {
    short: 'SFIP',
    descriptionKey: 'advisories.methods.sfip',
    fallbackDescription: 'Simplified Forecast Icing Potential',
  },
  ieng: {
    short: 'IENG',
    descriptionKey: 'advisories.methods.ieng',
    fallbackDescription: 'IENG icing method',
  },
  thermo: {
    short: 'Thermo',
    descriptionKey: 'advisories.methods.thermo',
    fallbackDescription: 'Thermodynamic convective method',
  },
  nwp_with_dd_floor: {
    short: 'NWP + DD floor',
    descriptionKey: 'advisories.methods.nwp_with_dd_floor',
    fallbackDescription: 'NWP result raised by the thermodynamic safety floor',
  },
  richardson_cat: {
    short: 'Ri CAT',
    descriptionKey: 'advisories.methods.richardson_cat',
    fallbackDescription: 'Richardson-number clear-air turbulence method',
  },
  vertical_motion: {
    short: 'Vertical motion',
    descriptionKey: 'advisories.methods.vertical_motion',
    fallbackDescription: 'Model vertical-motion trigger',
  },
  cat_with_vertical_motion: {
    short: 'CAT + motion',
    descriptionKey: 'advisories.methods.cat_with_vertical_motion',
    fallbackDescription: 'CAT and vertical-motion triggers both contributed',
  },
  terrain_wind: {
    short: 'Terrain wind',
    descriptionKey: 'advisories.methods.terrain_wind',
    fallbackDescription: 'Wind near significant terrain',
  },
  terrain_wind_wave: {
    short: 'Wind + wave',
    descriptionKey: 'advisories.methods.terrain_wind_wave',
    fallbackDescription: 'Terrain wind corroborated by a wave signature',
  },
  model_divergence: {
    short: 'Model spread',
    descriptionKey: 'advisories.methods.model_divergence',
    fallbackDescription: 'Cross-model divergence assessment',
  },
  dd_vs_nwp: {
    short: 'DD ↔ NWP',
    descriptionKey: 'advisories.methods.dd_vs_nwp',
    fallbackDescription: 'Within-model comparison of two derivations',
  },
  airport_conditions: {
    short: 'Airport NWP',
    descriptionKey: 'advisories.methods.airport_conditions',
    fallbackDescription: 'Forecast airport conditions',
  },
  runway_components: {
    short: 'Runway wind',
    descriptionKey: 'advisories.methods.runway_components',
    fallbackDescription: 'Runway-relative wind components',
  },
  density_altitude: {
    short: 'Density altitude',
    descriptionKey: 'advisories.methods.density_altitude',
    fallbackDescription: 'Forecast density-altitude calculation',
  },
  bulk_shear: {
    short: 'Bulk shear',
    descriptionKey: 'advisories.methods.bulk_shear',
    fallbackDescription: 'Low-level bulk wind shear',
  },
  hewson: {
    short: 'Hewson',
    descriptionKey: 'advisories.methods.hewson',
    fallbackDescription: 'Hewson frontal-boundary diagnostics',
  },
  vfr_composite: {
    short: 'VFR composite',
    descriptionKey: 'advisories.methods.vfr_composite',
    fallbackDescription: 'Multiple VFR feasibility methods tied for the controlling grade',
  },
  ifr_composite: {
    short: 'IFR composite',
    descriptionKey: 'advisories.methods.ifr_composite',
    fallbackDescription: 'Multiple IFR feasibility methods tied for the controlling grade',
  },
};

function hasOwn(record: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

export function advisoryMethodLabel(
  methodId: string | null | undefined,
): AdvisoryMethodLabel | null {
  if (!methodId) return null;
  const definition = hasOwn(METHOD_LABELS, methodId)
    ? METHOD_LABELS[methodId]
    : undefined;
  if (!definition) {
    console.warn(`Unknown advisory method_id: ${methodId}`);
    return null;
  }

  const translated = t(definition.descriptionKey);
  return {
    short: definition.short,
    description: translated === definition.descriptionKey
      ? definition.fallbackDescription
      : translated,
  };
}
