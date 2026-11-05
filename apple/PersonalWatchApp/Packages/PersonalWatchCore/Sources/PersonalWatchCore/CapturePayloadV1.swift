import Foundation

public struct CaptureDelivery: Codable, Equatable, Sendable {
    public var expectResponse: Bool
    public var responsePreference: String

    public init(expectResponse: Bool = true, responsePreference: String = "short") {
        self.expectResponse = expectResponse
        self.responsePreference = responsePreference
    }

    enum CodingKeys: String, CodingKey {
        case expectResponse = "expect_response"
        case responsePreference = "response_preference"
    }
}

public struct CaptureClientState: Codable, Equatable, Sendable {
    public var outboxAttempt: Int
    public var clientSentAt: String?

    public init(outboxAttempt: Int = 0, clientSentAt: String? = nil) {
        self.outboxAttempt = outboxAttempt
        self.clientSentAt = clientSentAt
    }

    enum CodingKeys: String, CodingKey {
        case outboxAttempt = "outbox_attempt"
        case clientSentAt = "client_sent_at"
    }
}

public struct CapturePayloadV1: Codable, Equatable, Sendable {
    public static let eventType = "mobile_capture.v1"
    // Legacy protocol identifier, independent of the app's visible brand.
    public static let schema = "com.jose.hermes.mobile_capture"
    public static let schemaVersion = 1

    public var eventType: String
    public var schema: String
    public var schemaVersion: Int
    public var requestID: String
    public var createdAt: String
    public var source: CaptureSource
    public var route: CaptureRoute
    public var capture: CaptureText
    public var entities: CaptureEntities
    public var context: CaptureContext
    public var delivery: CaptureDelivery
    public var clientState: CaptureClientState

    public init(
        requestID: String,
        createdAt: String,
        source: CaptureSource,
        route: CaptureRoute,
        capture: CaptureText,
        entities: CaptureEntities = CaptureEntities(),
        context: CaptureContext = CaptureContext(),
        delivery: CaptureDelivery = CaptureDelivery(),
        clientState: CaptureClientState = CaptureClientState()
    ) {
        self.eventType = Self.eventType
        self.schema = Self.schema
        self.schemaVersion = Self.schemaVersion
        self.requestID = requestID
        self.createdAt = createdAt
        self.source = source
        self.route = route
        self.capture = capture
        self.entities = entities
        self.context = context
        self.delivery = delivery
        self.clientState = clientState
    }

    enum CodingKeys: String, CodingKey {
        case eventType = "event_type"
        case schema
        case schemaVersion = "schema_version"
        case requestID = "request_id"
        case createdAt = "created_at"
        case source
        case route
        case capture
        case entities
        case context
        case delivery
        case clientState = "client_state"
    }
}

public extension JSONEncoder {
    static func captureEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return encoder
    }
}

public extension JSONDecoder {
    static func captureDecoder() -> JSONDecoder {
        JSONDecoder()
    }
}
