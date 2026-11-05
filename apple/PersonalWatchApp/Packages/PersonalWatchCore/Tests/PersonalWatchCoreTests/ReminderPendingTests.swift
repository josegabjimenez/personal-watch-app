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

    private func body(_ changes: [String: String] = [:]) throws -> String {
        let value = ["status": "error", "error": "reminder_pending_retry", "request_id": "synthetic-pending", "domain": "aura.reminder_capture"].merging(changes) { _, next in next }
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
    let data: Data
    init(data: Data) { self.data = data }
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        (data, HTTPURLResponse(url: request.url!, statusCode: 502, httpVersion: nil, headerFields: nil)!)
    }
}
