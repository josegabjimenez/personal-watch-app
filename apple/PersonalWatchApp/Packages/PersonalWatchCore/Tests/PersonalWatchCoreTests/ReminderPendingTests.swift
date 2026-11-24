import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import PersonalWatchCore

final class ReminderPendingTests: XCTestCase {
    private func payload(_ route: CaptureRoute = .reminder) -> CapturePayloadV1 {
        .init(requestID: "synthetic-pending", createdAt: "2026-11-05T10:00:00-05:00",
              source: .init(appVersion: "test", platform: "watchOS", osVersion: "27", deviceID: "synthetic", surface: "watch_app"),
              route: route, capture: .init(modality: "watch_dictation", text: "Llamar mañana a las 2 pm"),
              context: .init(dryRun: false, allowWrite: true, allowFireflyWrite: false))
    }

    private func body(_ changes: [String: Any] = [:]) throws -> String {
        let envelope: [String: Any] = ["status": "error", "error": "reminder_pending_retry", "request_id": "synthetic-pending", "domain": "aura.reminder_capture"]
        let value = envelope.merging(changes) { _, next in next }
        return String(data: try JSONSerialization.data(withJSONObject: value), encoding: .utf8)!
    }

    func testPendingFailureRequiresExactReminderCorrelationAndFixedErrorCode() throws {
        let pending = WebhookClientError.unacceptableStatus(502, try body())
        XCTAssertEqual(OutboxDeliveryService.reminderPendingFailure(pending, for: payload()), .reminderPending)
        XCTAssertNil(OutboxDeliveryService.reminderPendingFailure(pending, for: payload(.grocery)))
        for changes in [["request_id": "another"], ["domain": "aura.grocery_capture"], ["status": "committed"], ["error": "untrusted saved claim"], ["extra": "unexpected"]] {
            XCTAssertNil(OutboxDeliveryService.reminderPendingFailure(WebhookClientError.unacceptableStatus(502, try body(changes)), for: payload()))
        }
        XCTAssertNil(OutboxDeliveryService.reminderPendingFailure(WebhookClientError.unacceptableStatus(401, try body()), for: payload()))
        XCTAssertEqual(OutboxDeliveryService.reminderPendingFailure(WebhookClientError.unacceptableStatus(502, try body(["error": "reminder_configuration_pending"])), for: payload()), .reminderConfigurationPending)
    }

    private func occurrence(_ time: String, _ status: String, taskID: String? = nil) -> [String: Any] {
        var value: [String: Any] = [
            "status": status,
            "due": [
                "start": "2026-11-05T\(time):00-05:00",
                "timezone": "America/Bogota",
                "display": "Texto libre que no se muestra"
            ]
        ]
        if let taskID { value["todoist_task_id"] = taskID }
        return value
    }

    func testPartialDetailsRequireOrderedCorrelatedOccurrencesAndSafeStates() throws {
        let saved = occurrence("11:00", "saved", taskID: "synthetic-task")
        let uncertain = occurrence("14:00", "unverified")
        let pending = occurrence("17:00", "pending")
        let elapsed = occurrence("10:00", "elapsed")
        let valid = [elapsed, saved, uncertain, pending]
        let body = try body(["occurrences": valid])
        let failure = OutboxDeliveryService.reminderPendingFailure(
            WebhookClientError.unacceptableStatus(502, body), for: payload()
        )
        XCTAssertEqual(failure?.localizedDescription,
            "Horarios en Bogotá · 2026-11-05 10:00 no programado (la hora pasó) · 2026-11-05 11:00 guardado en Todoist · 2026-11-05 14:00 resultado por verificar · 2026-11-05 17:00 pendiente de envío · reintenta esta captura")
        XCTAssertEqual(failure?.isTransientTransportFailure, true)
        XCTAssertNil(OutboxDeliveryService.reminderPendingFailure(
            WebhookClientError.unacceptableStatus(502, body), for: payload(.expense)
        ))

        for occurrences in [
            [uncertain, saved], [saved, occurrence("11:00", "pending")],
            [saved, occurrence("14:00", "committed")], [saved, elapsed],
            [occurrence("11:00", "saved"), uncertain], [saved, occurrence("14:00", "unverified", taskID: "unexpected")],
            [saved, occurrence("99:00", "unverified")], [saved], []
        ] {
            let malformed = try self.body(["occurrences": occurrences])
            XCTAssertNil(OutboxDeliveryService.reminderPendingFailure(
                WebhookClientError.unacceptableStatus(502, malformed), for: payload()
            ))
        }
        for extras: [String: Any] in [
            ["request_id": "different"], ["domain": "aura.grocery_capture"],
            ["display_message": "No presentar texto libre"], ["error": "reminder_configuration_pending"]
        ] {
            let malformed = try self.body(extras.merging(["occurrences": valid]) { _, next in next })
            XCTAssertNil(OutboxDeliveryService.reminderPendingFailure(
                WebhookClientError.unacceptableStatus(502, malformed), for: payload()
            ))
        }
    }

    func testPartialOutcomeKeepsSameCaptureFailedUntilTerminalResult() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = FileOutboxStore(fileURL: directory.appendingPathComponent("outbox.json"))
        let original = payload()
        _ = try await store.enqueue(original, now: original.createdAt)
        let first = occurrence("11:00", "saved", taskID: "synthetic-first")
        let partial = Data(try body(["occurrences": [first, occurrence("14:00", "unverified")]]).utf8)
        let terminalDisplay = "11:00 guardado · 14:00 guardado"
        let terminal = try JSONSerialization.data(withJSONObject: [
            "status": "committed", "dry_run": false, "write_committed": true,
            "request_id": original.requestID, "domain": original.route.domain.rawValue,
            "display_message": terminalDisplay, "plan": ["would_write": true],
            "occurrences": [first, occurrence("14:00", "saved", taskID: "synthetic-second")]
        ])
        let session = PendingSession(responses: [(502, partial), (200, terminal)])
        let delivery = OutboxDeliveryService(store: store, client: WebhookClient(endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!, session: session))
        do {
            _ = try await delivery.deliver(payload: original, secret: "synthetic-only")
            XCTFail("Partial progress is not a terminal receipt")
        } catch let failure as OutboxDeliveryFailure {
            guard case .reminderPartial = failure else { return XCTFail("Expected per-time progress: \(failure)") }
        }
        let failed = try await store.loadAll().first
        XCTAssertEqual(failed?.payload, original)
        XCTAssertEqual(failed?.status, .failed)
        XCTAssertNil(failed?.lastResponse)
        XCTAssertEqual(failed?.lastError, "reminder_pending_verification")

        let receipt = try await delivery.deliver(payload: original, secret: "synthetic-only")
        XCTAssertEqual(receipt.displayMessage, terminalDisplay)
        let sent = try await store.loadAll().first
        XCTAssertEqual(sent?.status, .sent)
        XCTAssertEqual(sent?.attempts, 2)
        XCTAssertEqual(session.requestBodies.count, 2)
        XCTAssertEqual(session.requestBodies[0], session.requestBodies[1])
    }

    func testUnknownOutcomeRetainsOriginalFailedCaptureAndNeverMarksSaved() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = FileOutboxStore(fileURL: directory.appendingPathComponent("outbox.json"))
        let original = payload()
        _ = try await store.enqueue(original, now: "2026-11-05T15:00:00Z")
        let session = PendingSession(data: Data(try body().utf8))
        let delivery = OutboxDeliveryService(store: store, client: WebhookClient(endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!, session: session))
        do {
            _ = try await delivery.deliver(payload: original, secret: "synthetic-only")
            XCTFail("An unverified outcome is not a saved receipt")
        } catch let failure as OutboxDeliveryFailure {
            XCTAssertEqual(failure, .reminderPending)
            XCTAssertTrue(failure.isTransientTransportFailure)
            XCTAssertFalse(failure.localizedDescription.contains("guardado"))
        }
        let items = try await store.loadAll()
        XCTAssertEqual(items.first?.payload, original)
        XCTAssertEqual(items.first?.status, .failed)
        XCTAssertNil(items.first?.lastResponse)
        XCTAssertEqual(items.first?.lastError, "reminder_pending_verification")
        XCTAssertEqual(items.first?.attempts, 1)
    }
}

private final class PendingSession: URLSessioning {
    private var responses: [(Int, Data)]
    private(set) var requestBodies: [Data?] = []

    init(data: Data) { self.responses = [(502, data)] }
    init(responses: [(Int, Data)]) { self.responses = responses }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        requestBodies.append(request.httpBody)
        guard !responses.isEmpty else { throw URLError(.badServerResponse) }
        let (status, data) = responses.removeFirst()
        return (data, HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }
}
