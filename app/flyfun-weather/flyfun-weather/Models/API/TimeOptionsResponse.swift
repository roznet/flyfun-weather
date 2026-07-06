import Foundation

/// Swift Codable mirrors of the server `time_scan.py` DTO family (#357), matching
/// the web `TimeOptionsResponse` (`api-adapter.ts`). Datetime fields are decoded
/// as ISO-8601 **strings** (not `Date`) to mirror the web adapter and sidestep
/// the shared decoder's non-fractional `.iso8601` date strategy — the panel
/// formats them itself. Only the subset the UI renders is modelled; the shared
/// decoder ignores the extra keys the server includes (`coverage`, `models`,
/// `ecmwf_run_ts`, `valid_times` on the scan window, …).
///
/// Poll shape: `{ "status": <TimeScanStatus>|null, "scan": <TimeWindowScan>|null }`.
struct TimeOptionsResponse: Codable, Sendable {
    let status: TimeScanStatusDTO?
    let scan: TimeWindowScanDTO?
}

/// Body for the on-tap multi-model confirm — identifies the candidate by its
/// departure time (`departure_time` on the wire).
struct ConfirmTimeOptionRequest: Encodable, Sendable {
    let departureTime: String
}

/// Small polling-status sidecar. `.skipped` carries a machine `reason`
/// (`"no_alternate_time"`, `"flexibility_none"`, …) so the panel can tell
/// "nothing to show" from "still looking".
struct TimeScanStatusDTO: Codable, Sendable {
    let status: TimeScanJobStatus
    let flexibility: FlexibilityMode
    let reason: String
    let updatedAt: String

    private enum CodingKeys: String, CodingKey {
        case status, flexibility, reason, updatedAt
    }

    /// Tolerant decode, matching every other DTO in this file: a missing or
    /// renamed key degrades that one field instead of failing the whole
    /// `TimeOptionsResponse` decode (which would blank the panel via the poll's
    /// error path). `status` drives the state ladder, so an absent/unknown value
    /// defaults to the terminal `.done` — never spin forever on schema drift.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = (try? c.decode(TimeScanJobStatus.self, forKey: .status)) ?? .done
        flexibility = (try? c.decode(FlexibilityMode.self, forKey: .flexibility)) ?? .none
        reason = (try? c.decode(String.self, forKey: .reason)) ?? ""
        updatedAt = (try? c.decode(String.self, forKey: .updatedAt)) ?? ""
    }
}

/// Job lifecycle. `.pending`/`.running` are non-terminal (keep polling);
/// `.done`/`.failed`/`.skipped` are terminal.
enum TimeScanJobStatus: String, Codable, Sendable {
    case pending
    case running
    case done
    case failed
    case skipped
}

/// Result of a multi-model check of one candidate (slice-4 on-tap confirm, or
/// filled at scan time when the candidate is in-window).
struct TimeConfirmationDTO: Codable, Sendable {
    let modelsChecked: [String]
    let assessment: String            // GREEN / AMBER / RED
    let assessmentReason: String
    /// Per-model `"id=STATUS, ..."` breakdown; a model with nothing flagged is
    /// absent. Feeds the detail-table dot tooltips. Optional/defaulted so a
    /// legacy confirmation without it decodes.
    let perModelReasons: [String: String]
    let betterThanBaseline: Bool
    let improves: [String]
    let worsens: [String]
    let confirmedAt: String

    private enum CodingKeys: String, CodingKey {
        case modelsChecked, assessment, assessmentReason, perModelReasons
        case betterThanBaseline, improves, worsens, confirmedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        modelsChecked = (try? c.decode([String].self, forKey: .modelsChecked)) ?? []
        assessment = try c.decode(String.self, forKey: .assessment)
        assessmentReason = (try? c.decode(String.self, forKey: .assessmentReason)) ?? ""
        perModelReasons = (try? c.decode([String: String].self, forKey: .perModelReasons)) ?? [:]
        betterThanBaseline = (try? c.decode(Bool.self, forKey: .betterThanBaseline)) ?? false
        improves = (try? c.decode([String].self, forKey: .improves)) ?? []
        worsens = (try? c.decode([String].self, forKey: .worsens)) ?? []
        confirmedAt = (try? c.decode(String.self, forKey: .confirmedAt)) ?? ""
    }
}

/// Per-candidate model-coverage label — the "honesty ladder":
/// `.confirmedInWindow` (free, in every model's window), `.ecmwfOnly`
/// (provisional, shows the "Check all models" affordance), `.confirmed`
/// (user-tapped multi-model check, may be a designed downgrade).
enum TimeConfidence: String, Codable, Sendable {
    case confirmedInWindow = "confirmed_in_window"
    case ecmwfOnly = "ecmwf_only"
    case confirmed
}

/// One graded departure time.
struct TimeCandidateDTO: Codable, Identifiable, Sendable {
    let departureTime: String
    let departureShiftHours: Double
    /// Per-route-point ETAs the grade actually read (audit trail).
    let validTimes: [String]
    let assessment: String            // GREEN / AMBER / RED
    let assessmentReason: String
    let modelsUsed: [String]
    let improves: [String]
    let worsens: [String]
    let margin: Double
    let confidence: TimeConfidence
    let isBaseline: Bool
    let isAlternate: Bool
    let confirmed: TimeConfirmationDTO?
    /// True while an on-tap confirm is queued/running for this candidate — the
    /// panel renders "checking all models…" off this flag.
    let confirmPending: Bool

    /// Departure time is unique within a scan (minute tolerance) — a stable id.
    var id: String { departureTime }

    private enum CodingKeys: String, CodingKey {
        case departureTime, departureShiftHours, validTimes, assessment
        case assessmentReason, modelsUsed, improves, worsens, margin
        case confidence, isBaseline, isAlternate, confirmed, confirmPending
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        departureTime = try c.decode(String.self, forKey: .departureTime)
        departureShiftHours = (try? c.decode(Double.self, forKey: .departureShiftHours)) ?? 0
        validTimes = (try? c.decode([String].self, forKey: .validTimes)) ?? []
        assessment = try c.decode(String.self, forKey: .assessment)
        assessmentReason = (try? c.decode(String.self, forKey: .assessmentReason)) ?? ""
        modelsUsed = (try? c.decode([String].self, forKey: .modelsUsed)) ?? []
        improves = (try? c.decode([String].self, forKey: .improves)) ?? []
        worsens = (try? c.decode([String].self, forKey: .worsens)) ?? []
        margin = (try? c.decode(Double.self, forKey: .margin)) ?? 0
        confidence = (try? c.decode(TimeConfidence.self, forKey: .confidence)) ?? .ecmwfOnly
        isBaseline = (try? c.decode(Bool.self, forKey: .isBaseline)) ?? false
        isAlternate = (try? c.decode(Bool.self, forKey: .isAlternate)) ?? false
        confirmed = try? c.decodeIfPresent(TimeConfirmationDTO.self, forKey: .confirmed)
        confirmPending = (try? c.decode(Bool.self, forKey: .confirmPending)) ?? false
    }
}

/// The searched departure window and what clipped it.
struct TimeWindowDTO: Codable, Sendable {
    let start: String
    let end: String
    let daylightClipped: Bool
    let horizonClipped: Bool
    /// Previous-day mode where part of the daylight window has already elapsed.
    let pastClipped: Bool

    private enum CodingKeys: String, CodingKey {
        case start, end, daylightClipped, horizonClipped, pastClipped
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        start = (try? c.decode(String.self, forKey: .start)) ?? ""
        end = (try? c.decode(String.self, forKey: .end)) ?? ""
        daylightClipped = (try? c.decode(Bool.self, forKey: .daylightClipped)) ?? false
        horizonClipped = (try? c.decode(Bool.self, forKey: .horizonClipped)) ?? false
        pastClipped = (try? c.decode(Bool.self, forKey: .pastClipped)) ?? false
    }
}

/// The planned departure graded through the same path as the candidates — the
/// diff denominator.
struct TimeBaselineDTO: Codable, Sendable {
    let departureTime: String
    let assessment: String
    let assessmentReason: String

    private enum CodingKeys: String, CodingKey {
        case departureTime, assessment, assessmentReason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        departureTime = (try? c.decode(String.self, forKey: .departureTime)) ?? ""
        assessment = (try? c.decode(String.self, forKey: .assessment)) ?? ""
        assessmentReason = (try? c.decode(String.self, forKey: .assessmentReason)) ?? ""
    }
}

/// The `time_options.json` artifact — everything the scenario panel renders.
struct TimeWindowScanDTO: Codable, Sendable {
    let flexibility: FlexibilityMode
    let baseline: TimeBaselineDTO
    let window: TimeWindowDTO?      // null for pure "alternate" mode
    let candidates: [TimeCandidateDTO]
    /// Daylight hours refused because some model's coverage didn't span them.
    let refusedTimes: [String]
    let generatedAt: String

    private enum CodingKeys: String, CodingKey {
        case flexibility, baseline, window, candidates, refusedTimes, generatedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        flexibility = (try? c.decode(FlexibilityMode.self, forKey: .flexibility)) ?? .none
        baseline = try c.decode(TimeBaselineDTO.self, forKey: .baseline)
        window = try? c.decodeIfPresent(TimeWindowDTO.self, forKey: .window)
        candidates = (try? c.decode([TimeCandidateDTO].self, forKey: .candidates)) ?? []
        refusedTimes = (try? c.decode([String].self, forKey: .refusedTimes)) ?? []
        generatedAt = (try? c.decode(String.self, forKey: .generatedAt)) ?? ""
    }
}
