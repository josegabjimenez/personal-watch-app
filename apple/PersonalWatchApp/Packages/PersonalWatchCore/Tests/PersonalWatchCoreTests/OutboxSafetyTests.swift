import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import PersonalWatchCore

final class OutboxSafetyTests: XCTestCase {
    private let op = "exp_0123456789abcdef0123456789abcdef"
    private let otherOp = "exp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    private let now = "2026-10-25T01:00:00Z"

    func testCancellationAtomicallySuppressesUnsentConfirmationAndPreservesAuditAcrossRelaunch() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        try await seedPreview(fixture.store, operation: otherOp, id: "other-preview")
        let confirmation = payload(id: "confirm", operation: op, confirm: true)
        _ = try await fixture.store.enqueue(confirmation, now: now)
        let cancelled = try await fixture.store.enqueue(payload(id: "cancel", operation: op, cancel: true), now: now)
        XCTAssertEqual(cancelled.cancellationMayHaveBeenSent, false)
        let reopened = FileOutboxStore(fileURL: fixture.url)
        let items = try await reopened.loadAll()
        XCTAssertEqual(items.count, 4)
        XCTAssertEqual(items.first { $0.id == "confirm" }?.status, .cancelled)
        XCTAssertEqual(items.first { $0.id == "preview" }?.lastResponse?.operationID, op)
        XCTAssertNotNil(items.first { $0.id == "preview" }?.confirmationInvalidatedAt)
        let visible = try await reopened.loadPendingExpenseConfirmations()
        XCTAssertEqual(visible.map(\.id), ["other-preview"])
        let deliverable = try await reopened.loadDeliverable()
        XCTAssertEqual(deliverable.map(\.id), ["cancel"])
        do {
            _ = try await reopened.claimDelivery(payload: confirmation, now: now)
            XCTFail("Cancellation must prevent a later retry claim")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .operationCancelled) }
        do {
            _ = try await reopened.enqueue(payload(id: "new-confirm", operation: op, confirm: true), now: now)
            XCTFail("Cancellation tombstone must reject fresh confirmation IDs")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .operationCancelled) }
    }

    func testFailedACKLossCancellationIsUncertainAndSuppressesFutureRetry() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        let confirmation = payload(id: "confirm", operation: op, confirm: true)
        _ = try await fixture.store.enqueue(confirmation, now: now)
        _ = try await fixture.store.claimDelivery(payload: confirmation, now: now)
        try await fixture.store.markFailed(requestID: confirmation.requestID, message: "network_-1005", now: now)
        let cancelled = try await fixture.store.enqueue(payload(id: "cancel", operation: op, cancel: true), now: now)
        XCTAssertEqual(cancelled.cancellationMayHaveBeenSent, true)
        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first { $0.id == "confirm" }?.status, .cancelled)
        XCTAssertEqual(items.first { $0.id == "confirm" }?.attempts, 1)
        let deliverable = try await fixture.store.loadDeliverable()
        XCTAssertFalse(deliverable.contains { $0.id == "confirm" })
    }

    func testSendingAfterRelaunchKeepsPreviewAccessibleForUncertainCancellation() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        let confirmation = payload(id: "confirm", operation: op, confirm: true)
        _ = try await fixture.store.enqueue(confirmation, now: now)
        _ = try await fixture.store.claimDelivery(payload: confirmation, now: now)
        let reopened = FileOutboxStore(fileURL: fixture.url)
        let visible = try await reopened.loadPendingExpenseConfirmations()
        XCTAssertEqual(visible.map(\.id), ["preview"])
        do {
            _ = try await reopened.claimDelivery(payload: confirmation, now: now)
            XCTFail("A sending claim cannot be reclaimed")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .deliveryInProgress) }
        let cancellation = try await reopened.enqueue(payload(id: "cancel", operation: op, cancel: true), now: now)
        XCTAssertEqual(cancellation.cancellationMayHaveBeenSent, true)
        let afterCancel = try await reopened.loadPendingExpenseConfirmations()
        XCTAssertTrue(afterCancel.isEmpty)
    }

    func testRetryClaimAndCancellationSerializeAcrossStoreInstances() async throws {
        for index in 0..<30 {
            let fixture = fixture()
            defer { try? FileManager.default.removeItem(at: fixture.directory) }
            let confirmation = payload(id: "confirm-\(index)", operation: op, confirm: true)
            _ = try await fixture.store.enqueue(confirmation, now: now)
            let otherStore = FileOutboxStore(fileURL: fixture.url)
            let cancellation = payload(id: "cancel-\(index)", operation: op, cancel: true)
            async let claim = try? fixture.store.claimDelivery(payload: confirmation, now: now)
            async let cancel = otherStore.enqueue(cancellation, now: now)
            let (claimed, cancelled) = try await (claim, cancel)
            let items = try await otherStore.loadAll()
            let stored = try XCTUnwrap(items.first { $0.id == confirmation.requestID })
            if claimed != nil {
                XCTAssertEqual(stored.status, .sending)
                XCTAssertEqual(cancelled.cancellationMayHaveBeenSent, true)
            } else {
                XCTAssertEqual(stored.status, .cancelled)
                XCTAssertEqual(cancelled.cancellationMayHaveBeenSent, false)
            }
            let deliverable = try await otherStore.loadDeliverable()
            XCTAssertFalse(deliverable.contains { $0.id == confirmation.requestID })
        }
    }

    func testConcurrentSameIDDeliveryUsesOneTransportAndCachedTerminalReplay() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let request = payload(id: "same-id", operation: nil)
        _ = try await fixture.store.enqueue(request, now: now)
        let response = CaptureResponseV1(status: "accepted", dryRun: true, requestID: request.requestID,
                                         domain: request.route.domain.rawValue, plan: .init(wouldWrite: false))
        let session = SuspendedSession(response: response)
        let first = OutboxDeliveryService(store: fixture.store, client: client(session))
        let second = OutboxDeliveryService(store: FileOutboxStore(fileURL: fixture.url), client: client(session))
        let sending = Task { try await first.deliver(payload: request, secret: "synthetic") }
        await session.waitUntilStarted()
        do {
            _ = try await second.deliver(payload: request, secret: "synthetic")
            XCTFail("Concurrent delivery must fail before a second transport")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .deliveryInProgress) }
        await session.release()
        let firstResult = try await sending.value
        let replay = try await second.deliver(payload: request, secret: "synthetic")
        XCTAssertEqual(firstResult, response)
        XCTAssertEqual(replay, response)
        let calls = await session.calls
        XCTAssertEqual(calls, 1)
        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.attempts, 1)
    }

    func testDifferentConfirmationIDsReuseFirstOperationRequestID() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let otherStore = FileOutboxStore(fileURL: fixture.url)
        async let one = fixture.store.enqueue(payload(id: "tap-one", operation: op, confirm: true), now: now)
        async let two = otherStore.enqueue(payload(id: "tap-two", operation: op, confirm: true), now: now)
        let (first, second) = try await (one, two)
        XCTAssertEqual(first.id, second.id)
        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.count, 1)
    }

    func testConflictingSameRequestIDDoesNotOverwriteOrTransmitNewPayload() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let original = payload(id: "collision", operation: nil)
        _ = try await fixture.store.enqueue(original, now: now)
        var changed = original
        changed.capture.text = "different"
        do {
            _ = try await fixture.store.enqueue(changed, now: now)
            XCTFail("A request ID must bind one immutable payload")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .requestIDConflict) }
        do {
            _ = try await fixture.store.claimDelivery(payload: changed, now: now)
            XCTFail("A conflicting caller cannot claim the stored ID")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .requestIDConflict) }
        let items = try await fixture.store.loadAll()
        XCTAssertEqual(items.first?.payload, original)
        XCTAssertEqual(items.first?.attempts, 0)
    }

    func testCorrelatedTerminalRejectionInvalidatesPreviewDurablyFor200And422() async throws {
        for statusCode in [200, 422] {
            let fixture = fixture()
            defer { try? FileManager.default.removeItem(at: fixture.directory) }
            try await seedPreview(fixture.store, operation: op, id: "preview")
            try await seedPreview(fixture.store, operation: otherOp, id: "other")
            let confirmation = payload(id: "confirm", operation: op, confirm: true)
            _ = try await fixture.store.enqueue(confirmation, now: now)
            let rejection = CaptureResponseV1(status: "rejected", dryRun: false, requestID: confirmation.requestID,
                domain: confirmation.route.domain.rawValue, operationID: op, displayMessage: "Confirmación vencida",
                writeCommitted: false, plan: .init(wouldWrite: false))
            let delivery = OutboxDeliveryService(store: fixture.store, client: client(ImmediateSession(response: rejection, status: statusCode)))
            let result = try await delivery.deliver(payload: confirmation, secret: "synthetic")
            XCTAssertEqual(result.status, "rejected")
            let reopened = FileOutboxStore(fileURL: fixture.url)
            let visible = try await reopened.loadPendingExpenseConfirmations()
            XCTAssertEqual(visible.map(\.id), ["other"])
            let items = try await reopened.loadAll()
            let preview = try XCTUnwrap(items.first { $0.id == "preview" })
            XCTAssertEqual(preview.lastResponse?.operationID, op)
            XCTAssertEqual(preview.confirmationInvalidationReason, "rejected")
            XCTAssertEqual(items.first { $0.id == "confirm" }?.status, .sent)
        }
    }

    func testGenericNetworkFailurePreservesPreviewAndCanonicalRetryOnRelaunch() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        let confirmation = payload(id: "confirm", operation: op, confirm: true)
        _ = try await fixture.store.enqueue(confirmation, now: now)
        let delivery = OutboxDeliveryService(store: fixture.store, client: client(FailingSession()))
        do { _ = try await delivery.deliver(payload: confirmation, secret: "synthetic"); XCTFail("Expected offline") }
        catch let error as OutboxDeliveryFailure { XCTAssertEqual(error, .network(URLError.notConnectedToInternet.rawValue)) }
        let reopened = FileOutboxStore(fileURL: fixture.url)
        let visible = try await reopened.loadPendingExpenseConfirmations()
        XCTAssertEqual(visible.map(\.id), ["preview"])
        XCTAssertNil(visible.first?.confirmationInvalidatedAt)
        let retry = try await reopened.enqueue(payload(id: "new-tap", operation: op, confirm: true), now: now)
        XCTAssertEqual(retry.id, "confirm")
        XCTAssertEqual(retry.attempts, 1)
    }

    func testUncorrelated422DoesNotInvalidateValidPreview() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        let confirmation = payload(id: "confirm", operation: op, confirm: true)
        _ = try await fixture.store.enqueue(confirmation, now: now)
        let rejection = CaptureResponseV1(status: "rejected", dryRun: false, requestID: confirmation.requestID,
            domain: confirmation.route.domain.rawValue, operationID: otherOp,
            writeCommitted: false, plan: .init(wouldWrite: false))
        let delivery = OutboxDeliveryService(store: fixture.store, client: client(ImmediateSession(response: rejection, status: 422)))
        do { _ = try await delivery.deliver(payload: confirmation, secret: "synthetic"); XCTFail("Expected uncorrelated failure") }
        catch let error as OutboxDeliveryFailure { XCTAssertEqual(error, .http(422)) }
        let visible = try await fixture.store.loadPendingExpenseConfirmations()
        XCTAssertEqual(visible.map(\.id), ["preview"])
    }

    func testCancellationPersistenceFailureLeavesPreviewAvailableAndReturnsNoSuccess() async throws {
        let fixture = fixture()
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: fixture.directory.path)
            try? FileManager.default.removeItem(at: fixture.directory)
        }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: fixture.directory.path)
        if FileManager.default.isWritableFile(atPath: fixture.directory.path) {
            throw XCTSkip("This filesystem/user does not enforce directory write permissions")
        }
        do {
            _ = try await fixture.store.enqueue(payload(id: "cancel", operation: op, cancel: true), now: now)
            XCTFail("Cancellation must not report success if atomic persistence fails")
        } catch { /* An I/O error is required before any local success. */ }
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: fixture.directory.path)
        let reopened = FileOutboxStore(fileURL: fixture.url)
        let visible = try await reopened.loadPendingExpenseConfirmations()
        XCTAssertEqual(visible.map(\.id), ["preview"])
        let items = try await reopened.loadAll()
        XCTAssertEqual(items.count, 1)
    }

    func testCancelledCachedPreviewCannotReturnRegisterAgain() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        let items = try await fixture.store.loadAll()
        let preview = try XCTUnwrap(items.first?.payload)
        _ = try await fixture.store.enqueue(payload(id: "cancel", operation: op, cancel: true), now: now)
        do {
            _ = try await fixture.store.claimDelivery(payload: preview, now: now)
            XCTFail("An old cached preview must not resurrect Register after cancellation")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .operationCancelled) }
    }

    func testInvalidatedCachedPreviewCannotReturnRegisterAgain() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        let items = try await fixture.store.loadAll()
        let preview = try XCTUnwrap(items.first?.payload)
        _ = try await fixture.store.consumeExpenseConfirmation(operationID: op, now: now)
        do {
            _ = try await fixture.store.claimDelivery(payload: preview, now: now)
            XCTFail("An invalidated cached preview cannot be replayed")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .notDeliverable) }
    }

    func testNeutralInactivePreviewReplayInvalidatesOlderRegisterAndPreservesAudit() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "old-preview")
        var request = payload(id: "replayed-preview", operation: op)
        request.context.operationID = nil
        _ = try await fixture.store.enqueue(request, now: now)
        var response = CaptureResponseV1(status: "rejected", dryRun: false, requestID: request.requestID,
            domain: request.route.domain.rawValue, operationID: op, writeCommitted: false,
            plan: .init(wouldWrite: false), externalIDs: ["firefly_journal_ids": .array([.string("synthetic-journal")])])
        XCTAssertTrue(OutboxDeliveryService.isSafe(response: response, for: request))
        let delivery = OutboxDeliveryService(store: fixture.store, client: client(ImmediateSession(response: response)))
        _ = try await delivery.deliver(payload: request, secret: "synthetic")
        let reopened = FileOutboxStore(fileURL: fixture.url)
        let visible = try await reopened.loadPendingExpenseConfirmations()
        XCTAssertTrue(visible.isEmpty)
        let items = try await reopened.loadAll()
        XCTAssertEqual(items.first { $0.id == request.requestID }?.lastResponse?.externalIDs, response.externalIDs)
        response.status = "duplicate"
        response.duplicate = true
        response.writeCommitted = true
        response.plan = .init(wouldWrite: true)
        XCTAssertFalse(OutboxDeliveryService.isSafe(response: response, for: request))
    }

    func testMultipleTokenRefreshesUseLatestPreviewAndSameTokenRetainsOriginalID() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        var confirmation = payload(id: "confirm-0", operation: op, confirm: true)
        for index in 0..<3 {
            confirmation.requestID = "confirm-\(index)"
            confirmation.context.confirmationToken = index == 0 ? "v2.synthetic" : "v2.token-\(index)"
            let item = try await fixture.store.enqueue(confirmation, now: now)
            XCTAssertEqual(item.id, confirmation.requestID)
            var secondTap = confirmation
            secondTap.requestID = "second-tap-\(index)"
            let sameToken = try await fixture.store.enqueue(secondTap, now: now)
            XCTAssertEqual(sameToken.id, confirmation.requestID)
            let refreshed = CaptureResponseV1(status: "needs_confirmation", dryRun: false, requestID: confirmation.requestID,
                domain: confirmation.route.domain.rawValue, operationID: op, confirmationToken: "v2.token-\(index + 1)",
                confirmation: .init(amountCOP: 45000, description: "Almuerzo", sourceName: "Nequi", categoryName: "Comida", budgetName: "Comidas"),
                writeCommitted: false, plan: .init(wouldWrite: false))
            try await fixture.store.markSent(requestID: confirmation.requestID, now: now, response: refreshed)
            let visible = try await fixture.store.loadPendingExpenseConfirmations()
            XCTAssertEqual(visible.map(\.id), [confirmation.requestID])
        }
        var invalidToken = confirmation
        invalidToken.requestID = "never-issued"
        invalidToken.context.confirmationToken = "v2.never-issued"
        do {
            _ = try await fixture.store.enqueue(invalidToken, now: now)
            XCTFail("Only the active server token may create another confirmation")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .notDeliverable) }
        var stale = confirmation
        stale.requestID = "stale"
        stale.context.confirmationToken = "v2.synthetic"
        let canonicalStale = try await fixture.store.enqueue(stale, now: now)
        XCTAssertEqual(canonicalStale.id, "confirm-0")
        do {
            _ = try await fixture.store.claimDelivery(payload: canonicalStale.payload, now: now)
            XCTFail("Invalidated stale consent cannot be sent")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .notDeliverable) }
    }

    func testLatePreviewCannotResurrectRegisterAfterTerminalRejection() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        try await seedPreview(fixture.store, operation: op, id: "preview")
        let confirmation = payload(id: "confirm", operation: op, confirm: true)
        _ = try await fixture.store.enqueue(confirmation, now: now)
        let rejection = CaptureResponseV1(status: "rejected", dryRun: false, requestID: confirmation.requestID,
            domain: confirmation.route.domain.rawValue, operationID: op, writeCommitted: false, plan: .init(wouldWrite: false))
        try await fixture.store.markSent(requestID: confirmation.requestID, now: now, response: rejection)
        // A response from another in-flight preview arrives after the rejection.
        try await seedPreview(fixture.store, operation: op, id: "late-preview")
        let reopened = FileOutboxStore(fileURL: fixture.url)
        let visible = try await reopened.loadPendingExpenseConfirmations()
        XCTAssertTrue(visible.isEmpty)
        let items = try await reopened.loadAll()
        XCTAssertEqual(items.first { $0.id == "late-preview" }?.confirmationInvalidationReason, "operation_terminal")
        var newConsent = confirmation
        newConsent.requestID = "new-consent"
        do {
            _ = try await reopened.enqueue(newConsent, now: now)
            XCTFail("A terminal operation cannot regain confirmation authority")
        } catch let error as OutboxStoreError { XCTAssertEqual(error, .notDeliverable) }
    }

    func testSuppressedInflightPreviewReturnsInactiveResultWhenCancelOrRejectionWins() async throws {
        for cancellationWins in [true, false] {
            let fixture = fixture()
            defer { try? FileManager.default.removeItem(at: fixture.directory) }
            try await seedPreview(fixture.store, operation: op, id: "preview")
            var request = payload(id: "late-preview", operation: op)
            request.context.operationID = nil
            _ = try await fixture.store.enqueue(request, now: now)
            let preview = CaptureResponseV1(status: "needs_confirmation", dryRun: false, requestID: request.requestID,
                domain: request.route.domain.rawValue, operationID: op, confirmationToken: "v2.late",
                confirmation: .init(amountCOP: 45000, description: "Almuerzo", sourceName: "Nequi", categoryName: "Comida", budgetName: "Comidas"),
                writeCommitted: false, plan: .init(wouldWrite: false))
            let session = SuspendedSession(response: preview)
            let delivery = OutboxDeliveryService(store: fixture.store, client: client(session))
            let sending = Task { try await delivery.deliver(payload: request, secret: "synthetic") }
            await session.waitUntilStarted()
            let otherStore = FileOutboxStore(fileURL: fixture.url)
            if cancellationWins {
                _ = try await otherStore.enqueue(payload(id: "cancel", operation: op, cancel: true), now: now)
            } else {
                let confirmation = payload(id: "confirm", operation: op, confirm: true)
                _ = try await otherStore.enqueue(confirmation, now: now)
                let rejection = CaptureResponseV1(status: "rejected", dryRun: false, requestID: confirmation.requestID,
                    domain: confirmation.route.domain.rawValue, operationID: op, writeCommitted: false, plan: .init(wouldWrite: false))
                try await otherStore.markSent(requestID: confirmation.requestID, now: now, response: rejection)
            }
            await session.release()
            let result = try await sending.value
            XCTAssertEqual(result.status, "rejected")
            XCTAssertEqual(result.requestID, request.requestID)
            XCTAssertEqual(result.operationID, op)
            XCTAssertEqual(result.writeCommitted, false)
            XCTAssertNil(result.confirmationToken)
            XCTAssertNil(result.confirmation)
            let visible = try await otherStore.loadPendingExpenseConfirmations()
            XCTAssertTrue(visible.isEmpty)
            let items = try await otherStore.loadAll()
            let stored = try XCTUnwrap(items.first { $0.id == request.requestID })
            XCTAssertEqual(stored.status, .sent)
            XCTAssertEqual(stored.lastResponse, preview)
            XCTAssertNotNil(stored.confirmationInvalidatedAt)
            // A late failure callback may not downgrade the terminal audit.
            try await otherStore.markFailed(requestID: request.requestID, message: "network_-1005", now: now)
            let afterFailure = try await otherStore.loadAll()
            XCTAssertEqual(afterFailure.first { $0.id == request.requestID }?.status, .sent)
        }
    }

    func testExpenseOperationIDMustBeASCIIHex() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let request = payload(id: "unicode", operation: "exp_" + String(repeating: "١", count: 32), confirm: true)
        XCTAssertFalse(OutboxDeliveryService.isSafeRequest(request))
    }

    func testReminderDueAndUnverifiedDeliveryMetadataRoundTripsInOutbox() async throws {
        let fixture = fixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let response = CaptureResponseV1(status: "committed", interpretedDue: .init(start: "2026-10-25T08:00:00-05:00", timezone: "America/Bogota", display: "25 oct 2026, 8:00 a. m. (Bogotá)"), calendarSynced: true, calendarSyncPending: false, externalIDs: ["notion_page_id": "synthetic-page"], notificationVerified: false, deliveryStatus: "pending_verification")
        let request = payload(id: "reminder", operation: nil)
        _ = try await fixture.store.enqueue(request, now: now)
        try await fixture.store.markSent(requestID: request.requestID, now: now, response: response)
        let reopened = FileOutboxStore(fileURL: fixture.url)
        let items = try await reopened.loadAll()
        XCTAssertEqual(items.first?.lastResponse, response)
        XCTAssertEqual(items.first?.lastResponse?.notificationVerified, false)
    }

    private func fixture() -> (directory: URL, url: URL, store: FileOutboxStore) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let url = directory.appendingPathComponent("outbox.json")
        return (directory, url, FileOutboxStore(fileURL: url))
    }

    private func payload(id: String, operation: String?, confirm: Bool = false, cancel: Bool = false) -> CapturePayloadV1 {
        CapturePayloadV1(requestID: id, createdAt: now,
            source: .init(appVersion: "test", platform: "watchOS", osVersion: "27", deviceID: "synthetic", surface: "watch_app"),
            route: operation == nil ? .general : .expense,
            capture: .init(modality: "watch_dictation", text: "45 mil almuerzo Nequi"),
            context: .init(userConfirmation: confirm, userCancellation: cancel,
                dryRun: operation == nil, allowWrite: operation != nil, allowFireflyWrite: operation != nil && !cancel,
                confirmationToken: confirm ? "v2.synthetic" : nil, operationID: operation))
    }

    private func seedPreview(_ store: FileOutboxStore, operation: String, id: String) async throws {
        var request = payload(id: id, operation: operation)
        request.context.operationID = nil
        _ = try await store.enqueue(request, now: now)
        let response = CaptureResponseV1(status: "needs_confirmation", dryRun: false, requestID: id,
            domain: request.route.domain.rawValue, operationID: operation, confirmationToken: "v2.synthetic",
            confirmation: .init(amountCOP: 45000, description: "Almuerzo", sourceName: "Nequi", categoryName: "Comida", budgetName: "Comidas"),
            writeCommitted: false, plan: .init(wouldWrite: false))
        try await store.markSent(requestID: id, now: now, response: response)
    }

    private func client(_ session: URLSessioning) -> WebhookClient {
        WebhookClient(endpoint: URL(string: "https://example.invalid/webhooks/mobile-capture-v1")!, session: session)
    }
}

private final class ImmediateSession: URLSessioning {
    let response: CaptureResponseV1
    let status: Int
    init(response: CaptureResponseV1, status: Int = 200) { self.response = response; self.status = status }
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        (try JSONEncoder.captureEncoder().encode(response), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }
}

private final class FailingSession: URLSessioning {
    func data(for request: URLRequest) async throws -> (Data, URLResponse) { throw URLError(.notConnectedToInternet) }
}

private actor SuspendedSession: URLSessioning {
    let response: CaptureResponseV1
    var calls = 0
    var startedWaiter: CheckedContinuation<Void, Never>?
    var releaseWaiter: CheckedContinuation<Void, Never>?
    init(response: CaptureResponseV1) { self.response = response }
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        calls += 1
        startedWaiter?.resume()
        startedWaiter = nil
        await withCheckedContinuation { releaseWaiter = $0 }
        return (try JSONEncoder.captureEncoder().encode(response), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
    func waitUntilStarted() async {
        if calls > 0 { return }
        await withCheckedContinuation { startedWaiter = $0 }
    }
    func release() { releaseWaiter?.resume(); releaseWaiter = nil }
}
