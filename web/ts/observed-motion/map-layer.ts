import * as L from 'leaflet';
import type { FeatureRecord, MotionTime, ObservedMotion } from './types';

type ProjectionPresentation = 'active' | 'stored' | 'hidden';

function hasValidatedGeolocation(value: Record<string, unknown>): boolean {
  return value.status === 'validated';
}

function latLngs(data: ObservedMotion, feature: FeatureRecord, time: MotionTime): L.LatLngExpression[][][] | null {
  if (time !== 'observed') {
    const projectionEnd = feature.projection_end_at === null ? Number.NaN : Date.parse(feature.projection_end_at);
    const at = Date.parse(time);
    const source = data.sources.find(item => item.source_id === feature.source_id);
    if (!data.projection_times.includes(time) || feature.motion.status !== 'accepted'
        || !hasValidatedGeolocation(feature.geolocation) || !source || !hasValidatedGeolocation(source.geolocation)
        || !Number.isFinite(at) || at <= Date.parse(data.cutoff_at) || at > projectionEnd) return null;
  }
  const projection = time === 'observed' ? null : feature.projections.find(item => item.at === time);
  if (projection?.status === 'unavailable') return null;
  const geometry = time === 'observed' ? feature.display_geometry : projection?.display_geometry;
  if (geometry?.status !== 'available' || !geometry.geometry) return null;
  return geometry.geometry.coordinates.map(polygon => polygon.map(ring =>
    ring.map(([longitude, latitude]) => [latitude, longitude] as L.LatLngTuple)));
}

function familyClass(feature: FeatureRecord): string {
  return feature.family === 'radar_echo' ? 'radar' : 'cloud';
}

function contourLabel(feature: FeatureRecord): string {
  return feature.family === 'radar_echo' ? 'Radar echo ≥ 5 dBZ' : 'High cloud top ≥ 15,000 ft MSL';
}

/** Independent weather overlay group. It never mutates route/raster layers and
 * only draws server-provided coordinates, times and measurements. */
export class ObservedMotionMapLayer {
  private data: ObservedMotion | null = null;
  private time: MotionTime = 'observed';
  private selected = new Set<string>();
  private enabled = { radar_echo: true, high_cloud_top: true };
  private presentation: ProjectionPresentation = 'hidden';
  private destroyed = false;
  private legend: HTMLElement;

  constructor(private group: L.LayerGroup, private host: HTMLElement) {
    this.legend = document.createElement('div');
    this.legend.className = 'observed-motion-map-legend';
    this.host.appendChild(this.legend);
  }

  setData(data: ObservedMotion | null): void {
    if (this.destroyed) return;
    this.data = data;
    this.render();
  }

  selectTime(time: MotionTime, presentation: ProjectionPresentation = 'active'): void {
    if (this.destroyed) return;
    this.time = time;
    this.presentation = presentation;
    this.render();
  }

  selectFeature(featureId: string | null): void {
    this.selectFeatures(featureId ? [featureId] : []);
  }

  selectFeatures(featureIds: string[]): void {
    if (this.destroyed) return;
    this.selected = new Set(featureIds);
    this.render();
  }

  setSourceEnabled(family: 'radar_echo' | 'high_cloud_top', enabled: boolean): void {
    if (this.destroyed) return;
    this.enabled = { ...this.enabled, [family]: enabled };
    this.render();
  }

  clear(): void {
    this.group.clearLayers();
    this.legend.textContent = '';
    this.legend.hidden = true;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.clear();
    this.group.remove();
    this.legend.remove();
    this.data = null;
  }

  private render(): void {
    this.clear();
    if (!this.data || this.data.status === 'disabled' || (this.data.status === 'unavailable' && this.time !== 'observed')) return;
    const projected = this.time !== 'observed';
    const drawProjection = !projected || this.presentation !== 'hidden';
    for (const feature of this.data.features) {
      if (!this.enabled[feature.family]) continue;
      const family = familyClass(feature);
      const selected = this.selected.has(feature.feature_id);
      if (feature.trail.length > 0) {
        const trail = L.polyline(feature.trail.map(sample => [sample.center[1], sample.center[0]] as L.LatLngTuple), {
          color: feature.family === 'radar_echo' ? '#1677c8' : '#9c3fc7', weight: selected ? 5 : 3,
          opacity: 0.85, dashArray: feature.family === 'radar_echo' ? undefined : '3 5',
          className: `observed-motion-trail observed-motion-trail-${family}${selected ? ' observed-motion-selected' : ''}`,
        });
        trail.bindTooltip(feature.trail.map(sample => `${sample.observed_at}: contour centre`).join('<br>'));
        this.group.addLayer(trail);
      }
      const polygons = drawProjection ? latLngs(this.data, feature, this.time) : null;
      if (!polygons) continue;
      for (const polygon of polygons) {
        const layer = L.polygon(polygon, {
          color: feature.family === 'radar_echo' ? '#0868ac' : '#8e2bb9', fillColor: feature.family === 'radar_echo' ? '#41b6c4' : '#d98ce8',
          fillOpacity: projected ? 0.03 : 0.07, weight: selected ? 5 : 3,
          dashArray: projected ? (feature.family === 'radar_echo' ? '9 6' : '3 5') : (feature.family === 'radar_echo' ? undefined : '3 5'),
          className: `observed-motion-footprint observed-motion-footprint-${family}${projected ? ' observed-motion-projection' : ''}${this.presentation === 'stored' ? ' observed-motion-stored' : ''}${selected ? ' observed-motion-selected' : ''}`,
        });
        layer.bindTooltip(`${contourLabel(feature)} · ${feature.reference_at}`, { sticky: true });
        this.group.addLayer(layer);
      }
    }
    // Reported detections remain at their observed coordinates for every
    // selected contour time. Window-only timing has a distinct dashed ring;
    // nothing here advects or synthesizes a flash position.
    for (const detection of this.data.lightning) {
      const windowOnly = detection.time_precision === 'window_only';
      const selected = detection.associated_feature_ids?.some(featureId => this.selected.has(featureId)) ?? false;
      const marker = L.circleMarker([detection.position[1], detection.position[0]], {
        radius: windowOnly ? 7 : 5, color: '#b45309', fillColor: '#f59e0b', fillOpacity: windowOnly ? 0.12 : 0.8,
        weight: selected ? 4 : 2, dashArray: windowOnly ? '2 3' : undefined,
        className: `observed-motion-lightning${windowOnly ? ' observed-motion-lightning-window' : ''}${selected ? ' observed-motion-selected' : ''}`,
      });
      marker.bindTooltip(windowOnly
        ? `Lightning acquisition window ${detection.acquisition_window.start_at}–${detection.acquisition_window.end_at}`
        : `Lightning reported ${detection.event_at}`);
      this.group.addLayer(marker);
    }
    this.legend.hidden = false;
    this.legend.textContent = projected
      ? `${this.presentation === 'active' ? 'Experimental constant-motion projection' : 'Stored projection'} · dashed server contour · ${this.time}`
      : 'Observed contours · radar ≥ 5 dBZ · high cloud tops ≥ 15,000 ft MSL · trails use source times';
  }
}
