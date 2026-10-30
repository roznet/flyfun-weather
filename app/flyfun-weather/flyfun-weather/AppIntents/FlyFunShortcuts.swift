import AppIntents

/// Zero-setup Siri phrases. Every phrase must contain the app name (there's no
/// weather App Schema, so these are custom intents — see the design's "one
/// hard constraint"). English v1; localization is a fast-follow.
///
/// Built for the car: read the next (or a named) flight's briefing, refresh it,
/// read it again. One verb per action so Siri can't fuzzy-match across them:
/// "Open …" opens the app, "Brief me / Read / Check …" speaks, "Refresh …"
/// refreshes. Tested on device: a spoken phrase that ended like an open one
/// ("Check my next flight in …" vs "Show my next flight in …") ran the open
/// intent, and "What does … look like" questions went to web search.
///
/// `\(\.$flight)` matches the spoken synonyms upcoming flights carry
/// ("Le Touquet", "from Le Touquet", see `FlightSpokenNames`); call
/// `updateAppShortcutParameters()` when the flight list changes
/// (`SpotlightDonator` does).
///
/// Flights Overview and the flight list are deliberately not here: they add
/// little by voice and their phrases sat close to the briefing ones. Both stay
/// available as Shortcuts actions.
struct FlyFunShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: CheckBriefingIntent(),
            phrases: [
                "Brief me on my next \(.applicationName) flight",
                "Read my \(.applicationName) briefing",
                "Read my next flight in \(.applicationName)",
                "Read my next \(.applicationName) flight",
                "Check my \(.applicationName) briefing",
                "Read my \(.applicationName) briefing for \(\.$flight)",
                "Brief me on my \(.applicationName) flight \(\.$flight)",
            ],
            shortTitle: "Read Briefing",
            systemImageName: "speaker.wave.2"
        )
        AppShortcut(
            intent: RefreshBriefingIntent(),
            phrases: [
                "Refresh my \(.applicationName) briefing",
                "Refresh my next \(.applicationName) flight",
                "Refresh my \(.applicationName) briefing for \(\.$flight)",
            ],
            shortTitle: "Refresh Briefing",
            systemImageName: "arrow.clockwise"
        )
        AppShortcut(
            intent: OpenBriefingIntent(),
            phrases: [
                "Open my next \(.applicationName) flight",
                "Open my next \(.applicationName) briefing",
                "Open my \(.applicationName) briefing for \(\.$flight)",
            ],
            shortTitle: "Open Briefing",
            systemImageName: "airplane"
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
