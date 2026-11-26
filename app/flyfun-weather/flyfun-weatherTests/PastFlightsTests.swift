//
//  PastFlightsTests.swift
//  flyfun-weatherTests
//
//  Past flights finder (#1032): archive payload decoding (dynamic tag keys
//  survive snake→camel), the filter → query mapping the server reads, and the
//  view model's paging and month grouping.
//

import Foundation
import Testing
@testable import flyfun_weather

@MainActor
@Suite("PastFlights")
struct PastFlightsTests {

    static let payloadJSON = """
    {
      "view": "list", "total": 2, "limit": 30, "offset": 0,
      "facets": {
        "source": "forecast",
        "tags": {"ICE": 1, "TS": 0, "IMC": 1, "WIND": 0, "TURB": 0, "FRZ": 0, "VIS": 0},
        "outcomes": {"flown": 1, "cancelled": 1, "monitoring": 0, "debriefed": 2, "differs": 1},
        "not_recorded": 1
      },
      "rows": [
        {"id": "f1", "departure_time": "2026-11-20T09:00:00+00:00", "departure": "EGTK",
         "destination": "LFAT", "route_name": "Oxford to Le Touquet",
         "trip": {"id": "t1", "name": "Autumn trip"},
         "forecast_tags": {"ICE": "red", "IMC": "amber"}, "forecast_worst": "red",
         "tags_recorded": true, "reported_tags": ["ICE"], "decision": "flown",
         "debriefed": true, "differs_from_forecast": true, "distance_nm": 112.4},
        {"id": "f2", "departure_time": "2026-10-02T15:30:00+00:00", "departure": null,
         "destination": null, "route_name": "Local", "trip": null,
         "forecast_tags": {}, "forecast_worst": null, "tags_recorded": false,
         "reported_tags": [], "decision": "cancelled", "debriefed": true,
         "differs_from_forecast": false, "distance_nm": null}
      ]
    }
    """

    static func payload() throws -> PastArchiveResponse {
        try JSONDecoder.weatherBrief.decode(PastArchiveResponse.self, from: Data(payloadJSON.utf8))
    }

    @Test func decodesRowsAndFacets() throws {
        let resp = try Self.payload()
        #expect(resp.total == 2)
        #expect(resp.facets.tags["ICE"] == 1)
        #expect(resp.facets.outcomes["differs"] == 1)
        #expect(resp.facets.notRecorded == 1)
        let first = resp.rows[0]
        #expect(first.forecastTags == ["ICE": "red", "IMC": "amber"])
        #expect(first.title == "EGTK → LFAT")
        #expect(first.trip?.name == "Autumn trip")
        #expect(first.departureDate != nil)
        #expect(resp.rows[1].title == "Local")
        #expect(resp.rows[1].tagsRecorded == false)
    }

    @Test func emptyFilterAsksForAPageOnly() {
        let items = PastFilter.all.queryItems(limit: 30, offset: 0)
        #expect(items == [URLQueryItem(name: "limit", value: "30")])
        #expect(!PastFilter.all.isActive)
    }

    @Test func filterMapsToServerParams() {
        var f = PastFilter(query: " EGTK ", tags: ["ICE", "TS"], reported: true,
                           outcomes: ["flown"], hideInactive: true)
        #expect(f.isActive)
        let items = Dictionary(uniqueKeysWithValues: f.queryItems(limit: 30, offset: 60).map { ($0.name, $0.value) })
        #expect(items["q"] == "EGTK")
        #expect(items["tags"] == "ICE,TS")
        #expect(items["source"] == "reported")
        #expect(items["outcome"] == "flown")
        #expect(items["hide"] == "cancelled,monitoring")
        #expect(items["offset"] == "60")
        f.reported = false
        #expect(!f.queryItems(limit: 30, offset: 0).contains { $0.name == "source" })
    }

    @Test func chipLabelsFallBackToKey() {
        #expect(PastChips.label(forTag: "ICE") == "Icing")
        #expect(PastChips.label(forOutcome: "monitoring") == "Monitor only")
        #expect(PastChips.label(forTag: "XYZ") == "XYZ")
    }

    @Test func viewModelLoadsAndGroupsByMonth() async throws {
        let repo = MockBriefingRepository()
        let resp = try Self.payload()
        repo.pastArchiveHandler = { _ in resp }
        let vm = PastFlightsViewModel(repository: repo, filter: .withIcing)
        await vm.reload()
        #expect(vm.rows.count == 2)
        #expect(vm.facets?.tags["ICE"] == 1)
        #expect(!vm.hasMore)
        #expect(vm.monthSections.map(\.rows.count) == [1, 1])  // Nov, Oct (titles are locale-formatted)
    }
}
