import Foundation
#if canImport(CryptoKit)
import CryptoKit
#else
import Crypto
#endif

public enum HMACSigner {
    public static func signatureV2(rawBody: Data, timestamp: String, secret: String) -> String {
        let key = SymmetricKey(data: Data(secret.utf8))
        var signed = Data(timestamp.utf8)
        signed.append(0x2E) // "."
        signed.append(rawBody)
        let authenticationCode = HMAC<SHA256>.authenticationCode(for: signed, using: key)
        return authenticationCode.map { String(format: "%02x", $0) }.joined()
    }
}

public struct SignedWebhookRequest: Equatable, Sendable {
    public var body: Data
    public var timestamp: String
    public var signature: String
    public var requestID: String
    public var client: String
    public var deviceID: String

    public init(body: Data, timestamp: String, signature: String, requestID: String, client: String, deviceID: String) {
        self.body = body
        self.timestamp = timestamp
        self.signature = signature
        self.requestID = requestID
        self.client = client
        self.deviceID = deviceID
    }
}

public enum WebhookRequestSigner {
    public static func sign(
        payload: CapturePayloadV1,
        secret: String,
        timestamp: String = String(Int(Date().timeIntervalSince1970)),
        client: String? = nil,
        encoder: JSONEncoder = .captureEncoder()
    ) throws -> SignedWebhookRequest {
        let body = try encoder.encode(payload)
        let signature = HMACSigner.signatureV2(rawBody: body, timestamp: timestamp, secret: secret)
        let surface = payload.source.surface.isEmpty ? payload.source.platform : payload.source.surface
        // Preserve the existing client header value consumed by the BFF.
        let clientValue = client ?? "HermesCapture/\(surface)/\(payload.source.appVersion)"
        return SignedWebhookRequest(
            body: body,
            timestamp: timestamp,
            signature: signature,
            requestID: payload.requestID,
            client: clientValue,
            deviceID: payload.source.deviceID
        )
    }
}
