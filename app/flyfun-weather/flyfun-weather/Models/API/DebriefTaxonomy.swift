import Foundation

/// The debrief vocabulary served in the `debrief` section of
/// `GET /api/help/catalog` — decision buttons, condition tags (cancel reasons +
/// outcome categories), outcome values, and the advisory→tag map.
///
/// Python (`weatherbrief/debriefs/taxonomy.py`) is the single source of truth;
/// the iOS debrief form renders straight from this so the tag set / labels can't
/// drift from the backend or the web. Decoded with a plain `JSONDecoder` (no
/// snake-case key conversion, like the rest of the help catalog), so the
/// snake_case wire keys are mapped by explicit `CodingKeys`.
struct DebriefTaxonomy: Codable, Sendable, Equatable {
    /// One decision button (`flown` / `cancelled` / `monitoring`).
    struct DecisionOption: Codable, Sendable, Equatable, Identifiable {
        let id: String
        let label: String
    }

    /// One condition tag: a cancel reason and (unless `outcomeCategory` is false,
    /// i.e. OPS) a gradeable outcome category.
    struct TagOption: Codable, Sendable, Equatable, Identifiable {
        let id: String
        let label: String
        let description: String
        let outcomeCategory: Bool

        enum CodingKeys: String, CodingKey {
            case id, label, description
            case outcomeCategory = "outcome_category"
        }
    }

    /// One outcome value (`consistent` / `better` / `worse`).
    struct OutcomeOption: Codable, Sendable, Equatable, Identifiable {
        let id: String
        let label: String
    }

    let decisions: [DecisionOption]
    let tags: [TagOption]
    let outcomeValues: [OutcomeOption]
    /// advisory_id → tag id, used to pre-select the flown-form outcome rows from
    /// the flagged advisories on the open briefing.
    let advisoryTagMap: [String: String]
    /// Free-text note ceiling (characters).
    let noteMaxLength: Int

    enum CodingKeys: String, CodingKey {
        case decisions, tags
        case outcomeValues = "outcome_values"
        case advisoryTagMap = "advisory_tag_map"
        case noteMaxLength = "note_max_length"
    }

    // MARK: - Derived

    /// Tags that can be graded as flown-outcome categories (everything but OPS).
    var outcomeCategoryTags: [TagOption] { tags.filter { $0.outcomeCategory } }

    /// Look up a tag by id.
    func tag(_ id: String) -> TagOption? { tags.first { $0.id == id } }

    /// Look up a decision by id.
    func decision(_ id: String) -> DecisionOption? { decisions.first { $0.id == id } }

    /// The tag ids whose advisory came back AMBER/RED on the open briefing, in
    /// this taxonomy's tag order. GREEN/UNAVAILABLE don't count — there's nothing
    /// to grade. Mirrors `flagged_tags_from_advisories` in the Python taxonomy;
    /// drives which outcome rows the flown form shows.
    ///
    /// - Parameter advisories: `(advisoryId, aggregateStatus)` pairs from the
    ///   loaded advisories manifest.
    func flaggedTagIds(fromAdvisories advisories: [(id: String, status: String)]) -> [String] {
        var flagged = Set<String>()
        for advisory in advisories {
            let s = advisory.status.lowercased()
            guard s == "amber" || s == "red" else { continue }
            if let tagId = advisoryTagMap[advisory.id] { flagged.insert(tagId) }
        }
        // Preserve taxonomy tag order for a stable form layout.
        return tags.map(\.id).filter { flagged.contains($0) }
    }

    // MARK: - Offline fallback

    /// Hand-maintained fallback used before the first online catalog sync (the
    /// bundled metrics baseline carries no `debrief` section). Mirrors
    /// `build_taxonomy_catalog()` in the Python taxonomy — the served copy is the
    /// source of truth; this only backstops a cold first launch and is covered by
    /// the sync-ios-web drift audit.
    static let bundledBaseline = DebriefTaxonomy(
        decisions: [
            .init(id: "flown", label: "Flown"),
            .init(id: "cancelled", label: "Cancelled"),
            .init(id: "monitoring", label: "Monitor only"),
        ],
        tags: [
            .init(id: "IMC", label: "IMC", description: "Low ceilings / IFR conditions", outcomeCategory: true),
            .init(id: "ICE", label: "Icing", description: "Airframe icing", outcomeCategory: true),
            .init(id: "WIND", label: "Wind", description: "Strong / gusty / crosswind", outcomeCategory: true),
            .init(id: "TS", label: "Thunderstorm", description: "Thunderstorms or convective build-up", outcomeCategory: true),
            .init(id: "TURB", label: "Turbulence", description: "Turbulence (any intensity)", outcomeCategory: true),
            .init(id: "FRZ", label: "Freezing precip", description: "Freezing rain / sleet", outcomeCategory: true),
            .init(id: "VIS", label: "Visibility", description: "Reduced visibility, fog, mist", outcomeCategory: true),
            .init(id: "OPS", label: "Operational", description: "Non-weather (aircraft, pilot, NOTAM, fuel, …)", outcomeCategory: false),
        ],
        outcomeValues: [
            .init(id: "consistent", label: "As forecast"),
            .init(id: "better", label: "Better than forecast"),
            .init(id: "worse", label: "Worse than forecast"),
        ],
        advisoryTagMap: [
            "icing_escape": "ICE",
            "fiki_icing": "ICE",
            "vmc_cruise": "IMC",
            "cloud_top": "IMC",
            "vfr_feasibility": "IMC",
            "ifr_feasibility": "IMC",
            "flight_category": "IMC",
            "turbulence": "TURB",
            "mountain_wind": "TURB",
            "convective": "TS",
            "airport_wind": "WIND",
        ],
        noteMaxLength: 300
    )
}
