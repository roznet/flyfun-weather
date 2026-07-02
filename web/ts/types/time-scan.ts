/** TypeScript types for the timing-scenario scan (``time_options.json``),
 *  matching the Python Pydantic models in ``models/time_scan.py``.
 *
 *  The scan is an attention-director, never a verdict — a neutral soft hook
 *  surfacing calmer departure windows. The honesty ladder is encoded in
 *  ``TimeCandidate.confidence``: ``"ecmwf_only"`` (provisional) until a
 *  multi-model confirm attaches a {@link TimeConfirmation} on user tap. The
 *  confirm-downgrade (``better_than_baseline: false``) is a first-class outcome,
 *  not an error. */

/** ECMWF (or multi-model) assessment grade — server sends upper-case. */
export type TimeAssessment = 'GREEN' | 'AMBER' | 'RED';

export interface TimeConfirmation {
  /** Models actually checked in the confirm pass (ecmwf/gfs/icon/…). */
  models_checked: string[];
  assessment: TimeAssessment | string;
  assessment_reason: string;
  /** Did the full multi-model check still agree the window beats the planned
   *  time? ``false`` is the on-brand downgrade case, shown plainly. */
  better_than_baseline: boolean;
  improves: string[];
  worsens: string[];
  detail: string;
  confirmed_at: string | null;
}

export interface TimeCandidate {
  departure_time: string;               // ISO-8601
  departure_shift_hours: number;        // signed hours vs planned departure
  valid_times: string[];                // per-route-point ETAs (ISO-8601)
  ecmwf_assessment: TimeAssessment | string;
  ecmwf_assessment_reason: string;
  improves: string[];                   // advisory ids improved vs baseline
  worsens: string[];                    // advisory ids worsened vs baseline
  margin: number;                       // trigger-weighted improvement (ranking)
  confidence: 'ecmwf_only' | 'confirmed';
  is_preferred: boolean;                // the pilot's pinned preferred time
  is_baseline: boolean;                 // the planned departure (shift 0)
  confirmed: TimeConfirmation | null;   // filled on tap (or free in-window)
}

export interface TimeScanBaseline {
  departure_time: string;
  ecmwf_assessment: TimeAssessment | string;
  ecmwf_assessment_reason: string;
}

export interface TimeScanWindow {
  start: string;
  end: string;
  cadence_hours: number;
  daylight_clipped: boolean;
  /** Window stopped at the ECMWF fidelity horizon (hard edge, not silent OM). */
  horizon_clipped: boolean;
  day_flex: string;                     // "day" | "prev" | "next"
}

export interface TimeWindowScan {
  baseline: TimeScanBaseline;
  window: TimeScanWindow;
  candidates: TimeCandidate[];
  scan_flagged: string[];
  models: string[];
  ecmwf_run_ts: number | null;
  generated_at: string | null;
  cross_section_ext: boolean;
}
