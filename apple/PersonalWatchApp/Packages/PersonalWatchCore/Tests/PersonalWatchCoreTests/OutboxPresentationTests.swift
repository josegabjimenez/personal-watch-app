import Foundation
import XCTest
@testable import PersonalWatchCore

final class OutboxPresentationTests: XCTestCase {
    private let operationID = "exp_0123456789abcdef0123456789abcdef"
    private let capturedAt = "2026-11-01T00:00:00Z"
    private let receivedWarning = "Cancelación recibida · verifica si hubo un registro previo en Firefly"
    private let requestedWarning = "Cancelación solicitada · el envío anterior puede haberse registrado; verifica en Firefly"
    private let rawCancellationCopy = "Gasto cancelado · no se registró nada"

    func testPersistedSafeReceiptKeepsWarningAndOriginalEncodedEvidence() throws {
        let item = cancellation()
        let original = try JSONEncoder.captureEncoder().encode(item)
        let reopened = try JSONDecoder.captureDecoder().decode(OutboxItem.self, from: original)

        XCTAssertTrue(OutboxDeliveryService.isSafe(response: try XCTUnwrap(item.lastResponse), for: item.payload))
        XCTAssertEqual(OutboxPresentation.message(for: item), receivedWarning)
        XCTAssertEqual(OutboxPresentation.message(for: reopened), receivedWarning)
        XCTAssertEqual(try JSONEncoder.captureEncoder().encode(item), original)
        XCTAssertEqual(try JSONEncoder.captureEncoder().encode(reopened), original)
        XCTAssertEqual(reopened.lastResponse?.displayMessage, rawCancellationCopy)
        XCTAssertEqual(reopened.cancellationMayHaveBeenSent, true)
        XCTAssertEqual(reopened.payload.createdAt, capturedAt)
        XCTAssertEqual(reopened.attempts, 2)
    }

    func testSafeDuplicateReceiptStillKeepsEarlierRegistrationWarning() throws {
        var item = cancellation()
        item.lastResponse?.status = "duplicate"
        item.lastResponse?.duplicate = true

        XCTAssertTrue(OutboxDeliveryService.isSafe(response: try XCTUnwrap(item.lastResponse), for: item.payload))
        XCTAssertEqual(OutboxPresentation.message(for: item), receivedWarning)
        XCTAssertEqual(item.lastResponse?.displayMessage, rawCancellationCopy)
    }

    func testPendingFailedAndSendingCancellationWithoutReceiptKeepRequestedWarning() {
        for status in [OutboxStatus.pending, .failed, .sending] {
            var item = cancellation()
            item.status = status
            item.lastResponse = nil
            item.lastError = status == .failed ? "network_-1009" : nil

            XCTAssertEqual(OutboxPresentation.message(for: item), requestedWarning, status.rawValue)
            XCTAssertNil(item.lastResponse)
        }
    }

    func testMismatchedOrRejectedResponseCannotAcknowledgeCancellation() throws {
        let item = cancellation()
        let valid = try XCTUnwrap(item.lastResponse)
        var wrongRequest = valid
        wrongRequest.requestID = "another-cancellation"
        var wrongOperation = valid
        wrongOperation.operationID = "exp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        var wrongDomain = valid
        wrongDomain.domain = CaptureDomain.auraReminderCapture.rawValue
        var rejected = valid
        rejected.status = "rejected"
        var contradictoryWrite = valid
        contradictoryWrite.writeCommitted = true
        var missingNoWriteProof = valid
        missingNoWriteProof.writeCommitted = nil

        for response in [wrongRequest, wrongOperation, wrongDomain, rejected,
                         contradictoryWrite, missingNoWriteProof] {
            XCTAssertFalse(OutboxDeliveryService.isSafe(response: response, for: item.payload))
            XCTAssertEqual(OutboxPresentation.message(for: item, response: response), requestedWarning)
        }
        XCTAssertEqual(item.lastResponse, valid, "Projection must not replace the durable receipt")
    }

    func testJustReceivedResponseOverridesCacheWithoutChangingStoredReceipt() throws {
        var item = cancellation()
        let accepted = try XCTUnwrap(item.lastResponse)
        item.status = .pending
        item.lastResponse = nil
        let before = try JSONEncoder.captureEncoder().encode(item)

        XCTAssertEqual(OutboxPresentation.message(for: item, response: accepted), receivedWarning)
        XCTAssertEqual(try JSONEncoder.captureEncoder().encode(item), before)
        XCTAssertEqual(OutboxPresentation.message(for: item), requestedWarning)
    }

    func testFalseOrMissingUncertaintyFlagPreservesOrdinaryCancellationCopy() {
        for flag in [Optional(false), nil] {
            var item = cancellation()
            item.cancellationMayHaveBeenSent = flag

            XCTAssertEqual(OutboxPresentation.message(for: item), rawCancellationCopy)
        }
    }

    func testOtherRoutesAndNonCancellationExpenseKeepTheirOwnMessage() {
        let kinds: [QuickActionKind] = [.grocery, .reminder, .expense]
        for kind in kinds {
            let payload = factory(id: "ordinary-\(kind.rawValue)").makePayload(
                kind: kind, text: "Una captura sintética", requestWrite: true
            )
            var item = OutboxItem(payload: payload, status: .sent, createdAt: capturedAt, updatedAt: capturedAt)
            item.cancellationMayHaveBeenSent = true // Legacy metadata must not widen its meaning.
            item.lastResponse = CaptureResponseV1(
                status: "needs_input", dryRun: false, requestID: payload.requestID,
                domain: payload.route.domain.rawValue, displayMessage: "Revisa esta captura",
                writeCommitted: false, plan: .init(sideEffects: [], wouldWrite: false)
            )

            XCTAssertEqual(OutboxPresentation.message(for: item), "Revisa esta captura", kind.rawValue)
        }
    }

    func testOrdinaryResponseKeepsQuestionFallbackAndMissingMessage() {
        var item = cancellation()
        item.cancellationMayHaveBeenSent = false
        item.lastResponse?.displayMessage = nil
        item.lastResponse?.question = "¿Qué presupuesto?"
        XCTAssertEqual(OutboxPresentation.message(for: item), "¿Qué presupuesto?")

        var latest = item.lastResponse!
        latest.question = "¿Qué cuenta?"
        XCTAssertEqual(OutboxPresentation.message(for: item, response: latest), "¿Qué cuenta?")
        XCTAssertEqual(item.lastResponse?.question, "¿Qué presupuesto?")

        item.lastResponse = nil
        XCTAssertNil(OutboxPresentation.message(for: item))
    }

    private func cancellation() -> OutboxItem {
        let payload = factory(id: "synthetic-cancellation").makePayload(
            kind: .expense, text: "45 mil Uber Efectivo", requestWrite: true,
            userCancellation: true, operationID: operationID
        )
        var item = OutboxItem(payload: payload, status: .sent, attempts: 2,
                              createdAt: capturedAt, updatedAt: capturedAt)
        item.cancellationMayHaveBeenSent = true
        item.lastResponse = CaptureResponseV1(
            status: "cancelled", dryRun: false, requestID: payload.requestID,
            domain: payload.route.domain.rawValue, operationID: operationID,
            displayMessage: rawCancellationCopy, writeCommitted: false,
            plan: .init(sideEffects: [], wouldWrite: false)
        )
        return item
    }

    private func factory(id: String) -> CaptureFactory {
        let timestamp = capturedAt
        return CaptureFactory(
            appVersion: "synthetic-test", platform: "simulator", osVersion: "synthetic",
            deviceID: "synthetic-device", surface: "synthetic-test",
            nowISO8601: { timestamp }, makeRequestID: { id }
        )
    }
}
