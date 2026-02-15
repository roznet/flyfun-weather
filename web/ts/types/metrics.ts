/** Types for metrics catalog and display configuration. */

export type RiskLevel = 'none' | 'low' | 'moderate' | 'high' | 'severe';
export type Tier = 'key' | 'useful' | 'advanced';
export type DisplayMode = 'compact' | 'annotated';

export interface MetricThreshold {
  min: number | null;
  max: number | null;
  label: string;
  risk: RiskLevel;
  meaning: string;
}

export interface MetricCatalogEntry {
  name: string;
  unit: string;
  vibe: string;
  primary_goal: string;
  best_used_for: string;
  limitations: string;
  theory?: string;
  wikipedia?: string;
  llm_prompt?: string;
  thresholds: MetricThreshold[];
}

export type MetricCatalog = Record<string, MetricCatalogEntry>;

export interface ThresholdMatch {
  label: string;
  risk: RiskLevel;
  meaning: string;
}

// Display configuration types
export interface SectionMetric {
  id: string;
  tier: Tier;
  field?: string;
  source?: string;
}

export interface SectionConfig {
  label: string;
  metrics: SectionMetric[];
}

export interface MetricsDisplayConfig {
  sections: Record<string, SectionConfig>;
  tierDefaults: Record<Tier, boolean>;
}
