import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import PersonalWatchCore

final class GroceryRecoveryTests: XCTestCase {
    private let now = "2026-11-02T01:00:00Z"

    func testOnlyExplicitRecoveryAcceptsEachKnownConnectionFailure() async throws {
        for code in ["network_-1003", "network_-1004", "network_-1006", "network_-1009"] {
            let f = fixture()
            defer { try? FileManager.default.removeItem(at: f.directory) }
            let request = payload()
            try await exhaust(f.store, request, code: code)
            await assertRefusedStandard(f.store, request)
            _ = try await f.store.claimDelivery(payload: request, now: now, retryMode: .manualGroceryRecovery)
            let rows = try await f.store.loadAll()
            XCTAssertEqual(rows.first?.attempts, 6)
            XCTAssertEqual(rows.first?.payload, request)
        }
    }

    func testSelectedRecoveryPreservesExactBodyAndIdentityAfterFiveFailures() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        _ = try await f.store.enqueue(request, now: now)
        let session = RecoveryRecordingSession(failures: 5)
        let delivery = service(f.store, session)
        for _ in 0..<5 {
            do { _ = try await delivery.deliver(payload: request, secret: "synthetic-only"); XCTFail() }
            catch { XCTAssertEqual(error as? OutboxDeliveryFailure, .network(-1004)) }
        }
        do { _ = try await delivery.deliver(payload: request, secret: "synthetic-only"); XCTFail() }
        catch { XCTAssertEqual(error as? OutboxStoreError, .notDeliverable) }
        let batch = try await f.store.loadDeliverable()
        XCTAssertTrue(batch.isEmpty)
        let recovered = try await delivery.deliver(payload: request, secret: "synthetic-only", retryMode: .manualGroceryRecovery)
        XCTAssertTrue(recovered.writeCommitted == true)
        let bodies = await session.bodies
        XCTAssertEqual(bodies.count, 6)
        XCTAssertTrue(bodies.allSatisfy { $0 == bodies.first })
        let recordedIDs = await session.requestIDs
        XCTAssertEqual(recordedIDs, Array(repeating: request.requestID, count: 6))
        let reopened = FileOutboxStore(fileURL: f.url)
        let reopenedRows = try await reopened.loadAll()
        let row = try XCTUnwrap(reopenedRows.first)
        XCTAssertEqual(row.payload, request)
        XCTAssertEqual(row.attempts, 6)
        XCTAssertEqual(row.status, .sent)
        _ = try await delivery.deliver(payload: request, secret: "synthetic-only", retryMode: .manualGroceryRecovery)
        let finalCount = await session.bodies.count
        XCTAssertEqual(finalCount, 6, "A saved receipt must not dispatch again")
    }

    func testAnotherRecoveryRequiresAnotherExplicitCallAndNeverResetsAttempts() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        try await exhaust(f.store, request)
        let session = RecoveryRecordingSession(failures: 100)
        let delivery = service(f.store, session)
        for expected in [6, 7] {
            do { _ = try await delivery.deliver(payload: request, secret: "synthetic-only", retryMode: .manualGroceryRecovery); XCTFail() }
            catch { XCTAssertEqual(error as? OutboxDeliveryFailure, .network(-1004)) }
            let rows = try await f.store.loadAll()
        let row = try XCTUnwrap(rows.first)
            XCTAssertEqual(row.attempts, expected)
            XCTAssertEqual(row.payload, request)
            do { _ = try await delivery.deliver(payload: request, secret: "synthetic-only"); XCTFail() }
            catch { XCTAssertEqual(error as? OutboxStoreError, .notDeliverable) }
        }
        let finalCount = await session.bodies.count
        XCTAssertEqual(finalCount, 2)
    }

    func testRecoveryRefusesOtherRoutesDryRunUnsafeAndAmbiguousFailures() async throws {
        var requests = [payload(route: .reminder), payload(route: .expense), payload(route: .general)]
        var dryRun = payload(); dryRun.context.dryRun = true; dryRun.context.allowWrite = false
        var unsafe = payload(); unsafe.context.allowFireflyWrite = true
        requests += [dryRun, unsafe]
        for request in requests {
            let f = fixture()
            defer { try? FileManager.default.removeItem(at: f.directory) }
            try await exhaust(f.store, request)
            await assertRefused(f.store, request)
        }
        for code in ["network_-1001", "network_-1005", "http_401", "http_503", "unsafe_response", "invalid_response"] {
            let f = fixture()
            defer { try? FileManager.default.removeItem(at: f.directory) }
            let request = payload()
            try await exhaust(f.store, request, code: code)
            await assertRefused(f.store, request)
        }
    }

    func testRecoveryRequiresFailedStateAndOriginalPayload() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        try await exhaust(f.store, request)
        var changed = request; changed.capture.text = "otro artículo"
        do { _ = try await f.store.claimDelivery(payload: changed, now: now, retryMode: .manualGroceryRecovery); XCTFail() }
        catch { XCTAssertEqual(error as? OutboxStoreError, .requestIDConflict) }
        _ = try await f.store.claimDelivery(payload: request, now: now, retryMode: .manualGroceryRecovery)
        do { _ = try await f.store.claimDelivery(payload: request, now: now, retryMode: .manualGroceryRecovery); XCTFail() }
        catch { XCTAssertEqual(error as? OutboxStoreError, .deliveryInProgress) }
        let rows = try await f.store.loadAll()
        let row = try XCTUnwrap(rows.first)
        XCTAssertEqual(row.attempts, 6)
        XCTAssertEqual(row.status, .sending)
    }

    func testConcurrentStoreInstancesShareOneRecoveryClaim() async throws {
        let f = fixture()
        defer { try? FileManager.default.removeItem(at: f.directory) }
        let request = payload()
        try await exhaust(f.store, request)
        let session = RecoverySuspendedSession()
        let firstService = service(f.store, session)
        let secondService = service(FileOutboxStore(fileURL: f.url), session)
        let first = Task { try await firstService.deliver(payload: request, secret: "synthetic-only", retryMode: .manualGroceryRecovery) }
        await session.waitUntilStarted()
        do { _ = try await secondService.deliver(payload: request, secret: "synthetic-only", retryMode: .manualGroceryRecovery); XCTFail() }
        catch { XCTAssertEqual(error as? OutboxStoreError, .deliveryInProgress) }
        await session.release()
        _ = try await first.value
        let calls = await session.calls
        XCTAssertEqual(calls, 1)
        let rows = try await f.store.loadAll()
        let row = try XCTUnwrap(rows.first)
        XCTAssertEqual(row.attempts, 6)
        XCTAssertEqual(row.status, .sent)
    }

    private func assertRefused(_ store: FileOutboxStore, _ request: CapturePayloadV1,
                               file: StaticString = #filePath, line: UInt = #line) async {
        do { _ = try await store.claimDelivery(payload: request, now: now, retryMode: .manualGroceryRecovery); XCTFail(file: file, line: line) }
        catch { XCTAssertEqual(error as? OutboxStoreError, .notDeliverable, file: file, line: line) }
    }

    private func assertRefusedStandard(_ store: FileOutboxStore, _ request: CapturePayloadV1) async {
        do { _ = try await store.claimDelivery(payload: request, now: now); XCTFail() }
        catch { XCTAssertEqual(error as? OutboxStoreError, .notDeliverable) }
    }

    private func exhaust(_ store: FileOutboxStore, _ request: CapturePayloadV1, code: String = "network_-1004") async throws {
        _ = try await store.enqueue(request, now: now)
        for _ in 0..<5 {
            _ = try await store.claimDelivery(payload: request, now: now)
            try await store.markFailed(requestID: request.requestID, message: code, now: now)
        }
    }

    private func fixture() -> (directory: URL, url: URL, store: FileOutboxStore) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let url = directory.appendingPathComponent("outbox.json")
        return (directory, url, FileOutboxStore(fileURL: url))
    }

    private func payload(route: CaptureRoute = .grocery) -> CapturePayloadV1 {
        CapturePayloadV1(requestID: "synthetic-grocery", createdAt: now,
            source: .init(appVersion: "0.3.4", platform: "watchOS", osVersion: "27.2", deviceID: "synthetic", surface: "watch_app"),
            route: route, capture: .init(modality: "watch_dictation", text: "arroz sintético"),
            context: .init(dryRun: false, allowWrite: true, allowFireflyWrite: false))
    }

    private func service(_ store: FileOutboxStore, _ session: URLSessioning) -> OutboxDeliveryService {
        OutboxDeliveryService(store: store, client: WebhookClient(endpoint: URL(string: "https://recovery.invalid/webhooks/mobile-capture-v1")!, session: session))
    }
}

private func recoveryResponse(_ request: URLRequest) throws -> (Data, URLResponse) {
    let payload = try JSONDecoder.captureDecoder().decode(CapturePayloadV1.self, from: request.httpBody!)
    let response = CaptureResponseV1(status: "committed", dryRun: false, requestID: payload.requestID,
        domain: "aura.grocery_capture", writeCommitted: true, plan: .init(wouldWrite: true))
    return (try JSONEncoder.captureEncoder().encode(response),
            HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
}

private actor RecoveryRecordingSession: URLSessioning {
    let failures: Int
    var bodies: [Data] = []
    var requestIDs: [String] = []
    init(failures: Int) { self.failures = failures }
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        bodies.append(request.httpBody!)
        requestIDs.append(request.value(forHTTPHeaderField: WebhookHeaders.requestID)!)
        if bodies.count <= failures { throw URLError(.cannotConnectToHost) }
        return try recoveryResponse(request)
    }
}

private actor RecoverySuspendedSession: URLSessioning {
    var calls = 0
    private var started: CheckedContinuation<Void, Never>?
    private var resumeRequest: CheckedContinuation<Void, Never>?
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        calls += 1
        started?.resume(); started = nil
        await withCheckedContinuation { resumeRequest = $0 }
        return try recoveryResponse(request)
    }
    func waitUntilStarted() async {
        if calls > 0 { return }
        await withCheckedContinuation { started = $0 }
    }
    func release() { resumeRequest?.resume(); resumeRequest = nil }
}
