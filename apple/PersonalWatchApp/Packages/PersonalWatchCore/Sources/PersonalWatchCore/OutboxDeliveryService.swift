import Foundation

public enum OutboxDeliveryFailure: Error, Equatable, LocalizedError, Sendable {
    case http(Int)
    case network(Int)
    case invalidResponse
    case unsafeResponse
    case unknown
    case reminderPending
    case reminderConfigurationPending

    public var errorDescription: String? {
        switch self {
        case .reminderPending:
            return "Recordatorio pendiente de verificar · reintenta esta captura"
        case .reminderConfigurationPending:
            return "Recordatorio pendiente · revisa la configuración o el límite de IA"
        case .http(401):
            return "Autorización rechazada · revisa la configuración desde el iPhone"
        case .http(let statusCode):
            return "El BFF respondió HTTP \(statusCode)"
        case .network(let code):
            return CaptureTransportDescription.message(code: code)
        case .invalidResponse:
            return "Respuesta inválida · guardado para reintento"
        case .unsafeResponse:
            return "Respuesta no segura · envío detenido"
        case .unknown:
            return "No se pudo enviar · guardado para reintento"
        }
    }

    public var isTransientTransportFailure: Bool {
        switch self {
        case .network, .reminderPending, .reminderConfigurationPending:
            return true
        case .http(let statusCode):
            return statusCode == 502 || statusCode == 503 || statusCode == 504
        case .invalidResponse, .unsafeResponse, .unknown:
            return false
        }
    }

    public var storageCode: String {
        switch self {
        case .reminderPending:
            return "reminder_pending_verification"
        case .reminderConfigurationPending:
            return "reminder_configuration_pending"
        case .http(let statusCode):
            return "http_\(statusCode)"
        case .network(let code):
            return "network_\(code)"
        case .invalidResponse:
            return "invalid_response"
        case .unsafeResponse:
            return "unsafe_response"
        case .unknown:
            return "send_failed"
        }
    }
}

public actor OutboxDeliveryService {
    private let store: FileOutboxStore
    private let client: WebhookClient
    private let nowISO8601: @Sendable () -> String

    public init(
        store: FileOutboxStore,
        client: WebhookClient,
        nowISO8601: @escaping @Sendable () -> String = {
            ISO8601DateFormatter().string(from: Date())
        }
    ) {
        self.store = store
        self.client = client
        self.nowISO8601 = nowISO8601
    }

    public func deliver(
        payload: CapturePayloadV1,
        secret: String,
        retryMode: CaptureRetryMode = .standard
    ) async throws -> CaptureResponseV1 {
        let claim = try await store.claimDelivery(payload: payload, now: nowISO8601(), retryMode: retryMode)
        if case .alreadySent(let response) = claim {
            guard let response, Self.isSafe(response: response, for: payload) else {
                throw OutboxStoreError.notDeliverable
            }
            return response
        }

        do {
            guard Self.isSafeRequest(payload) else {
                throw OutboxDeliveryFailure.unsafeResponse
            }
            #if DEBUG
            let environment = ProcessInfo.processInfo.environment
            // Retain the original Debug override as a compatibility alias.
            if environment["PERSONAL_WATCH_SIMULATE_OFFLINE"] == "1" || environment["HERMES_SIMULATE_OFFLINE"] == "1" {
                throw URLError(.notConnectedToInternet)
            }
            #endif
            let response = try await client.submit(payload: payload, secret: secret)
            guard Self.isSafe(response: response, for: payload) else {
                throw OutboxDeliveryFailure.unsafeResponse
            }
            let stored = try await store.markSent(
                requestID: payload.requestID,
                now: nowISO8601(),
                deliveryPath: .directHTTPS,
                response: response
            )
            return Self.presentationResponse(response, stored: stored)
        } catch {
            // A business rejection may use an HTTP 4xx envelope. Only a fully
            // correlated, proven no-write response may invalidate a preview.
            if case WebhookClientError.unacceptableStatus(let status, let body) = error,
               [400, 409, 410, 422].contains(status),
               let response = try? JSONDecoder.captureDecoder().decode(CaptureResponseV1.self, from: Data(body.utf8)),
               payload.route == .expense, payload.context.userConfirmation,
               ["rejected", "needs_input"].contains(response.status),
               Self.isSafe(response: response, for: payload) {
                try await store.markSent(requestID: payload.requestID, now: nowISO8601(),
                                         deliveryPath: .directHTTPS, response: response)
                return response
            }
            let failure = Self.reminderPendingFailure(error, for: payload) ?? Self.classify(error)
            try? await store.markFailed(
                requestID: payload.requestID,
                message: failure.storageCode,
                now: nowISO8601()
            )
            throw failure
        }
    }

    private static func presentationResponse(_ response: CaptureResponseV1, stored: OutboxItem?) -> CaptureResponseV1 {
        guard response.status == "needs_confirmation", stored?.confirmationInvalidatedAt != nil else { return response }
        // Cancellation/terminal rejection won while the preview was in flight.
        // Never hand its token back to a view after the durable invalidation.
        var inactive = response
        inactive.status = "rejected"
        inactive.confirmationToken = nil
        inactive.confirmation = nil
        inactive.question = nil
        inactive.writeCommitted = false
        inactive.displayMessage = "Vista previa inactiva · consulta el estado del gasto"
        inactive.plan = CaptureResponsePlan(sideEffects: [], wouldWrite: false)
        return inactive
    }

    // An uncertain reminder must remain failed/retryable in the original
    // outbox row. Use only fixed local copy from a strictly correlated error;
    // never present an arbitrary error body's claims or mark a receipt sent.
    static func reminderPendingFailure(_ error: Error, for payload: CapturePayloadV1) -> OutboxDeliveryFailure? {
        guard payload.route == .reminder, isSafeRequest(payload), !payload.context.dryRun,
              case WebhookClientError.unacceptableStatus(let status, let body) = error,
              status == 502, body.utf8.count <= 4096,
              let value = try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: String],
              Set(value.keys) == Set(["status", "error", "request_id", "domain"]),
              value["status"] == "error", value["request_id"] == payload.requestID,
              value["domain"] == CaptureRoute.reminder.domain.rawValue else { return nil }
        switch value["error"] {
        case "reminder_pending_retry": return .reminderPending
        case "reminder_configuration_pending": return .reminderConfigurationPending
        default: return nil
        }
    }

    private static func classify(_ error: Error) -> OutboxDeliveryFailure {
        if let failure = error as? OutboxDeliveryFailure {
            return failure
        }
        if let clientError = error as? WebhookClientError {
            switch clientError {
            case .invalidHTTPResponse:
                return .invalidResponse
            case .unacceptableStatus(let statusCode, _):
                return .http(statusCode)
            }
        }
        if let urlError = error as? URLError {
            return .network(urlError.errorCode)
        }
        if error is DecodingError {
            return .invalidResponse
        }
        return .unknown
    }

    public static func isSafeRequest(_ payload: CapturePayloadV1) -> Bool {
        let isCancellation = payload.context.userCancellation == true
        let hasConfirmationToken = !(payload.context.confirmationToken?.isEmpty ?? true)
        if payload.context.dryRun {
            return !payload.context.allowWrite
                && payload.context.allowFireflyWrite != true
                && !payload.context.userConfirmation
                && !isCancellation
                && payload.context.confirmationToken == nil
                && payload.context.operationID == nil
        }
        guard payload.context.allowWrite else { return false }
        switch payload.route {
        case .expense:
            if isCancellation {
                return !payload.context.userConfirmation
                    && !payload.context.requiresConfirmation
                    && payload.context.allowFireflyWrite == false
                    && payload.context.confirmationToken == nil
                    && isValidExpenseOperationID(payload.context.operationID)
            }
            guard payload.context.allowFireflyWrite == true else { return false }
            if payload.context.userConfirmation {
                return hasConfirmationToken && isValidExpenseOperationID(payload.context.operationID)
            }
            return payload.context.confirmationToken == nil
                && payload.context.operationID == nil
        case .reminder, .grocery:
            return payload.context.allowFireflyWrite != true
                && !payload.context.userConfirmation
                && !isCancellation
                && payload.context.confirmationToken == nil
                && payload.context.operationID == nil
        default:
            return false
        }
    }

    public static func isSafe(
        response: CaptureResponseV1,
        for payload: CapturePayloadV1
    ) -> Bool {
        guard Self.isSafeRequest(payload) else { return false }
        guard
            response.requestID == payload.requestID,
            response.domain == payload.route.domain.rawValue
        else {
            return false
        }
        if payload.context.dryRun {
            return response.dryRun == true
                && response.plan?.wouldWrite == false
                && response.writeCommitted != true
        }
        guard
            payload.context.allowWrite,
            response.dryRun == false
        else {
            return false
        }

        let committedStatuses = ["committed", "duplicate"]
        let committedWrite = committedStatuses.contains(response.status)
            && response.plan?.wouldWrite == true
            && response.writeCommitted == true
        let terminalNoWriteStatuses = ["rejected", "needs_input", "needs_confirmation", "duplicate"]
        let terminalNoWrite = terminalNoWriteStatuses.contains(response.status)
            && response.plan?.wouldWrite == false
            && response.writeCommitted != true

        switch payload.route {
        case .expense:
            if payload.context.userCancellation == true {
                return ["cancelled", "duplicate"].contains(response.status)
                    && response.plan?.wouldWrite == false
                    && response.writeCommitted == false
                    && response.operationID == payload.context.operationID
                    && isValidExpenseOperationID(response.operationID)
                    && response.confirmationToken == nil
                    && response.confirmation == nil
            }
            guard payload.context.allowFireflyWrite == true else { return false }
            if committedWrite {
                return payload.context.userConfirmation
                    && response.operationID == payload.context.operationID
                    && isValidExpenseOperationID(response.operationID)
            }
            if response.status == "needs_confirmation" {
                let correlatedOperation = !payload.context.userConfirmation
                    || response.operationID == payload.context.operationID
                return terminalNoWrite
                    && correlatedOperation
                    && isValidExpenseOperationID(response.operationID)
                    && !(response.confirmationToken?.isEmpty ?? true)
                    && response.confirmation.map { summary in
                        summary.amountCOP > 0 && !summary.description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            && !summary.sourceName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            && !summary.categoryName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            && !summary.budgetName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    } == true
            }
            if payload.context.userConfirmation {
                return terminalNoWrite
                    && response.operationID == payload.context.operationID
                    && isValidExpenseOperationID(response.operationID)
            }
            return terminalNoWrite
                && (response.operationID == nil || isValidExpenseOperationID(response.operationID))
        case .reminder, .grocery:
            guard payload.context.allowFireflyWrite != true else { return false }
            return committedWrite || terminalNoWrite
        default:
            return false
        }
    }

    private static func isValidExpenseOperationID(_ value: String?) -> Bool {
        guard let value, value.hasPrefix("exp_"), value.count == 36 else { return false }
        return value.dropFirst(4).allSatisfy { character in
            ("0"..."9").contains(character) || ("a"..."f").contains(character)
        }
    }
}
