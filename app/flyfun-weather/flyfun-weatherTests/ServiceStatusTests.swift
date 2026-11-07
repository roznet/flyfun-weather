//
//  ServiceStatusTests.swift
//  flyfun-weatherTests
//
//  The server's status page (scripts/ops/maintenance.py + Caddy handle_errors):
//  parsing its JSON, the error text a gateway 5xx shows, and that it counts as
//  transient so the offline queues and caches take over.
//

import Testing
import Foundation
@testable import flyfun_weather

struct ServiceStatusTests {
    private func body(_ object: [String: Any?]) -> Data {
        try! JSONSerialization.data(withJSONObject: object.mapValues { $0 ?? NSNull() })
    }

    @Test func parsesTheMaintenanceBody() {
        let data = body([
            "service_status": "maintenance",
            "detail": "FlyFun Weather is down for planned maintenance. Expected back around 14:30Z.",
            "until": "2026-11-07T14:30:00Z",
            "updated_at": "2026-11-07T13:00:00Z",
        ])
        let status = ServiceStatus.parse(statusCode: 503, body: data)
        #expect(status?.kind == .maintenance)
        #expect(status?.message.hasPrefix("FlyFun Weather is down for planned maintenance") == true)
        #expect(status?.until == ISO8601DateFormatter().date(from: "2026-11-07T14:30:00Z"))
    }

    @Test func parsesTheGenericOutageBodyOnA502() {
        let status = ServiceStatus.parse(statusCode: 502, body: body([
            "service_status": "unavailable", "detail": "", "until": nil,
        ]))
        #expect(status == ServiceStatus(kind: .unavailable, message: ServiceStatus.genericMessage, until: nil))
    }

    @Test func leavesAppErrorsAlone() {
        // FastAPI's own 5xx carry detail but no service_status.
        #expect(ServiceStatus.parse(statusCode: 503, body: body(["detail": "Pack not ready"])) == nil)
        #expect(ServiceStatus.parse(statusCode: 500, body: Data("<html>oops</html>".utf8)) == nil)
        #expect(ServiceStatus.parse(statusCode: 409, body: body(["service_status": "maintenance"])) == nil)
        #expect(ServiceStatus.parse(statusCode: 503, body: body(["service_status": "weird"])) == nil)
    }

    @Test func gatewayErrorsReadAsASentence() {
        let sentence = "FlyFun Weather is down for planned maintenance."
        #expect(APIError.serverError(503, sentence).errorDescription == sentence)
        // An older proxy: empty or HTML body -> generic sentence, no raw markup.
        #expect(APIError.serverError(502, nil).errorDescription == ServiceStatus.genericMessage)
        #expect(APIError.serverError(502, "<html><body>Bad Gateway</body></html>").errorDescription
                == ServiceStatus.genericMessage)
        // Non-gateway codes keep their code.
        #expect(APIError.serverError(500, "boom").errorDescription == "Server error 500: boom")
    }

    @Test func gatewayErrorsAreTransient() {
        #expect(APIError.serverError(502, nil).isTransientNetwork)
        #expect(APIError.serverError(503, "maintenance").isTransientNetwork)
        #expect(APIError.serverError(504, nil).isTransientNetwork)
        #expect(!APIError.serverError(500, nil).isTransientNetwork)
        #expect(!APIError.serverError(429, nil).isTransientNetwork)
    }

    /// A 503 and a 2xx racing: their main-actor hops can run in either order,
    /// and the banner must end on the last report, never stuck up.
    @MainActor
    @Test func outOfOrderReportsConvergeOnTheLatest() async {
        let status = ServiceStatus(kind: .maintenance, message: "race", until: nil)
        ServiceStatusStore.noteUnavailable(status)
        ServiceStatusStore.noteReachable()
        for _ in 0..<20 { await Task.yield() }
        #expect(ServiceStatusStore.shared.current == nil)

        ServiceStatusStore.noteReachable()  // nothing shown: no hop
        ServiceStatusStore.noteUnavailable(status)
        for _ in 0..<20 { await Task.yield() }
        #expect(ServiceStatusStore.shared.current == status)

        ServiceStatusStore.noteReachable()
        for _ in 0..<20 { await Task.yield() }
        #expect(ServiceStatusStore.shared.current == nil)
    }

    @MainActor
    @Test func storeReportsAndClears() {
        let store = ServiceStatusStore()
        let status = ServiceStatus(kind: .maintenance, message: "x", until: nil)
        store.report(status)
        #expect(store.current == status)
        store.clear()
        #expect(store.current == nil)
    }
}
