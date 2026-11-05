@preconcurrency import Foundation

public protocol RouteSecretStore: Sendable {
    func loadRouteSecret() async throws -> String?
    func saveRouteSecret(_ secret: String) async throws
}

public actor InMemoryRouteSecretStore: RouteSecretStore {
    private var secret: String?

    public init(secret: String? = nil) {
        self.secret = secret
    }

    public func loadRouteSecret() async throws -> String? {
        secret
    }

    public func saveRouteSecret(_ secret: String) async throws {
        self.secret = secret
    }
}

public struct MobileCaptureConfiguration: Codable, Equatable, Sendable {
    public var baseURL: URL
    public var endpointPath: String

    public init(baseURL: URL, endpointPath: String = "/webhooks/mobile-capture-v1") {
        self.baseURL = baseURL
        self.endpointPath = endpointPath
    }

    public var endpointURL: URL {
        endpointPath
            .split(separator: "/")
            .reduce(baseURL) { url, component in
                url.appendingPathComponent(String(component))
            }
    }
}
