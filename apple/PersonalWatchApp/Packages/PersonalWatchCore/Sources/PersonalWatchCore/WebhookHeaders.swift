// Existing BFF header names are compatibility identifiers, not display branding.
public enum WebhookHeaders {
    public static let timestamp = "X-Webhook-Timestamp"
    public static let signatureV2 = "X-Webhook-Signature-V2"
    public static let requestID = "X-Request-ID"
    public static let payloadVersion = "X-Hermes-Payload-Version"
    public static let client = "X-Hermes-Client"
    public static let deviceID = "X-Hermes-Device-ID"
}
