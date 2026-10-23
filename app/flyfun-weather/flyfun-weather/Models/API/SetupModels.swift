import Foundation

// First-run setup (#900): the DTOs behind `SetupWizardView`, the app's port of
// the web welcome wizard (`web/ts/components/welcome-wizard.ts`). Kept apart
// from `ProfileResponse` so the flight form's decoding is untouched.

/// The one `/auth/me` field the app reads. `/api/user/preferences` does not
/// carry `setup_completed`; `/auth/me` does (false when the user has no
/// preferences row yet), and it is what the web reads. Missing means "not done".
nonisolated struct AuthMeResponse: Decodable, Sendable {
    let setupCompleted: Bool?
}

/// A system profile template from `GET /api/user/profiles/system-templates`:
/// the localized description shown on the wizard's profile card.
nonisolated struct SystemTemplate: Decodable, Hashable, Sendable {
    let key: String
    let name: String
    let description: String
}

/// A profile from `GET /api/user/profiles`, with only what the wizard needs:
/// the template it was seeded from, and the mission settings it prefills.
nonisolated struct SetupProfile: Decodable, Identifiable, Hashable, Sendable {
    let id: Int
    let name: String
    let isDefault: Bool
    let systemTemplateKey: String?
    let settings: SetupProfileSettings

    /// The profile says FIKI: the IFR FIKI template, or the retired FIKI Icing
    /// card on. Mirrors the server's `profile_declares_fiki` (#850).
    var declaresFiki: Bool {
        systemTemplateKey == "ifr_fiki" || settings.fikiIcingEnabled
    }
}

/// The mission subset of a profile's settings. Decoding never throws on a
/// field: the settings blob carries many other keys, and an odd value in one
/// of them must not keep the wizard from loading.
nonisolated struct SetupProfileSettings: Decodable, Hashable, Sendable {
    var flightRules: String?
    var cruiseAltitudeFt: Int?
    var flightCeilingFt: Int?
    /// `advisories.enabled.fiki_icing`, the retired FIKI Icing card.
    var fikiIcingEnabled: Bool

    init(flightRules: String? = nil, cruiseAltitudeFt: Int? = nil,
         flightCeilingFt: Int? = nil, fikiIcingEnabled: Bool = false) {
        self.flightRules = flightRules
        self.cruiseAltitudeFt = cruiseAltitudeFt
        self.flightCeilingFt = flightCeilingFt
        self.fikiIcingEnabled = fikiIcingEnabled
    }

    private enum CodingKeys: String, CodingKey {
        case flightRules, cruiseAltitudeFt, flightCeilingFt, advisories
    }
    private enum AdvisoriesKeys: String, CodingKey { case enabled }
    private enum EnabledKeys: String, CodingKey { case fikiIcing }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        flightRules = try? c.decodeIfPresent(String.self, forKey: .flightRules)
        cruiseAltitudeFt = try? c.decodeIfPresent(Int.self, forKey: .cruiseAltitudeFt)
        flightCeilingFt = try? c.decodeIfPresent(Int.self, forKey: .flightCeilingFt)
        let enabled = try? c.nestedContainer(keyedBy: AdvisoriesKeys.self, forKey: .advisories)
            .nestedContainer(keyedBy: EnabledKeys.self, forKey: .enabled)
        fikiIcingEnabled = (try? enabled?.decodeIfPresent(Bool.self, forKey: .fikiIcing)) ?? false
    }
}

/// Body for `PUT /api/user/profiles/{id}`. The server merges `settings`
/// sparsely: an omitted key is kept, so the template's models and advisory
/// choices survive. Nil fields are omitted, never sent as null (null deletes).
nonisolated struct SetupProfileUpdate: Encodable, Sendable {
    var isDefault: Bool?
    var settings: SetupMission?
}

/// The wizard's "Your usual mission" group, saved to the chosen profile.
nonisolated struct SetupMission: Encodable, Hashable, Sendable {
    let flightRules: String
    let cruiseAltitudeFt: Int
    let flightCeilingFt: Int
}
