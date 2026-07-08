import AppIntents

/// "What's the FlyFun weather at Cannes tomorrow?" — speaks the consensus flight
/// category + wind for an airport (and the latest observation for today).
struct AirportWeatherIntent: AppIntent {
    static var title: LocalizedStringResource = "Airport Weather"
    static var description = IntentDescription("Hear the forecast flight category and wind for an airport.")

    @Parameter(title: "Airport")
    var airport: AirportEntity

    @Parameter(title: "Day", default: .today)
    var day: AirportWeatherDay

    static var parameterSummary: some ParameterSummary {
        Summary("Get the weather at \(\.$airport) \(\.$day)")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard IntentSupport.isSignedIn else {
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)")
        }
        let repo = IntentSupport.makeRepository()
        do {
            let response = try await repo.airportWeather(icao: airport.icao, day: day.dayOffset, hour: 12)
            guard let entry = response.airports.first else {
                if let unsupported = response.unsupported, !unsupported.isEmpty {
                    return .result(dialog: "\(airport.id) isn't in FlyFun's European coverage area.")
                }
                return .result(dialog: "No forecast is available for \(airport.id) yet.")
            }
            let line = IntentDialogs.airportWeather(entry, dayLabel: day.spokenLabel)
            return .result(dialog: "\(line)")
        } catch APIError.unauthorized {
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)")
        } catch {
            return .result(dialog: "Sorry, I couldn't get the weather for \(airport.id) right now.")
        }
    }
}

/// Voice-friendly day selector (an `@AppEnum` reads far better than a raw `Int`
/// for Siri/Shortcuts — see the design's Open Question on the day parameter).
enum AirportWeatherDay: String, AppEnum {
    case today
    case tomorrow
    case inTwoDays
    case inThreeDays

    static var typeDisplayRepresentation: TypeDisplayRepresentation {
        TypeDisplayRepresentation(name: "Day")
    }

    static var caseDisplayRepresentations: [AirportWeatherDay: DisplayRepresentation] {
        [
            .today: "Today",
            .tomorrow: "Tomorrow",
            .inTwoDays: "In 2 days",
            .inThreeDays: "In 3 days",
        ]
    }

    /// Days from today, matching the server's `day` query param (0…3).
    var dayOffset: Int {
        switch self {
        case .today: 0
        case .tomorrow: 1
        case .inTwoDays: 2
        case .inThreeDays: 3
        }
    }

    /// Leading phrase for the spoken line.
    var spokenLabel: String {
        switch self {
        case .today: "Today"
        case .tomorrow: "Tomorrow"
        case .inTwoDays: "In two days"
        case .inThreeDays: "In three days"
        }
    }
}
