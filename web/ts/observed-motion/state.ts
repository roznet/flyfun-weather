import { parseObservedMotion, type MotionTime, type ParsedObservedMotion } from './types';

export type MotionCapability = 'unknown' | 'enabled' | 'disabled';
export interface MotionRequestToken { generation: number; sequence: number }

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  return `{${Object.keys(value as Record<string, unknown>).sort().map(key =>
    `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`).join(',')}}`;
}

export class MotionState {
  current: ParsedObservedMotion | null = null;
  capability: MotionCapability = 'unknown';
  selectedTime: MotionTime = 'observed';
  selectedFeatureId: string | null = null;
  selectedAssociationId: string | null = null;
  modeActive = false;
  sourceEnabled: Record<'radar_echo' | 'high_cloud_top', boolean> = { radar_echo: true, high_cloud_top: true };
  requestGeneration = 0;
  private requestSequence = 0;
  private capabilitySequence = 0;
  private contextKey = '';
  private now = new Date();
  private refreshFailed = false;
  private requestMotionIssue: string | null = null;
  private listeners = new Set<() => void>();

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private changed(): void { for (const listener of this.listeners) listener(); }

  enterContext(flightId: string, packId: string): number {
    const key = `${flightId}\n${packId}`;
    this.requestGeneration++;
    this.requestSequence = 0;
    this.capabilitySequence = 0;
    this.capability = 'unknown';
    this.refreshFailed = false;
    this.requestMotionIssue = null;
    if (key !== this.contextKey) {
      this.contextKey = key;
      this.current = null;
      this.resetSelection();
    }
    this.changed();
    return this.requestGeneration;
  }

  enterMotionMode(): number {
    this.modeActive = true;
    this.capability = 'unknown';
    this.capabilitySequence = 0;
    this.requestGeneration++;
    this.requestSequence = 0;
    this.requestMotionIssue = null;
    this.resetSelection();
    this.sourceEnabled = { radar_echo: true, high_cloud_top: true };
    this.changed();
    return this.requestGeneration;
  }

  leaveMotionMode(): void {
    this.modeActive = false;
    this.resetSelection();
    this.changed();
  }

  markLifecycleUnknown(): number {
    this.capability = 'unknown';
    this.capabilitySequence = 0;
    this.requestGeneration++;
    this.requestSequence = 0;
    this.changed();
    return this.requestGeneration;
  }

  beginRequest(generation = this.requestGeneration): MotionRequestToken {
    return { generation, sequence: ++this.requestSequence };
  }

  acceptCapability(enabled: boolean | null, token: MotionRequestToken): boolean {
    if (token.generation !== this.requestGeneration || token.sequence < this.capabilitySequence) return false;
    this.capabilitySequence = token.sequence;
    this.capability = enabled === true ? 'enabled' : enabled === false ? 'disabled' : 'unknown';
    this.changed();
    return true;
  }

  accept(raw: unknown, generation = this.requestGeneration): boolean {
    if (generation !== this.requestGeneration) return false;
    const incoming = parseObservedMotion(raw);
    if (incoming.revision === null) {
      this.requestMotionIssue = incoming.unavailableReason;
      this.changed();
      return false;
    }
    this.requestMotionIssue = null;
    const previous = this.current;
    if (previous?.revision != null) {
      if (incoming.revision < previous.revision) return false;
      if (incoming.revision === previous.revision) {
        if (canonicalJson(incoming.raw) === canonicalJson(previous.raw)) {
          this.refreshFailed = false;
          this.changed();
          return true;
        }
        this.current = {
          raw: incoming.raw, status: 'unavailable', revision: incoming.revision, motion: null,
          unavailableReason: 'same_revision_conflict', validationReasons: ['same_revision_conflict'],
        };
        this.requestMotionIssue = 'same_revision_conflict';
        this.resetSelection();
        this.changed();
        return false;
      }
    }
    const identityChanged = previous?.motion?.run_id !== incoming.motion?.run_id
      || previous?.motion?.route_geometry_id !== incoming.motion?.route_geometry_id;
    this.current = incoming;
    this.refreshFailed = false;
    if (identityChanged || incoming.status !== 'available') this.resetSelection();
    this.changed();
    return true;
  }

  noteRefreshFailure(_detail?: string): void {
    this.refreshFailed = true;
    this.requestMotionIssue = 'refresh_failed';
    this.changed();
  }

  selectTime(time: MotionTime): void {
    if (time !== 'observed' && (this.current?.status !== 'available' || !this.current.motion?.projection_times.includes(time))) return;
    this.selectedTime = time;
    this.changed();
  }

  selectFeature(featureId: string | null): void {
    this.selectedFeatureId = featureId && this.current?.motion?.features.some(feature => feature.feature_id === featureId) ? featureId : null;
    this.selectedAssociationId = null;
    this.changed();
  }

  selectAssociation(associationId: string | null): void {
    const association = this.current?.motion?.associations.find(item => item.association_id === associationId);
    this.selectedAssociationId = association?.association_id ?? null;
    this.selectedFeatureId = null;
    this.changed();
  }

  setSourceEnabled(family: 'radar_echo' | 'high_cloud_top', enabled: boolean): void {
    this.sourceEnabled = { ...this.sourceEnabled, [family]: enabled };
    this.changed();
  }

  updateClock(now = new Date()): void {
    this.now = now;
    this.changed();
  }

  get canPresentActivePrediction(): boolean {
    if (!this.modeActive || this.capability !== 'enabled' || this.requestMotionIssue !== null
        || this.selectedTime === 'observed' || this.current?.status !== 'available' || !this.current.motion) return false;
    const selected = Date.parse(this.selectedTime);
    return Number.isFinite(selected) && selected > this.now.getTime() && !this.clockUncertain;
  }

  get clockUncertain(): boolean {
    const cutoff = this.current?.motion?.cutoff_at;
    return !!cutoff && (!Number.isFinite(this.now.getTime()) || this.now.getTime() < Date.parse(cutoff));
  }

  get presentationReasons(): string[] {
    const result: string[] = [];
    if (this.current?.unavailableReason) result.push(this.current.unavailableReason);
    if (this.requestMotionIssue) result.push(this.requestMotionIssue);
    if (this.capability === 'unknown') result.push('capability_unknown');
    if (this.capability === 'disabled') result.push('feature_disabled');
    if (this.refreshFailed) result.push('refresh_failed');
    if (this.refreshFailed || this.requestMotionIssue || this.capability !== 'enabled') result.push('stored_analysis');
    if (this.clockUncertain) result.push('clock_uncertain');
    if (this.selectedTime !== 'observed' && Date.parse(this.selectedTime) <= this.now.getTime()) result.push('expired');
    return [...new Set(result)];
  }

  private resetSelection(): void {
    this.selectedTime = 'observed';
    this.selectedFeatureId = null;
    this.selectedAssociationId = null;
  }
}
