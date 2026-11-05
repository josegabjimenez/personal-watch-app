import Foundation

public enum QuickActionKind: String, CaseIterable, Identifiable, Sendable {
    case expense
    case reminder
    case grocery
    case general

    public static let mvpActions: [QuickActionKind] = [.grocery, .reminder, .expense]

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .expense: return "Gasto"
        case .reminder: return "Recordatorio"
        case .grocery: return "Mercado"
        case .general: return "Capturar"
        }
    }

    public var symbolName: String {
        switch self {
        case .expense: return "creditcard"
        case .reminder: return "bell"
        case .grocery: return "cart"
        case .general: return "sparkles"
        }
    }

    public var route: CaptureRoute {
        switch self {
        case .expense: return .expense
        case .reminder: return .reminder
        case .grocery: return .grocery
        case .general: return .general
        }
    }

    public var modality: String {
        switch self {
        case .expense, .reminder, .grocery, .general:
            return "watch_dictation"
        }
    }

    /// Requests a live write from the BFF; server-side gates remain authority.
    public var requestsLiveWrite: Bool {
        Self.mvpActions.contains(self)
    }
}

public struct CaptureFactory: Sendable {
    public var appVersion: String
    public var platform: String
    public var osVersion: String
    public var deviceID: String
    public var surface: String
    public var nowISO8601: @Sendable () -> String
    public var makeRequestID: @Sendable () -> String

    public init(
        appVersion: String,
        platform: String,
        osVersion: String,
        deviceID: String,
        surface: String,
        nowISO8601: @escaping @Sendable () -> String,
        makeRequestID: @escaping @Sendable () -> String
    ) {
        self.appVersion = appVersion
        self.platform = platform
        self.osVersion = osVersion
        self.deviceID = deviceID
        self.surface = surface
        self.nowISO8601 = nowISO8601
        self.makeRequestID = makeRequestID
    }

    public func makePayload(
        kind: QuickActionKind,
        text: String,
        rawText: String? = nil,
        modality: String? = nil,
        requestWrite: Bool = false,
        userConfirmation: Bool = false,
        userCancellation: Bool = false,
        confirmationToken: String? = nil,
        operationID: String? = nil
    ) -> CapturePayloadV1 {
        let requestWrite = requestWrite && kind.requestsLiveWrite
        return CapturePayloadV1(
            requestID: makeRequestID(),
            createdAt: nowISO8601(),
            source: CaptureSource(
                appVersion: appVersion,
                platform: platform,
                osVersion: osVersion,
                deviceID: deviceID,
                surface: surface
            ),
            route: kind.route,
            capture: CaptureText(
                modality: modality ?? kind.modality,
                text: text,
                rawText: rawText
            ),
            context: CaptureContext(
                userConfirmation: userConfirmation,
                userCancellation: userCancellation,
                requiresConfirmation: kind == .expense && requestWrite && !userConfirmation && !userCancellation,
                dryRun: !requestWrite,
                allowWrite: requestWrite,
                allowFireflyWrite: requestWrite && kind == .expense && !userCancellation,
                confirmationToken: confirmationToken,
                operationID: operationID
            )
        )
    }
}
