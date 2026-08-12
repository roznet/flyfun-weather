//
//  WhatsNewTests.swift
//  flyfun-weatherTests
//
//  The release stream ("What's New", #550). Two silent-failure classes are
//  pinned here:
//
//  1. **Decoding** — `/api/messages` is decoded with the snake_case decoder, and
//     the stream must survive a category this build predates (the server owns
//     the valid set, so a new one must render, not break the whole list).
//  2. **Markdown-lite bullets** — every body written so far is a `- ` list, and
//     a splitter that stops recognising the marker degrades silently to literal
//     "- " text rather than crashing.
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("WhatsNew stream")
struct WhatsNewStreamTests {

    static let streamJSON = """
    [
      {
        "id": 42,
        "date": "2026-08-08",
        "title": "iOS 1.4 — Route SIGMETs",
        "body": "- Route SIGMETs\\n- Current observations",
        "category": "app_release",
        "highlight": true
      },
      {
        "id": 41,
        "date": "2026-07-02",
        "title": "Visibility in your local units",
        "body": "Visibility now renders **kilometres in Europe**.",
        "category": "change",
        "highlight": false
      }
    ]
    """

    @Test("Stream decodes with the shared snake_case decoder")
    func decodesStream() throws {
        let messages = try JSONDecoder.weatherBrief.decode(
            [SystemMessage].self, from: Data(Self.streamJSON.utf8))

        #expect(messages.count == 2)
        #expect(messages[0].id == 42)
        #expect(messages[0].highlight)
        #expect(messages[1].category == "change")
    }

    @Test("Status decodes unseen_count / latest_message_date")
    func decodesStatus() throws {
        let json = #"{"unseen_count": 3, "latest_message_date": "2026-08-08"}"#
        let status = try JSONDecoder.weatherBrief.decode(
            MessagesStatus.self, from: Data(json.utf8))

        #expect(status.unseenCount == 3)
        #expect(status.latestMessageDate == "2026-08-08")
    }

    @Test("A null latest date decodes rather than failing the status read")
    func decodesStatusWithNullDate() throws {
        let json = #"{"unseen_count": 0, "latest_message_date": null}"#
        let status = try JSONDecoder.weatherBrief.decode(
            MessagesStatus.self, from: Data(json.utf8))

        #expect(status.unseenCount == 0)
        #expect(status.latestMessageDate == nil)
    }

    @Test("An unknown category decodes and falls back to a readable chip label")
    func unknownCategoryStillDecodes() throws {
        let json = """
        [{"id": 1, "date": "2026-09-01", "title": "T", "body": "B",
          "category": "deprecation", "highlight": false}]
        """
        let messages = try JSONDecoder.weatherBrief.decode(
            [SystemMessage].self, from: Data(json.utf8))

        #expect(messages[0].category == "deprecation")
        #expect(messages[0].categoryLabel == "Deprecation")
    }

    @Test("Known categories carry the web's chip labels")
    func knownCategoryLabels() {
        func label(_ category: String) -> String {
            SystemMessage(id: 1, date: "2026-01-01", title: "t", body: "b",
                          category: category, highlight: false).categoryLabel
        }
        #expect(label("feature") == "New")
        #expect(label("change") == "Change")
        #expect(label("fix") == "Fix")
        #expect(label("app_release") == "iOS app")
    }

    @Test("Dates render as day/month/year, and a malformed one falls back raw")
    func displayDate() {
        func rendered(_ date: String) -> String {
            SystemMessage(id: 1, date: date, title: "t", body: "b",
                          category: "feature", highlight: false).displayDate
        }
        #expect(rendered("2026-08-08") == "8 Aug 2026")
        #expect(rendered("not-a-date") == "not-a-date")
    }

    @Test("Round-trips through the on-disk cache encoding")
    func roundTripsThroughCache() throws {
        let messages = try JSONDecoder.weatherBrief.decode(
            [SystemMessage].self, from: Data(Self.streamJSON.utf8))
        let encoded = try JSONEncoder.weatherBrief.encode(messages)
        let decoded = try JSONDecoder.weatherBrief.decode([SystemMessage].self, from: encoded)

        #expect(decoded == messages)
    }
}

@Suite("WhatsNew background sync")
struct WhatsNewSyncTests {

    @Test("An empty cache always downloads")
    func emptyCacheDownloads() {
        #expect(WhatsNewStore.needsStreamDownload(
            latestMessageDate: "2026-08-08", cachedDates: []))
    }

    @Test("A newer server date downloads; an equal or older one does not")
    func comparesDatesChronologically() {
        let cached = ["2026-07-02", "2026-08-08"]
        #expect(WhatsNewStore.needsStreamDownload(
            latestMessageDate: "2026-08-09", cachedDates: cached))
        #expect(!WhatsNewStore.needsStreamDownload(
            latestMessageDate: "2026-08-08", cachedDates: cached))
        #expect(!WhatsNewStore.needsStreamDownload(
            latestMessageDate: "2026-07-30", cachedDates: cached))
        // Cross-year, where a naive numeric parse of the day would get it wrong.
        #expect(WhatsNewStore.needsStreamDownload(
            latestMessageDate: "2027-01-01", cachedDates: cached))
    }

    @Test("A failed status read (or an empty server stream) fetches nothing")
    func nilStatusIsANoOp() {
        #expect(!WhatsNewStore.needsStreamDownload(
            latestMessageDate: nil, cachedDates: []))
        #expect(!WhatsNewStore.needsStreamDownload(
            latestMessageDate: nil, cachedDates: ["2026-08-08"]))
    }
}

@Suite("MarkdownLiteText bullets")
struct MarkdownLiteBulletTests {

    @Test("Recognises the markers release-note bodies actually use")
    func recognisesMarkers() {
        #expect(MarkdownLiteText.bulletContent("- Route SIGMETs") == "Route SIGMETs")
        #expect(MarkdownLiteText.bulletContent("• Route SIGMETs") == "Route SIGMETs")
        #expect(MarkdownLiteText.bulletContent("* Route SIGMETs") == "Route SIGMETs")
        #expect(MarkdownLiteText.bulletContent("  - indented") == "indented")
    }

    @Test("Non-bullet lines are left alone")
    func leavesProseAlone() {
        // No trailing space after the marker: a lone dash or an em-dash-led
        // clause must not be eaten as a bullet.
        #expect(MarkdownLiteText.bulletContent("-not a bullet") == nil)
        #expect(MarkdownLiteText.bulletContent("Visibility — now in km") == nil)
        #expect(MarkdownLiteText.bulletContent("") == nil)
        #expect(MarkdownLiteText.bulletContent("Wind 5 - 10 kt") == nil)
    }

    @Test("Inline emphasis is left in place for the inline-markdown parser")
    func keepsInlineMarkdown() {
        #expect(MarkdownLiteText.bulletContent("- **Live SIGMETs** show as red zones")
                == "**Live SIGMETs** show as red zones")
    }

    @Test("Authored bodies keep their own line breaks when normalisation is off")
    func authoredBodiesNeedNoNormalisation() {
        // The LLM heuristic breaks a run-on list; authored release notes already
        // carry real newlines, so the view passes `normalizeRunOnLists: false`
        // and this is what it would otherwise have done to them.
        let body = "- One thing, - and another"
        #expect(MarkdownLiteText.normalize(body).contains("\n"))
    }
}
