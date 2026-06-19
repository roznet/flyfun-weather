/**
 * Privacy-first analytics client.
 *
 * - Generates a stable anonymous browser UUID (``anon_id``) once and
 *   stores it in ``localStorage``. Sessions are scoped to a tab visit
 *   plus a 30-minute idle timeout.
 * - Events are pushed into an in-memory buffer and flushed every
 *   ``FLUSH_INTERVAL_MS`` plus on ``visibilitychange``/``pagehide`` via
 *   ``navigator.sendBeacon`` so the page can unload without losing data.
 * - A user-set ``analytics-notrack=1`` flag in ``localStorage`` disables
 *   tracking entirely (use for dev/internal devices).
 * - Tracking is also disabled when ``window.location.hostname`` is
 *   localhost or 127.0.0.1, so dev sessions never contaminate prod data.
 *
 * Public API: ``track(event, props?)`` and ``setBriefingContext()``.
 * Mirror of ``src/weatherbrief/analytics/events.py``.
 */

import { EVENTS, type EventName } from './events';

const ANON_KEY = 'analytics-anon-id';
const NOTRACK_KEY = 'analytics-notrack';
// Set ``localStorage['analytics-force'] = '1'`` to enable tracking on
// localhost — useful when you want to test the full pipeline against the
// dev API server. Has no effect in production.
const FORCE_KEY = 'analytics-force';
const SESSION_STORAGE_KEY = 'analytics-session';
const SESSION_IDLE_MS = 30 * 60 * 1000;
const FLUSH_INTERVAL_MS = 10_000;
const ENDPOINT = '/api/events';

// Arrays of strings are allowed for bounded *set* dimensions (e.g. the
// cross-section's enabled-layer ids). They serialise to a JSON array in the
// stored props blob; the server rollup iterates them. Keep set values
// low-cardinality bounded id lists — never free text or unbounded counts.
type Props = Record<string, string | number | boolean | null | string[]>;

interface QueuedEvent {
  event: EventName;
  ts: string;
  // Briefing context: the public-API composite key. The server resolves
  // this to the internal briefing_packs.id at ingest.
  flight_id?: string;
  briefing_ts?: string;
  props?: Props;
}

interface BatchPayload {
  anon_id: string;
  session_id: string;
  app_version?: string;
  events: QueuedEvent[];
}

let queue: QueuedEvent[] = [];
let flushTimer: number | null = null;
let initialized = false;

// Briefing context attached to every event until cleared. The briefing-main
// entrypoint sets this on load and clears it on unload.
let currentBriefingTs: string | undefined;
let currentFlightId: string | undefined;
// Events already emitted for the *current* briefing context — used by
// ``trackOncePerBriefing`` to suppress duplicate feature-open events when
// the user toggles between e.g. skew-T view modes. Cleared whenever the
// context changes.
const seenInCurrentBriefing = new Set<EventName>();

export function setBriefingContext(
  flightId: string | undefined,
  briefingTs: string | undefined,
): void {
  if (flightId !== currentFlightId || briefingTs !== currentBriefingTs) {
    seenInCurrentBriefing.clear();
  }
  currentFlightId = flightId;
  currentBriefingTs = briefingTs;
}

function isDisabled(): boolean {
  try {
    if (window.localStorage.getItem(NOTRACK_KEY) === '1') return true;
    if (window.localStorage.getItem(FORCE_KEY) === '1') return false;
    const host = window.location.hostname;
    if (host === 'localhost' || host === '127.0.0.1' || host.endsWith('.localhost')) {
      return true;
    }
  } catch {
    return true;
  }
  return false;
}

function uuid(): string {
  // Prefer crypto.randomUUID where available (all modern browsers);
  // fall back to a v4-shaped string built from getRandomValues.
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function getAnonId(): string {
  let id: string | null = null;
  try {
    id = window.localStorage.getItem(ANON_KEY);
  } catch {
    /* localStorage may be blocked in private mode */
  }
  if (!id) {
    id = uuid();
    try {
      window.localStorage.setItem(ANON_KEY, id);
    } catch {
      /* ignore */
    }
  }
  return id;
}

interface SessionRecord {
  id: string;
  lastActivity: number;
}

interface SessionInfo {
  id: string;
  isFresh: boolean;
}

function getOrCreateSession(): SessionInfo {
  const now = Date.now();
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as SessionRecord;
      if (parsed.id && now - parsed.lastActivity < SESSION_IDLE_MS) {
        parsed.lastActivity = now;
        window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(parsed));
        return { id: parsed.id, isFresh: false };
      }
    }
  } catch {
    /* ignore — fall through to fresh session */
  }
  const fresh: SessionRecord = { id: uuid(), lastActivity: now };
  try {
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(fresh));
  } catch {
    /* ignore */
  }
  return { id: fresh.id, isFresh: true };
}

function getSessionId(): string {
  return getOrCreateSession().id;
}

function appVersion(): string | undefined {
  // Look for a build-injected version on a meta tag if present;
  // otherwise leave undefined.
  const meta = document.querySelector<HTMLMetaElement>('meta[name="app-version"]');
  return meta?.content || undefined;
}

function scheduleFlush(): void {
  if (flushTimer !== null) return;
  flushTimer = window.setTimeout(() => {
    flushTimer = null;
    void flush();
  }, FLUSH_INTERVAL_MS);
}

async function flush(): Promise<void> {
  if (queue.length === 0) return;
  const batch: BatchPayload = {
    anon_id: getAnonId(),
    session_id: getSessionId(),
    app_version: appVersion(),
    events: queue,
  };
  queue = [];

  const body = JSON.stringify(batch);
  // Prefer sendBeacon — survives page unload. Falls back to fetch with
  // keepalive when the browser refuses (large payload, missing API).
  let sent = false;
  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([body], { type: 'application/json' });
      sent = navigator.sendBeacon(ENDPOINT, blob);
    }
  } catch {
    /* sendBeacon can throw if the page is being unloaded — ignore */
  }
  if (!sent) {
    try {
      await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
      });
    } catch {
      // Best-effort. Dropping a batch beats stuck UI; events are not
      // critical for app function.
    }
  }
}

function flushNow(): void {
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  void flush();
}

function ensureInitialized(): void {
  if (initialized || isDisabled()) return;
  initialized = true;
  window.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      flushNow();
    }
  });
  window.addEventListener('pagehide', flushNow);
  // ``initialized`` is module-scoped — it resets on every full page load,
  // but the session_id in sessionStorage persists across loads within the
  // 30-minute idle window. Emitting session_started here unconditionally
  // would re-fire it on reload under the same session_id and inflate the
  // rollup. Gate on the session being genuinely fresh.
  if (getOrCreateSession().isFresh) {
    track(EVENTS.SESSION_STARTED);
  }
}

/**
 * Per-call context override. Used for events that fire *outside* the
 * briefing page where ``setBriefingContext`` is not active — most notably
 * ``flight.created``, which has a flight_id but no briefing_ts yet.
 *
 * Pass ``flight_id`` here rather than stuffing it in ``props`` so the
 * server-side enrichment (which reads the top-level field) picks it up.
 */
export interface TrackContext {
  flight_id?: string;
  briefing_ts?: string;
}

export function track(
  event: EventName,
  props?: Props,
  context?: TrackContext,
): void {
  if (isDisabled()) return;
  ensureInitialized();
  queue.push({
    event,
    ts: new Date().toISOString(),
    flight_id: context?.flight_id ?? currentFlightId,
    briefing_ts: context?.briefing_ts ?? currentBriefingTs,
    props,
  });
  scheduleFlush();
}

/**
 * Track an event at most once per briefing context.
 *
 * Use for "feature opened" signals where the user toggling sub-views
 * (skew-T dynamic ↔ compare ↔ static, map ↔ cross-section ↔ compare)
 * would otherwise inflate the count. The first call inside a given
 * briefing context fires; subsequent calls are silently dropped until
 * ``setBriefingContext`` is called with a different context.
 */
export function trackOncePerBriefing(event: EventName, props?: Props): void {
  // Check isDisabled() before mutating ``seenInCurrentBriefing``. Otherwise,
  // marking the event as seen while tracking is off (analytics-notrack
  // flag) means flipping the flag back on mid-session silently never
  // re-fires it for the current briefing context.
  if (isDisabled()) return;
  if (seenInCurrentBriefing.has(event)) return;
  seenInCurrentBriefing.add(event);
  track(event, props);
}

// Expose the registry too for ergonomics.
export { EVENTS } from './events';
