import Foundation
import PersonalWatchCore
import WatchConnectivity

enum WatchPhoneFallbackError: Error, LocalizedError {
    case unavailable
    case rejected(String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .unavailable:
            return "iPhone no disponible"
        case .rejected:
            return "El iPhone no pudo enviar"
        case .invalidResponse:
            return "Respuesta inválida del iPhone"
        }
    }
}

enum WatchPhoneCaptureFallback {
    static func submit(payload: CapturePayloadV1) async throws -> CaptureResponseV1 {
        guard !PersonalWatchAppRuntime.isSynthetic else { throw WatchPhoneFallbackError.unavailable }
        #if DEBUG
        let environment = ProcessInfo.processInfo.environment
        // Retain the original local override as a compatibility alias.
        if environment["PERSONAL_WATCH_DISABLE_PHONE_FALLBACK"] == "1" || environment["HERMES_DISABLE_PHONE_FALLBACK"] == "1" {
            throw WatchPhoneFallbackError.unavailable
        }
        #endif

        guard WCSession.isSupported(), WCSession.default.isReachable else {
            throw WatchPhoneFallbackError.unavailable
        }
        let payloadData = try JSONEncoder.captureEncoder().encode(payload)
        let message = WatchCaptureFallbackMessage.make(payloadData: payloadData)

        return try await withCheckedThrowingContinuation { continuation in
            WCSession.default.sendMessage(message) { reply in
                guard reply[WatchBootstrapMessage.replyOKKey] as? Bool == true else {
                    let code = reply[WatchBootstrapMessage.replyErrorKey] as? String ?? "rejected"
                    continuation.resume(throwing: WatchPhoneFallbackError.rejected(code))
                    return
                }
                guard
                    let responseData = reply[WatchCaptureFallbackMessage.responseDataKey] as? Data,
                    let response = try? JSONDecoder.captureDecoder().decode(
                        CaptureResponseV1.self,
                        from: responseData
                    ),
                    OutboxDeliveryService.isSafe(response: response, for: payload)
                else {
                    continuation.resume(throwing: WatchPhoneFallbackError.invalidResponse)
                    return
                }
                continuation.resume(returning: response)
            } errorHandler: { _ in
                continuation.resume(throwing: WatchPhoneFallbackError.unavailable)
            }
        }
    }
}

enum WatchCaptureDeliveryCoordinator {
    static func deliver(
        payload: CapturePayloadV1,
        secret: String,
        baseURL: URL,
        outbox: FileOutboxStore,
        retryMode: CaptureRetryMode = .standard
    ) async throws -> CaptureResponseV1 {
        let direct = OutboxDeliveryService(
            store: outbox,
            client: PersonalWatchAppRuntime.makeClient(baseURL: baseURL)
        )
        do {
            return try await direct.deliver(payload: payload, secret: secret, retryMode: retryMode)
        } catch let failure as OutboxDeliveryFailure {
            guard
                payload.context.dryRun,
                OutboxDeliveryService.isSafeRequest(payload),
                failure.isTransientTransportFailure
            else {
                throw failure
            }
            do {
                let response = try await WatchPhoneCaptureFallback.submit(payload: payload)
                try await outbox.markSent(
                    requestID: payload.requestID,
                    now: ISO8601DateFormatter().string(from: Date()),
                    deliveryPath: .iPhoneFallback,
                    response: response
                )
                return response
            } catch {
                throw failure
            }
        }
    }
}
