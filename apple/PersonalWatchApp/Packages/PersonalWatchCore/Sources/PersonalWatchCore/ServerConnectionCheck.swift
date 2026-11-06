import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Fixed local wording only. A transport failure never proves that all prior
/// attempts stopped before the server received the capture.
enum CaptureTransportDescription {
    static func message(code: Int) -> String {
        switch code {
        case -1003, -1006:
            return "No se encuentra el servidor · captura conservada"
        case -1004:
            return "Servidor inaccesible · captura conservada"
        case -1009:
            return "Sin internet disponible · captura conservada"
        case -1001, -1005:
            return "Respuesta no confirmada · puede haberse guardado"
        case -1206 ... -1200:
            return "Conexión segura rechazada · revisa el servidor y su certificado"
        case -1022:
            return "Conexión HTTPS requerida · revisa el endpoint"
        case -1000, -1002:
            return "Dirección del servidor inválida · revisa la configuración"
        case -1012, -1013:
            return "Autorización no completada · captura conservada"
        case -999:
            return "Solicitud interrumpida · resultado sin verificar"
        default:
            return "No se pudo completar la conexión · consulta Historial"
        }
    }
}

public enum ServerConnectionCheckResult: Equatable, Sendable {
    case ready(reminderEnabled: Bool)
    case http(Int)
    case network(Int)
    case invalidConfiguration
    case invalidResponse

    public var message: String {
        switch self {
        case .ready(let enabled):
            return enabled ? "Servidor accesible · Recordatorio habilitado"
                : "Servidor accesible · Recordatorio no habilitado"
        case .http(let status):
            return (300..<400).contains(status)
                ? "El servidor redirige la conexión · revisa el endpoint"
                : "El servidor respondió HTTP \(status)"
        case .network(let code):
            switch code {
            case -1003, -1006: return "Este dispositivo no resuelve el nombre del servidor"
            case -1004: return "Este dispositivo no logra conectar con el servidor"
            case -1009: return "El sistema no encuentra conexión a internet"
            case -1001: return "El servidor no respondió a tiempo"
            case -1005: return "La conexión se interrumpió durante la comprobación"
            case -1206 ... -1200: return "TLS o certificado rechazado · se mantiene la conexión segura"
            case -999: return "Comprobación interrumpida"
            case -1000, -1002, -1022: return "Revisa la dirección HTTPS del servidor"
            default: return "No se pudo comprobar la conexión de este dispositivo"
            }
        case .invalidConfiguration:
            return "Revisa la configuración HTTPS desde el iPhone"
        case .invalidResponse:
            return "La respuesta no corresponde al servidor esperado"
        }
    }

    public var diagnosticCode: String? {
        switch self {
        case .network(let code): return "network_\(code)"
        case .http(let code): return "http_\(code)"
        case .invalidConfiguration: return "configuration_invalid"
        case .invalidResponse: return "health_response_invalid"
        case .ready: return nil
        }
    }
}

/// An explicit, read-only check from the Watch's own URLSession route. It sends
/// no capture, request identity, signature, secret or stored authorization.
public enum ServerConnectionCheck {
    public static func check(baseURL: URL, session injected: URLSessioning? = nil) async -> ServerConnectionCheckResult {
        guard let checked = try? EndpointValidator.normalizedBaseURL(from: baseURL.absoluteString),
              checked.user == nil, checked.password == nil else { return .invalidConfiguration }
        let url = EndpointValidator.healthURL(from: checked)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 8)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let owned: URLSession?
        let session: URLSessioning
        if let injected {
            owned = nil
            session = injected
        } else {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForRequest = 8
            configuration.timeoutIntervalForResource = 10
            configuration.urlCache = nil
            configuration.urlCredentialStorage = nil
            configuration.httpCookieStorage = nil
            configuration.httpShouldSetCookies = false
            configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
            let value = URLSession(configuration: configuration, delegate: NoHealthRedirect(), delegateQueue: nil)
            owned = value
            session = value
        }
        defer { owned?.invalidateAndCancel() }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse,
                  let responseURL = http.url,
                  responseURL.scheme == url.scheme, responseURL.host == url.host,
                  responseURL.port == url.port, responseURL.path == url.path,
                  responseURL.user == nil, responseURL.password == nil,
                  responseURL.query == nil, responseURL.fragment == nil else { return .invalidResponse }
            guard http.statusCode == 200 else { return .http(http.statusCode) }
            guard data.count <= 4096,
                  let health = try? JSONDecoder().decode(Health.self, from: data),
                  health.status == "ok", health.service == "mobile-capture-v1-staging",
                  ["dry-run", "allowlisted-write"].contains(health.mode) else { return .invalidResponse }
            return .ready(reminderEnabled: health.mode == "allowlisted-write"
                          && health.writeDomains.contains(CaptureRoute.reminder.domain.rawValue))
        } catch let error as URLError {
            return .network(error.errorCode)
        } catch {
            return .invalidResponse
        }
    }

    private struct Health: Decodable {
        let status: String
        let service: String
        let mode: String
        let writeDomains: [String]
        enum CodingKeys: String, CodingKey {
            case status, service, mode
            case writeDomains = "write_domains"
        }
    }
}

private final class NoHealthRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
