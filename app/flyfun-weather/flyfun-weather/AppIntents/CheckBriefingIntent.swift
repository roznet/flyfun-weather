import AppIntents
import SwiftUI

/// "What's my FlyFun assessment for the flight to Cannes?" — speaks a one- or
/// two-line summary (GREEN/AMBER/RED + top advisories) without opening the app,
/// and shows the same as a result card (`CheckBriefingSnippetView`, #365).
/// Reads from cache when offline.
///
/// On flight day (#365) it leads with the live layer when the flight has one:
/// alert lines, then the highlight or headline, then the grade labelled as the
/// briefing's. Outside the live window, or with no live data, the output is
/// the briefing-only line.
struct CheckBriefingIntent: AppIntent {
    static var title: LocalizedStringResource = "Check Briefing"
    static var description = IntentDescription("Hear the assessment and top concerns for a flight.")

    @Parameter(title: "Flight")
    var flight: FlightEntity

    static var parameterSummary: some ParameterSummary {
        Summary("Check the briefing for \(\.$flight)")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog & ShowsSnippetView {
        // Every path returns the same view type (an opaque result can't mix
        // them); with no flight to draw, the card is just an "Open FlyFun" button.
        guard IntentSupport.isSignedIn else {
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)", view: CheckBriefingSnippetView(content: nil))
        }
        let repo = IntentSupport.makeRepository()
        do {
            let flights = try await repo.flights()
            guard let match = flights.first(where: { $0.id == flight.id }) else {
                return .result(dialog: "I couldn't find that flight.", view: CheckBriefingSnippetView(content: nil))
            }
            let live = await Self.liveLayer(for: match, repository: repo)
            let content = CheckBriefingSnippetView.Content(flight: match, live: IntentDialogs.liveCheckLines(live))
            return .result(
                dialog: "\(IntentDialogs.checkSummary(match, live: live))",
                view: CheckBriefingSnippetView(content: content)
            )
        } catch APIError.unauthorized {
            // Token expired and the silent refresh failed (Decision 4).
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)", view: CheckBriefingSnippetView(content: nil))
        } catch {
            return .result(
                dialog: "Sorry, I couldn't load your briefing right now.",
                view: CheckBriefingSnippetView(content: nil)
            )
        }
    }

    /// The flight's live layer when it is in its live window, else nil.
    /// `CachingBriefingRepository.liveLayer` falls back to the on-disk copy
    /// offline. A failure is swallowed: live is an extra on top of the
    /// briefing line, never a reason to speak an error instead of it.
    @MainActor
    private static func liveLayer(for flight: FlightResponse, repository: CachingBriefingRepository) async -> LiveLayerResponse? {
        // A take-off entered in the app stretches the window past the planned
        // arrival (#874). Only knowable when the app is alive; a cold Siri
        // process uses the planned window.
        let started = flight.isEditable
            && AppState.current?.livePositions?.isStartedNotLanded(flightId: flight.id) == true
        guard flight.isInLiveObservationWindow(started: started) else { return nil }
        return try? await repository.liveLayer(flightId: flight.id)
    }
}
