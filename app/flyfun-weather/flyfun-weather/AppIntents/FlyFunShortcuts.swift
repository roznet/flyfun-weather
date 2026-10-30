import AppIntents

/// Zero-setup Siri phrases for the Tier-1 intents. Every phrase must contain the
/// app name (there's no weather App Schema, so these are custom intents — see the
/// design's "one hard constraint"). English v1; localization is a fast-follow.
///
/// One verb per action so Siri can't fuzzy-match across them: "Open …" opens
/// the app, "Brief me / Read / Check …" speaks. A spoken phrase that ended like
/// an open one ("Check my next flight in …" vs "Show my next flight in …") ran
/// the open intent, and "What does … look like" questions went to web search.
struct FlyFunShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: OpenFlightListIntent(),
            phrases: [
                "Show my \(.applicationName) briefings",
                "Open \(.applicationName)",
            ],
            shortTitle: "My Briefings",
            systemImageName: "list.bullet.rectangle"
        )
        AppShortcut(
            intent: OpenBriefingIntent(),
            phrases: [
                "Open my next \(.applicationName) flight",
                "Open my next \(.applicationName) briefing",
            ],
            shortTitle: "Next Briefing",
            systemImageName: "airplane"
        )
        AppShortcut(
            intent: CheckBriefingIntent(),
            phrases: [
                "Brief me on my next \(.applicationName) flight",
                "Read my \(.applicationName) briefing",
                "Read my next flight in \(.applicationName)",
                "Read my next \(.applicationName) flight",
                "Check my \(.applicationName) briefing",
                "What's my \(.applicationName) assessment for \(\.$flight)",
            ],
            shortTitle: "Check Briefing",
            systemImageName: "checkmark.seal"
        )
        AppShortcut(
            intent: FlightsOverviewIntent(),
            phrases: [
                "How do my \(.applicationName) flights look",
                "My \(.applicationName) flights overview",
            ],
            shortTitle: "Flights Overview",
            systemImageName: "list.bullet.rectangle.portrait"
        )
        AppShortcut(
            intent: RefreshBriefingIntent(),
            phrases: [
                "Refresh my \(.applicationName) briefing",
                "Refresh my \(.applicationName) briefing for \(\.$flight)",
            ],
            shortTitle: "Refresh Briefing",
            systemImageName: "arrow.clockwise"
        )
        AppShortcut(
            intent: AirportWeatherIntent(),
            phrases: [
                "What's the \(.applicationName) weather at \(\.$airport)",
                "\(.applicationName) airport weather",
            ],
            shortTitle: "Airport Weather",
            systemImageName: "cloud.sun"
        )
    }
}
