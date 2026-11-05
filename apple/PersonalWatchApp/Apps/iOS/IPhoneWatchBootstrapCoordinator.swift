import Combine
import Foundation
import PersonalWatchCore
import WatchConnectivity

final class IPhoneWatchBootstrapCoordinator: NSObject, ObservableObject {
    @Published private(set) var isReachable = false
    @Published private(set) var activationMessage = "Activando WatchConnectivity"

    private var session: WCSession?

    override init() {
        super.init()
        guard !PersonalWatchAppRuntime.isSynthetic else { return }
        guard WCSession.isSupported() else {
            activationMessage = "WatchConnectivity no disponible"
            return
        }
        let session = WCSession.default
        self.session = session
        session.delegate = self
        session.activate()
    }

    func sendConfiguration(
        baseURL: URL,
        routeSecret: String,
        completion: @escaping (Result<Void, WatchBootstrapSendError>) -> Void
    ) {
        guard let session else {
            completion(.failure(.unsupported))
            return
        }
        guard session.isReachable else {
            refreshReachability(session)
            completion(.failure(.watchNotReachable))
            return
        }

        let message = WatchBootstrapMessage.make(
            baseURL: baseURL,
            routeSecret: routeSecret
        )
        session.sendMessage(message) { reply in
            DispatchQueue.main.async {
                if reply[WatchBootstrapMessage.replyOKKey] as? Bool == true {
                    completion(.success(()))
                } else {
                    completion(.failure(.watchRejected))
                }
            }
        } errorHandler: { _ in
            DispatchQueue.main.async {
                completion(.failure(.deliveryFailed))
            }
        }
    }

    func requestDiagnostics(
        completion: @escaping (Result<WatchOutboxDiagnostics, WatchBootstrapSendError>) -> Void
    ) {
        guard let session else {
            completion(.failure(.unsupported))
            return
        }
        guard session.isReachable else {
            refreshReachability(session)
            completion(.failure(.watchNotReachable))
            return
        }

        session.sendMessage(WatchDiagnosticsMessage.request) { reply in
            DispatchQueue.main.async {
                guard let diagnostics = WatchOutboxDiagnostics(reply: reply) else {
                    completion(.failure(.watchRejected))
                    return
                }
                completion(.success(diagnostics))
            }
        } errorHandler: { _ in
            DispatchQueue.main.async {
                completion(.failure(.deliveryFailed))
            }
        }
    }

    private func handleCaptureFallback(
        message: [String: Any],
        replyHandler: @escaping ([String: Any]) -> Void
    ) {
        guard
            let payloadData = message[WatchCaptureFallbackMessage.payloadDataKey] as? Data,
            payloadData.count <= 64 * 1024,
            let payload = try? JSONDecoder.captureDecoder().decode(
                CapturePayloadV1.self,
                from: payloadData
            ),
            payload.eventType == CapturePayloadV1.eventType,
            payload.schema == CapturePayloadV1.schema,
            payload.schemaVersion == CapturePayloadV1.schemaVersion,
            !payload.requestID.isEmpty,
            payload.source.platform == "watchOS",
            ["watch_app", "app_intent_watch"].contains(payload.source.surface),
            !payload.capture.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            payload.capture.text.utf8.count <= 16 * 1024,
            payload.context.dryRun,
            !payload.context.allowWrite,
            payload.context.allowFireflyWrite != true,
            OutboxDeliveryService.isSafeRequest(payload)
        else {
            replyHandler([
                WatchBootstrapMessage.replyOKKey: false,
                WatchBootstrapMessage.replyErrorKey: "invalid_capture_payload"
            ])
            return
        }

        guard
            let rawBaseURL = UserDefaults.standard.string(forKey: "hermes.baseURL"),
            let baseURL = try? EndpointValidator.normalizedBaseURL(from: rawBaseURL),
            let secret = try? KeychainRouteSecretStore().loadRouteSecretSynchronously(),
            !secret.isEmpty
        else {
            replyHandler([
                WatchBootstrapMessage.replyOKKey: false,
                WatchBootstrapMessage.replyErrorKey: "iphone_not_configured"
            ])
            return
        }

        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 20
        let client = WebhookClient(
            endpoint: EndpointValidator.captureURL(from: baseURL),
            session: URLSession(configuration: configuration)
        )
        Task {
            do {
                let response = try await client.submit(payload: payload, secret: secret)
                guard OutboxDeliveryService.isSafe(response: response, for: payload) else {
                    replyHandler([
                        WatchBootstrapMessage.replyOKKey: false,
                        WatchBootstrapMessage.replyErrorKey: "unsafe_response"
                    ])
                    return
                }
                let responseData = try JSONEncoder.captureEncoder().encode(response)
                replyHandler([
                    WatchBootstrapMessage.replyOKKey: true,
                    WatchCaptureFallbackMessage.responseDataKey: responseData
                ])
            } catch let error as WebhookClientError {
                let errorCode: String
                switch error {
                case .unacceptableStatus(let statusCode, _):
                    errorCode = "http_\(statusCode)"
                case .invalidHTTPResponse:
                    errorCode = "invalid_http_response"
                }
                replyHandler([
                    WatchBootstrapMessage.replyOKKey: false,
                    WatchBootstrapMessage.replyErrorKey: errorCode
                ])
            } catch {
                replyHandler([
                    WatchBootstrapMessage.replyOKKey: false,
                    WatchBootstrapMessage.replyErrorKey: "iphone_delivery_failed"
                ])
            }
        }
    }

    private func refreshReachability(_ session: WCSession) {
        DispatchQueue.main.async { [weak self] in
            self?.isReachable = session.isReachable
            self?.activationMessage = session.isReachable
                ? "Apple Watch conectado"
                : "Abre Personal Watch App en el Apple Watch"
        }
    }
}

extension IPhoneWatchBootstrapCoordinator: WCSessionDelegate {
    func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {
        DispatchQueue.main.async { [weak self] in
            if let error {
                self?.isReachable = false
                self?.activationMessage = "WatchConnectivity: \(error.localizedDescription)"
            } else {
                self?.isReachable = session.isReachable
                self?.activationMessage = session.isReachable
                    ? "Apple Watch conectado"
                    : "Abre Personal Watch App en el Apple Watch"
            }
        }
    }

    func sessionReachabilityDidChange(_ session: WCSession) {
        refreshReachability(session)
    }

    func session(
        _ session: WCSession,
        didReceiveMessage message: [String: Any],
        replyHandler: @escaping ([String: Any]) -> Void
    ) {
        guard
            message[WatchBootstrapMessage.commandKey] as? String == WatchCaptureFallbackMessage.command
        else {
            replyHandler([
                WatchBootstrapMessage.replyOKKey: false,
                WatchBootstrapMessage.replyErrorKey: "unsupported_command"
            ])
            return
        }
        handleCaptureFallback(message: message, replyHandler: replyHandler)
    }

    func sessionDidBecomeInactive(_ session: WCSession) {
        refreshReachability(session)
    }

    func sessionDidDeactivate(_ session: WCSession) {
        session.activate()
    }
}

enum WatchBootstrapSendError: LocalizedError {
    case unsupported
    case watchNotReachable
    case watchRejected
    case deliveryFailed

    var errorDescription: String? {
        switch self {
        case .unsupported:
            return "WatchConnectivity no está disponible"
        case .watchNotReachable:
            return "Abre Personal Watch App en el Apple Watch y vuelve a intentar"
        case .watchRejected:
            return "El Apple Watch rechazó la solicitud"
        case .deliveryFailed:
            return "No se pudo entregar la solicitud al Apple Watch"
        }
    }
}
