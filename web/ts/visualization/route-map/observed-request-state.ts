import type { ObservedBadgeField } from './observed-overlay-geometry';

export interface ObservedImageState {
  url: string | null;
  field: ObservedBadgeField | null;
  pending: boolean;
  failed: boolean;
}

type ImageResult = { blob: Blob; field: ObservedBadgeField };

/** Owns newest-request-wins ordering and object-URL lifetime for map rasters. */
export class ObservedImageRequests {
  private activeKey: string | null = null;
  private generation = 0;
  private attemptedGeneration = -1;
  private state: ObservedImageState = { url: null, field: null, pending: false, failed: false };
  private destroyed = false;

  constructor(
    private readonly load: (url: string, field: ObservedBadgeField) => Promise<ImageResult>,
    private readonly createUrl: (blob: Blob) => string,
    private readonly revokeUrl: (url: string) => void,
  ) {}

  current(): Readonly<ObservedImageState> { return this.state; }

  select(key: string, url: string, field: ObservedBadgeField, changed: () => void): void {
    if (this.destroyed) return;
    if (key !== this.activeKey) {
      this.activeKey = key;
      this.generation++;
      this.attemptedGeneration = -1;
      this.replaceUrl(null);
      this.state = { url: null, field: null, pending: false, failed: false };
    }
    if (this.attemptedGeneration === this.generation) return;

    const requestGeneration = this.generation;
    this.attemptedGeneration = requestGeneration;
    this.state = { ...this.state, pending: true, failed: false };
    void this.load(url, field).then((result) => {
      if (this.destroyed || requestGeneration !== this.generation) return;
      const objectUrl = this.createUrl(result.blob);
      this.replaceUrl(objectUrl);
      this.state = { url: objectUrl, field: result.field, pending: false, failed: false };
    }).catch(() => {
      if (this.destroyed || requestGeneration !== this.generation) return;
      this.state = { ...this.state, pending: false, failed: true };
    }).finally(() => {
      if (!this.destroyed && requestGeneration === this.generation) changed();
    });
  }

  /** Permit one new attempt after an explicit briefing refresh. */
  retryFailed(): void {
    if (this.destroyed || !this.state.failed) return;
    this.generation++;
    this.attemptedGeneration = -1;
    this.state = { ...this.state, pending: false, failed: false };
  }

  clear(): void {
    if (this.destroyed) return;
    this.activeKey = null;
    this.generation++;
    this.attemptedGeneration = -1;
    this.replaceUrl(null);
    this.state = { url: null, field: null, pending: false, failed: false };
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.generation++;
    this.replaceUrl(null);
    this.state = { url: null, field: null, pending: false, failed: false };
  }

  private replaceUrl(next: string | null): void {
    if (this.state.url && this.state.url !== next) this.revokeUrl(this.state.url);
  }
}

/** Single-key newest-request-wins cache for lightning points. */
export class ObservedFlashRequests<T> {
  private activeKey: string | null = null;
  private generation = 0;
  private attemptedGeneration = -1;
  private value: T | null = null;
  private failed = false;

  current(key: string): T | null {
    return key === this.activeKey ? this.value : null;
  }

  select(key: string, load: () => Promise<T>, changed: () => void): void {
    if (key !== this.activeKey) {
      this.activeKey = key;
      this.generation++;
      this.attemptedGeneration = -1;
      this.value = null;
      this.failed = false;
    }
    if (this.attemptedGeneration === this.generation) return;

    const requestGeneration = this.generation;
    this.attemptedGeneration = requestGeneration;
    void load().then((value) => {
      if (requestGeneration === this.generation) this.value = value;
    }).catch(() => {
      if (requestGeneration === this.generation) {
        this.value = null;
        this.failed = true;
      }
    }).finally(() => {
      if (requestGeneration === this.generation) changed();
    });
  }

  retryFailed(): void {
    if (!this.failed) return;
    this.generation++;
    this.attemptedGeneration = -1;
    this.failed = false;
  }

  clear(): void {
    this.activeKey = null;
    this.generation++;
    this.attemptedGeneration = -1;
    this.value = null;
    this.failed = false;
  }
}
