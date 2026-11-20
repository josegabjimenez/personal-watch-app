import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import PersonalWatchCore

final class ReminderRecoveryTests: XCTestCase {
    private let oldTime = "2026-10-01T14:00:00-05:00"
    private let reviewTime = "2026-11-06T17:30:00-05:00"

    func testLegacyRowsDecodeWithoutSuppressionAndExpiredPayloadBytesRemainExact() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        let original = try await f.store.enqueue(request, now: oldTime)
        let rawBefore = try Data(contentsOf: f.url)
        XCTAssertFalse(String(decoding: rawBefore, as: UTF8.self).contains("retry_suppressed_at"))
        let decoded = try JSONDecoder.captureDecoder().decode([OutboxItem].self, from: rawBefore)
        XCTAssertNil(decoded.first?.retrySuppressedAt)
        let originalBody = try requestBody(request)

        let retained = try await f.store.retainReminderForReview(requestID: request.requestID, now: reviewTime)
        XCTAssertTrue(retained.isRetrySuppressed)
        XCTAssertEqual(retained.retrySuppressedAt, reviewTime)
        XCTAssertEqual(retained.payload, original.payload)
        XCTAssertEqual(try requestBody(retained.payload), originalBody)
        XCTAssertEqual(retained.payload.createdAt, oldTime, "An expired original capture must never be rebased")
        XCTAssertEqual(retained.createdAt, original.createdAt)
        XCTAssertEqual(retained.status, .pending)
        XCTAssertEqual(retained.attempts, 0)
        XCTAssertEqual(retained.lastError, original.lastError)
        XCTAssertEqual(retained.lastResponse, original.lastResponse)
        let reopened = try await FileOutboxStore(fileURL: f.url).loadAll()
        XCTAssertEqual(reopened, [retained])
    }

    func testFailedAndExhaustedReminderRetainsAllEvidenceAndCannotBeClaimed() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        _ = try await f.store.enqueue(request, now: oldTime)
        for _ in 0..<5 {
            _ = try await f.store.claimDelivery(payload: request, now: oldTime)
            try await f.store.markFailed(requestID: request.requestID, message: "network_-1003", now: oldTime)
        }
        let beforeRows = try await f.store.loadAll()
        let before = try XCTUnwrap(beforeRows.first)
        let retained = try await f.store.retainReminderForReview(requestID: request.requestID, now: reviewTime)
        XCTAssertEqual(retained.status, .failed)
        XCTAssertEqual(retained.attempts, 5)
        XCTAssertEqual(retained.payload, before.payload)
        XCTAssertEqual(retained.lastError, "network_-1003")
        XCTAssertEqual(retained.lastResponse, before.lastResponse)
        for retryMode in [CaptureRetryMode.standard, .manualGroceryRecovery] {
            await assertRetainedClaim(f.store, request, retryMode: retryMode)
        }
        let deliverable = try await f.store.loadDeliverable(maxAttempts: 100)
        XCTAssertTrue(deliverable.isEmpty)
        let after = try await f.store.loadAll()
        XCTAssertEqual(after, [retained], "A refused retry must not increment attempts or rewrite evidence")
    }

    func testSendingReminderKeepsUnknownStateAndLateFailureKeepsSuppression() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        _ = try await f.store.enqueue(request, now: oldTime)
        _ = try await f.store.claimDelivery(payload: request, now: oldTime)
        let retained = try await f.store.retainReminderForReview(requestID: request.requestID, now: reviewTime)
        XCTAssertEqual(retained.status, .sending)
        XCTAssertEqual(retained.attempts, 1)
        await assertRetainedClaim(f.store, request)
        try await f.store.markFailed(requestID: request.requestID, message: "network_-1005", now: reviewTime)
        let loadedRows = try await f.store.loadAll()
        let row = try XCTUnwrap(loadedRows.first)
        XCTAssertTrue(row.isRetrySuppressed)
        XCTAssertEqual(row.retrySuppressedAt, reviewTime)
        XCTAssertEqual(row.status, .failed)
        XCTAssertEqual(row.lastError, "network_-1005")
        XCTAssertEqual(row.payload, request)
        XCTAssertEqual(row.attempts, 1)
        let deliverable = try await f.store.loadDeliverable()
        XCTAssertTrue(deliverable.isEmpty)
    }

    func testLateAcknowledgementPreservesSuppressionAcrossStoreInstancesWithoutAnotherTransport() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        _ = try await f.store.enqueue(request, now: oldTime)
        let session = RetentionSuspendedSession()
        let delivery = service(f.store, session)
        let first = Task { try await delivery.deliver(payload: request, secret: "synthetic-only") }
        await session.waitUntilStarted()
        let otherStore = FileOutboxStore(fileURL: f.url)
        let preserved = try await otherStore.retainReminderForReview(requestID: request.requestID, now: reviewTime)
        XCTAssertEqual(preserved.status, .sending)
        XCTAssertNil(preserved.lastResponse)
        await session.release()
        let response = try await first.value
        XCTAssertEqual(response.status, "committed")
        let afterRows = try await otherStore.loadAll()
        let after = try XCTUnwrap(afterRows.first)
        XCTAssertTrue(after.isRetrySuppressed)
        XCTAssertEqual(after.status, .sent)
        XCTAssertEqual(after.lastResponse, response)
        XCTAssertEqual(after.attempts, 1)
        XCTAssertEqual(after.payload, request)
        XCTAssertEqual(try requestBody(after.payload), try requestBody(request))
        do { _ = try await delivery.deliver(payload: request, secret: "synthetic-only"); XCTFail("Retained rows must not receive another delivery claim") }
        catch { XCTAssertEqual(error as? OutboxStoreError, .captureRetainedForReview) }
        let calls = await session.calls
        XCTAssertEqual(calls, 1)
        let retainedAgain = try await otherStore.retainReminderForReview(requestID: request.requestID, now: "2026-11-07T12:00:00Z")
        XCTAssertEqual(retainedAgain, after, "An existing suppression remains idempotent after a late result")
    }

    func testRetentionAndDeliveryClaimSerializeAcrossInstances() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        _ = try await f.store.enqueue(request, now: oldTime)
        let otherStore = FileOutboxStore(fileURL: f.url)
        let claim = Task { () -> Bool in
            do {
                _ = try await f.store.claimDelivery(payload: request, now: self.reviewTime)
                return true
            } catch {
                XCTAssertEqual(error as? OutboxStoreError, .captureRetainedForReview)
                return false
            }
        }
        let retain = Task { try await otherStore.retainReminderForReview(requestID: request.requestID, now: self.reviewTime) }
        let wasClaimed = await claim.value
        _ = try await retain.value
        let loadedRows = try await otherStore.loadAll()
        let row = try XCTUnwrap(loadedRows.first)
        XCTAssertTrue(row.isRetrySuppressed)
        XCTAssertEqual(row.status, wasClaimed ? .sending : .pending)
        XCTAssertEqual(row.attempts, wasClaimed ? 1 : 0)
        XCTAssertEqual(row.payload, request)
        await assertRetainedClaim(f.store, request)
        let deliverable = try await otherStore.loadDeliverable()
        XCTAssertTrue(deliverable.isEmpty)
    }

    func testRetainIsIdempotentAndNeverChangesOriginalFailureOrReceipt() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        var row = OutboxItem(payload: request, status: .failed, attempts: 3, lastError: "network_-1001",
                             lastDeliveryPath: .directHTTPS, lastResponse: receipt(request), createdAt: oldTime, updatedAt: oldTime)
        // A stored response does not make a failed/unknown row safe to replay.
        try seed([row], at: f.url)
        row = try await f.store.retainReminderForReview(requestID: request.requestID, now: reviewTime)
        let bytes = try Data(contentsOf: f.url)
        let repeated = try await FileOutboxStore(fileURL: f.url).retainReminderForReview(requestID: request.requestID, now: "2026-11-07T12:00:00Z")
        XCTAssertEqual(repeated, row)
        XCTAssertEqual(try Data(contentsOf: f.url), bytes)
        XCTAssertEqual(row.status, .failed)
        XCTAssertEqual(row.lastError, "network_-1001")
        XCTAssertEqual(row.lastDeliveryPath, .directHTTPS)
        XCTAssertEqual(row.lastResponse, receipt(request))
        XCTAssertEqual(row.payload.createdAt, oldTime)
    }

    func testRetentionRejectsOtherRoutesUnsafePayloadAndTerminalRowsWithoutChangingFile() async throws {
        var requests = [payload(route: .grocery), payload(route: .expense), payload(route: .general)]
        var unsafe = payload(); unsafe.context.allowFireflyWrite = true
        var confirmation = payload(); confirmation.context.userConfirmation = true
        requests += [unsafe, confirmation]
        for request in requests {
            let f = fixture()
            defer { try? FileManager.default.removeItem(at: f.directory) }
            _ = try await f.store.enqueue(request, now: oldTime)
            let before = try Data(contentsOf: f.url)
            do { _ = try await f.store.retainReminderForReview(requestID: request.requestID, now: reviewTime); XCTFail() }
            catch { XCTAssertEqual(error as? OutboxStoreError, .notDeliverable) }
            XCTAssertEqual(try Data(contentsOf: f.url), before)
        }
        for status in [OutboxStatus.sent, .cancelled] {
            let f = fixture()
            defer { try? FileManager.default.removeItem(at: f.directory) }
            let request = payload()
            try seed([OutboxItem(payload: request, status: status, createdAt: oldTime, updatedAt: oldTime)], at: f.url)
            let before = try Data(contentsOf: f.url)
            do { _ = try await f.store.retainReminderForReview(requestID: request.requestID, now: reviewTime); XCTFail() }
            catch { XCTAssertEqual(error as? OutboxStoreError, .notDeliverable) }
            XCTAssertEqual(try Data(contentsOf: f.url), before)
        }
    }

    func testMissingRowAndPersistenceFailureNeverClaimLocalRetention() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        do { _ = try await f.store.retainReminderForReview(requestID: "missing", now: reviewTime); XCTFail() }
        catch { XCTAssertEqual(error as? OutboxStoreError, .notEnqueued) }
        let request = payload()
        _ = try await f.store.enqueue(request, now: oldTime)
        let before = try Data(contentsOf: f.url)
        let failing = FileOutboxStore(fileURL: f.url, encoder: RetentionFailingEncoder())
        do { _ = try await failing.retainReminderForReview(requestID: request.requestID, now: reviewTime); XCTFail() }
        catch { XCTAssertTrue(error is RetentionEncodingFailure) }
        XCTAssertEqual(try Data(contentsOf: f.url), before)
        let storedRows = try await f.store.loadAll()
        XCTAssertFalse(try XCTUnwrap(storedRows.first).isRetrySuppressed)
    }

    func testNewIntentIsSeparateWhileExpiredRetainedReminderRemainsUntouched() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let old = payload()
        _ = try await f.store.enqueue(old, now: oldTime)
        let preserved = try await f.store.retainReminderForReview(requestID: old.requestID, now: reviewTime)
        var new = payload()
        new.requestID = "synthetic-new-intent"
        new.createdAt = reviewTime
        new.capture.text = "Otra tarea intencional mañana a las dos"
        _ = try await f.store.enqueue(new, now: reviewTime)
        let rows = try await f.store.loadAll()
        XCTAssertEqual(rows.first(where: { $0.id == old.requestID }), preserved)
        XCTAssertEqual(rows.first(where: { $0.id == new.requestID })?.attempts, 0)
        let eligible = try await f.store.loadDeliverable()
        XCTAssertEqual(eligible.map(\.payload), [new])
        XCTAssertEqual(try requestBody(preserved.payload), try requestBody(old))
        XCTAssertEqual(preserved.payload.createdAt, oldTime)
        await assertRetainedClaim(f.store, old)
    }

    func testSafeHistoricalDryRunReminderCanBePreservedLocally() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        var request = payload()
        request.context.dryRun = true
        request.context.allowWrite = false
        _ = try await f.store.enqueue(request, now: oldTime)
        let retained = try await f.store.retainReminderForReview(requestID: request.requestID, now: reviewTime)
        XCTAssertTrue(retained.isRetrySuppressed)
        XCTAssertEqual(retained.payload, request)
        XCTAssertEqual(retained.status, .pending)
    }

    private func assertRetainedClaim(_ store: FileOutboxStore, _ request: CapturePayloadV1,
                                     retryMode: CaptureRetryMode = .standard,
                                     file: StaticString = #filePath, line: UInt = #line) async {
        do { _ = try await store.claimDelivery(payload: request, now: reviewTime, retryMode: retryMode); XCTFail(file: file, line: line) }
        catch { XCTAssertEqual(error as? OutboxStoreError, .captureRetainedForReview, file: file, line: line) }
    }

    private func fixture() -> (directory: URL, url: URL, store: FileOutboxStore) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let url = directory.appendingPathComponent("outbox.json")
        return (directory, url, FileOutboxStore(fileURL: url))
    }

    private func payload(route: CaptureRoute = .reminder) -> CapturePayloadV1 {
        CapturePayloadV1(requestID: "synthetic-old-reminder", createdAt: oldTime,
            source: .init(appVersion: "0.3.5", platform: "watchOS", osVersion: "27.2", deviceID: "synthetic", surface: "watch_app"),
            route: route, capture: .init(modality: "watch_dictation", text: "Recuérdame llamar en diez minutos"),
            context: .init(dryRun: false, allowWrite: true, allowFireflyWrite: false))
    }

    private func requestBody(_ payload: CapturePayloadV1) throws -> Data {
        try XCTUnwrap(WebhookClient(endpoint: URL(string: "https://retention.invalid/webhooks/mobile-capture-v1")!)
            .makeRequest(payload: payload, secret: "synthetic-only", timestamp: "1793980000").httpBody)
    }

    private func receipt(_ payload: CapturePayloadV1) -> CaptureResponseV1 {
        CaptureResponseV1(status: "committed", dryRun: false, requestID: payload.requestID,
                          domain: "aura.reminder_capture", writeCommitted: true,
                          plan: .init(wouldWrite: true),
                          externalIDs: ["todoist_task_id": .string("synthetic-existing-task")])
    }

    private func service(_ store: FileOutboxStore, _ session: URLSessioning) -> OutboxDeliveryService {
        OutboxDeliveryService(store: store, client: WebhookClient(endpoint: URL(string: "https://retention.invalid/webhooks/mobile-capture-v1")!, session: session))
    }

    private func seed(_ rows: [OutboxItem], at url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder.captureEncoder().encode(rows).write(to: url)
    }
}

private enum RetentionEncodingFailure: Error { case expected }
private final class RetentionFailingEncoder: JSONEncoder, @unchecked Sendable {
    override func encode<T>(_ value: T) throws -> Data where T: Encodable {
        throw RetentionEncodingFailure.expected
    }
}

private actor RetentionSuspendedSession: URLSessioning {
    var calls = 0
    private var started: CheckedContinuation<Void, Never>?
    private var resumeRequest: CheckedContinuation<Void, Never>?
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        calls += 1
        started?.resume(); started = nil
        await withCheckedContinuation { resumeRequest = $0 }
        let payload = try JSONDecoder.captureDecoder().decode(CapturePayloadV1.self, from: request.httpBody!)
        let response = CaptureResponseV1(status: "committed", dryRun: false, requestID: payload.requestID,
                                        domain: "aura.reminder_capture", writeCommitted: true,
                                        plan: .init(wouldWrite: true),
                                        externalIDs: ["todoist_task_id": .string("synthetic-existing-task")])
        return (try JSONEncoder.captureEncoder().encode(response),
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
    func waitUntilStarted() async {
        if calls > 0 { return }
        await withCheckedContinuation { started = $0 }
    }
    func release() { resumeRequest?.resume(); resumeRequest = nil }
}
