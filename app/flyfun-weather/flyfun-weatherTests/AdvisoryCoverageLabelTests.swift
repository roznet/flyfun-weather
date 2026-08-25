//
//  AdvisoryCoverageLabelTests.swift
//  flyfun-weatherTests
//
//  The percentage an advisory card prints must name its denominator when that
//  denominator is not the whole route (#571 D3).
//
//  Background: Mountain Wind restricts its domain to the route's mountain
//  points, so its coverage is a share of *mountain miles*. Rendered unqualified,
//  ICON's "93% affected" read as route coverage for a 131.8 nm footprint on a
//  582 nm route — a ~4x overstatement, which the LLM digest then promoted to a
//  watch item. The backend now publishes `affectedDomain` alongside the
//  percentage; every surface that renders the percentage has to use it.
//

import Foundation
import Testing
@testable import flyfun_weather

struct AdvisoryCoverageLabelTests {

    @Test func routeDomainNeedsNoQualifier() {
        #expect(AdvisoryCardView.coverageLabel(pct: 40, domain: nil)
                == "40% affected")
    }

    @Test func namesADomainThatIsNotTheWholeRoute() {
        #expect(AdvisoryCardView.coverageLabel(pct: 93, domain: "of high terrain")
                == "93% of high terrain affected")
    }

    @Test func treatsAnEmptyDomainAsAbsent() {
        // Defensive: an old pack sends null, but a blank string would otherwise
        // render "93%  affected" with a doubled space.
        #expect(AdvisoryCardView.coverageLabel(pct: 93, domain: "")
                == "93% affected")
    }

    @Test func truncatesTowardTheWholePercent() {
        // Matches the Int() cast the cards have always used, so this helper is a
        // pure relocation of the label — not a formatting change.
        #expect(AdvisoryCardView.coverageLabel(pct: 33.7, domain: nil)
                == "33% affected")
    }
}
