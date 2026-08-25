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

/// Wire-format decoding for the #571 extent-domain fields.
///
/// The label tests above build values directly; these prove the fields actually
/// survive `JSONDecoder`, in both the present and absent cases. A server that
/// omits them (an old pack) must decode to nil rather than throw, and a server
/// that sends them must land them on the right properties — the card renders a
/// percentage off `affectedDomain`, so a decoding slip shows up as an
/// unqualified "93% affected", which is the exact D3 defect being fixed.
struct AdvisoryExtentDomainDecodingTests {

    @Test func decodesTheDomainFieldsWhenPresent() throws {
        let json = """
        {"model":"icon","status":"red","detail":"Mountain wave risk over 132nm/190nm of high terrain (69%)",
         "affected_points":14,"total_points":15,"affected_pct":69.4,
         "affected_nm":131.8,"total_nm":582.0,
         "domain_nm":190.0,"affected_domain":"of high terrain"}
        """
        let result = try JSONDecoder.weatherBrief.decode(
            ModelAdvisoryResult.self, from: Data(json.utf8))
        #expect(result.domainNm == 190.0)
        #expect(result.affectedDomain == "of high terrain")
        // The denominator is mountain miles, NOT the route's 582.
        #expect(result.totalNm == 582.0)
        #expect(AdvisoryCardView.coverageLabel(
            pct: result.affectedPct, domain: result.affectedDomain)
                == "69% of high terrain affected")
    }

    @Test func anOldPackWithoutTheFieldsDecodesToNil() throws {
        let json = """
        {"model":"gfs","status":"amber","detail":"d","affected_points":1,
         "total_points":10,"affected_pct":10.0,"affected_nm":5.0,"total_nm":50.0}
        """
        let result = try JSONDecoder.weatherBrief.decode(
            ModelAdvisoryResult.self, from: Data(json.utf8))
        #expect(result.domainNm == nil)
        #expect(result.affectedDomain == nil)
        #expect(AdvisoryCardView.coverageLabel(
            pct: result.affectedPct, domain: result.affectedDomain)
                == "10% affected")
    }

    @Test func theDetailEndpointDecodesTheSameFields() throws {
        let json = """
        {"model":"icon","status":"red","detail":"d","affected_pct":69.4,
         "affected_nm":131.8,"total_nm":582.0,
         "domain_nm":190.0,"affected_domain":"of high terrain"}
        """
        let result = try JSONDecoder.weatherBrief.decode(
            ModelAdvisoryDetail.self, from: Data(json.utf8))
        #expect(result.domainNm == 190.0)
        #expect(result.affectedDomain == "of high terrain")
    }

    @Test func theDetailEndpointToleratesTheFieldsBeingAbsent() throws {
        let json = """
        {"model":"gfs","status":"green","detail":"d"}
        """
        let result = try JSONDecoder.weatherBrief.decode(
            ModelAdvisoryDetail.self, from: Data(json.utf8))
        #expect(result.domainNm == nil)
        #expect(result.affectedDomain == nil)
        #expect(result.affectedPct == nil)
    }
}
