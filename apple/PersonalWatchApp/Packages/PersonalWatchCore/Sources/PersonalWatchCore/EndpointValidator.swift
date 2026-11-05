import Foundation

public enum EndpointValidationError: Error, Equatable, LocalizedError, Sendable {
    case empty
    case invalidURL
    case httpsRequired
    case hostRequired
    case dnsNameRequired

    public var errorDescription: String? {
        switch self {
        case .empty:
            return "Ingresa el endpoint del BFF"
        case .invalidURL:
            return "El endpoint no es una URL válida"
        case .httpsRequired:
            return "El endpoint debe usar HTTPS"
        case .hostRequired:
            return "El endpoint debe incluir un host"
        case .dnsNameRequired:
            return "Usa el nombre DNS de Tailscale, no la IP; el certificado TLS está emitido para el hostname"
        }
    }
}

public enum EndpointValidator {
    public static func normalizedBaseURL(from rawValue: String) throws -> URL {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw EndpointValidationError.empty
        }
        guard var components = URLComponents(string: trimmed) else {
            throw EndpointValidationError.invalidURL
        }
        guard components.scheme?.lowercased() == "https" else {
            throw EndpointValidationError.httpsRequired
        }
        guard let host = components.host, !host.isEmpty else {
            throw EndpointValidationError.hostRequired
        }
        guard !isIPAddress(host) else {
            throw EndpointValidationError.dnsNameRequired
        }

        components.scheme = "https"
        components.path = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if !components.path.isEmpty {
            components.path = "/\(components.path)"
        }
        components.query = nil
        components.fragment = nil

        guard let url = components.url else {
            throw EndpointValidationError.invalidURL
        }
        return url
    }

    public static func healthURL(from baseURL: URL) -> URL {
        baseURL.appendingPathComponent("health")
    }

    public static func captureURL(from baseURL: URL) -> URL {
        baseURL
            .appendingPathComponent("webhooks")
            .appendingPathComponent("mobile-capture-v1")
    }

    private static func isIPAddress(_ host: String) -> Bool {
        if host.contains(":") {
            return true
        }
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else {
            return false
        }
        return parts.allSatisfy { part in
            guard let value = Int(part) else { return false }
            return (0...255).contains(value)
        }
    }
}
