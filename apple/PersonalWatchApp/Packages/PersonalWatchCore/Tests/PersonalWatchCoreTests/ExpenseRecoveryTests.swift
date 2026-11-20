import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import PersonalWatchCore

final class ExpenseRecoveryTests: XCTestCase {
    private let now = "2026-11-19T14:00:00Z"
    private let retainedAt = "2026-11-19T14:01:00Z"
    private let operationID = "exp_" + String(repeating: "a", count: 32)
    private let token = "synthetic-preview-token"

    func testFailedPreviewSurvivesReopeningWithASeparateDraftAndNoRetryClaim() async throws {
        let url = temporaryOutboxURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = FileOutboxStore(fileURL: url)
        let original = expense("synthetic-original", text: "45 mil en taxi")
        _ = try await store.enqueue(original, now: now)
        _ = try await store.claimDelivery(payload: original, now: now)
        try await store.markFailed(requestID: original.requestID, message: "unsafe_response", now: now)
        let retryCandidates = try await store.loadDeliverable()
        let previouslyListed = try XCTUnwrap(retryCandidates.first)
        XCTAssertEqual(previouslyListed.payload, original)

        let retained = try await store.retainInitialCaptureForReview(requestID: original.requestID, route: .expense, now: retainedAt)
        XCTAssertEqual(retained.payload, original)
        XCTAssertEqual(retained.createdAt, now)
        XCTAssertEqual(retained.status, .failed)
        XCTAssertEqual(retained.attempts, 1)
        XCTAssertEqual(retained.lastError, "unsafe_response")
        XCTAssertNil(retained.lastResponse)
        XCTAssertEqual(retained.retrySuppressedAt, retainedAt)
        XCTAssertEqual(OutboxPresentation.retentionDetail(for: retained), "El resultado del envío anterior sigue por verificar.")

        let reopened = FileOutboxStore(fileURL: url)
        do {
            _ = try await reopened.claimDelivery(payload: previouslyListed.payload, now: retainedAt)
            XCTFail("A previously listed row must be checked again before sending")
        } catch {
            XCTAssertEqual(error as? OutboxStoreError, .captureRetainedForReview)
        }
        let fresh = expense("synthetic-fresh", text: "30 mil en libros")
        _ = try await reopened.enqueue(fresh, now: retainedAt)
        let rows = try await reopened.loadAll()
        XCTAssertEqual(rows.first, retained)
        let deliverable = try await reopened.loadDeliverable()
        XCTAssertEqual(deliverable.map(\.payload), [fresh])
    }

    func testLatePreviewIsPreservedWithoutBecomingAnActiveConfirmation() async throws {
        let url = temporaryOutboxURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = FileOutboxStore(fileURL: url)
        let original = expense("synthetic-original", text: "45 mil en taxi")
        _ = try await store.enqueue(original, now: now)
        let preview = preview(for: original, token: token)
        let transport = RetainedExpensePreviewTransport(preview: preview)
        let client = WebhookClient(endpoint: URL(string: "https://expense-recovery.invalid/capture")!, session: transport)
        let delivery = OutboxDeliveryService(store: store, client: client)
        let inFlight = Task { try await delivery.deliver(payload: original, secret: "synthetic-only") }
        await transport.waitUntilStarted()
        let reopened = FileOutboxStore(fileURL: url)
        let retained: OutboxItem
        do {
            retained = try await reopened.retainInitialCaptureForReview(requestID: original.requestID, route: .expense, now: retainedAt)
        } catch {
            await transport.release()
            throw error
        }
        XCTAssertEqual(retained.status, .sending)
        XCTAssertEqual(retained.attempts, 1)
        await transport.release()
        let result = try await inFlight.value
        XCTAssertEqual(result.status, "rejected")
        XCTAssertEqual(result.writeCommitted, false)
        XCTAssertNil(result.confirmationToken)
        XCTAssertNil(result.confirmation)

        let rows = try await reopened.loadAll()
        let historical = try XCTUnwrap(rows.first)
        XCTAssertEqual(historical.payload, original)
        XCTAssertEqual(historical.status, .sent)
        XCTAssertEqual(historical.attempts, 1)
        XCTAssertEqual(historical.retrySuppressedAt, retainedAt)
        XCTAssertEqual(historical.lastResponse, preview)
        XCTAssertEqual(OutboxPresentation.message(for: historical), "Vista previa recibida · conservada en Historial")
        let pending = try await reopened.loadPendingExpenseConfirmations()
        XCTAssertTrue(pending.isEmpty)
        let staleConfirmation = expense("synthetic-stale-confirm", text: original.capture.text, confirm: true)
        do {
            _ = try await reopened.enqueue(staleConfirmation, now: retainedAt)
            XCTFail("A retained preview must not authorize a new confirmation")
        } catch {
            XCTAssertEqual(error as? OutboxStoreError, .notDeliverable)
        }
    }

    func testLateRetainedPreviewDoesNotOverrideAnActivePreviewForTheSameOperation() async throws {
        for retainedReceiptArrivesLast in [true, false] {
            let url = temporaryOutboxURL()
            defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
            let store = FileOutboxStore(fileURL: url)
            let original = expense("synthetic-original", text: "45 mil en taxi")
            let fresh = expense("synthetic-new", text: original.capture.text)
            for payload in [original, fresh] {
                _ = try await store.enqueue(payload, now: now)
                _ = try await store.claimDelivery(payload: payload, now: now)
            }
            try await store.retainInitialCaptureForReview(requestID: original.requestID, route: .expense, now: retainedAt)
            let oldPreview = preview(for: original, token: "synthetic-retained-token")
            if retainedReceiptArrivesLast {
                try await store.markSent(requestID: fresh.requestID, now: retainedAt, response: preview(for: fresh, token: token))
                try await store.markSent(requestID: original.requestID, now: retainedAt, response: oldPreview)
            } else {
                try await store.markSent(requestID: original.requestID, now: retainedAt, response: oldPreview)
                try await store.markSent(requestID: fresh.requestID, now: retainedAt, response: preview(for: fresh, token: token))
            }
            let reopened = FileOutboxStore(fileURL: url)
            let pending = try await reopened.loadPendingExpenseConfirmations()
            XCTAssertEqual(pending.map(\.id), [fresh.requestID])

            var stale = expense("synthetic-retained-confirmation", text: original.capture.text, confirm: true)
            stale.context.confirmationToken = oldPreview.confirmationToken
            do {
                _ = try await reopened.enqueue(stale, now: retainedAt)
                XCTFail("Only the active token can authorize a confirmation for the reused operation")
            } catch {
                XCTAssertEqual(error as? OutboxStoreError, .notDeliverable)
            }
            let active = expense("synthetic-active-confirmation", text: fresh.capture.text, confirm: true)
            let queued = try await reopened.enqueue(active, now: retainedAt)
            XCTAssertEqual(queued.payload, active)
        }
    }

    func testConfirmationAndCancellationAreIneligibleForRetention() async throws {
        let url = temporaryOutboxURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = FileOutboxStore(fileURL: url)
        let confirm = expense("synthetic-confirm", text: "45 mil en taxi", confirm: true)
        let cancel = expense("synthetic-cancel", text: "45 mil en taxi", cancel: true)
        for action in [confirm, cancel] {
            let queued = try await store.enqueue(action, now: now)
            do {
                _ = try await store.retainInitialCaptureForReview(requestID: action.requestID, route: .expense, now: retainedAt)
                XCTFail("Only an initial expense is eligible")
            } catch {
                XCTAssertEqual(error as? OutboxStoreError, .notDeliverable)
            }
            let rows = try await store.loadAll()
            XCTAssertEqual(rows.last, queued)
        }
    }

    private func preview(for payload: CapturePayloadV1, token: String) -> CaptureResponseV1 {
        CaptureResponseV1(
            status: "needs_confirmation", dryRun: false, requestID: payload.requestID,
            domain: payload.route.domain.rawValue, operationID: operationID, confirmationToken: token,
            confirmation: .init(amountCOP: 45_000, description: "Taxi", sourceName: "Efectivo", categoryName: "Transporte", budgetName: "Movilidad"),
            writeCommitted: false, plan: .init(sideEffects: [], wouldWrite: false)
        )
    }

    private func temporaryOutboxURL() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("outbox.json")
    }

    private func expense(_ id: String, text: String, confirm: Bool = false, cancel: Bool = false) -> CapturePayloadV1 {
        let capturedAt = now
        return CaptureFactory(appVersion: "synthetic", platform: "watchOS", osVersion: "synthetic",
                              deviceID: "synthetic-device", surface: "synthetic",
                              nowISO8601: { capturedAt }, makeRequestID: { id })
            .makePayload(kind: .expense, text: text, requestWrite: true, userConfirmation: confirm,
                         userCancellation: cancel, confirmationToken: confirm ? token : nil,
                         operationID: confirm || cancel ? operationID : nil)
    }
}

private actor RetainedExpensePreviewTransport: URLSessioning {
    let preview: CaptureResponseV1
    private var started: CheckedContinuation<Void, Never>?
    private var releaseResponse: CheckedContinuation<Void, Never>?

    init(preview: CaptureResponseV1) {
        self.preview = preview
    }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        await withCheckedContinuation {
            releaseResponse = $0
            started?.resume()
            started = nil
        }
        let url = try XCTUnwrap(request.url)
        let response = try XCTUnwrap(HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil))
        return (try JSONEncoder.captureEncoder().encode(preview), response)
    }

    func waitUntilStarted() async {
        if releaseResponse != nil { return }
        await withCheckedContinuation { started = $0 }
    }

    func release() {
        releaseResponse?.resume()
        releaseResponse = nil
    }
}
