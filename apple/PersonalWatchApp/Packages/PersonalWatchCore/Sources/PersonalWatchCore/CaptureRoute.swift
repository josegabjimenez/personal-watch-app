import Foundation

public enum CaptureAgent: String, Codable, CaseIterable, Equatable, Sendable {
    case megan
    case aura
    case argos
    case pipo
    case atenea
    case horacio
}

public enum CaptureDomain: String, Codable, CaseIterable, Equatable, Sendable {
    case meganExpenseCapture = "megan.expense_capture"
    case auraReminderCapture = "aura.reminder_capture"
    case auraGroceryCapture = "aura.grocery_capture"
    case auraHomeAction = "aura.home_action"
    case auraGeneralLifeCapture = "aura.general_life_capture"
    case argosGeneralCapture = "argos.general_capture"
    case pipoCodingTaskCapture = "pipo.coding_task_capture"
    case ateneaResearchCapture = "atenea.research_capture"
    case horacioDesignBriefCapture = "horacio.design_brief_capture"
}

public struct CaptureRoute: Codable, Equatable, Sendable {
    public var agent: CaptureAgent
    public var intent: String
    public var domain: CaptureDomain

    public init(agent: CaptureAgent, intent: String, domain: CaptureDomain) {
        self.agent = agent
        self.intent = intent
        self.domain = domain
    }

    public static let expense = CaptureRoute(agent: .megan, intent: "expense", domain: .meganExpenseCapture)
    public static let reminder = CaptureRoute(agent: .aura, intent: "reminder", domain: .auraReminderCapture)
    public static let grocery = CaptureRoute(agent: .aura, intent: "grocery", domain: .auraGroceryCapture)
    public static let general = CaptureRoute(agent: .argos, intent: "general_capture", domain: .argosGeneralCapture)
}

public struct CaptureSource: Codable, Equatable, Sendable {
    public var app: String
    public var appVersion: String
    public var platform: String
    public var osVersion: String
    public var deviceID: String
    public var locale: String
    public var timezone: String
    public var surface: String

    public init(
        // Keep the established wire identity for the existing BFF.
        app: String = "HermesCapture",
        appVersion: String,
        platform: String,
        osVersion: String,
        deviceID: String,
        locale: String = "es_CO",
        timezone: String = "America/Bogota",
        surface: String
    ) {
        self.app = app
        self.appVersion = appVersion
        self.platform = platform
        self.osVersion = osVersion
        self.deviceID = deviceID
        self.locale = locale
        self.timezone = timezone
        self.surface = surface
    }

    enum CodingKeys: String, CodingKey {
        case app
        case appVersion = "app_version"
        case platform
        case osVersion = "os_version"
        case deviceID = "device_id"
        case locale
        case timezone
        case surface
    }
}

public struct CaptureText: Codable, Equatable, Sendable {
    public var modality: String
    public var language: String
    public var text: String
    public var rawText: String

    public init(modality: String, language: String = "es", text: String, rawText: String? = nil) {
        self.modality = modality
        self.language = language
        self.text = text
        self.rawText = rawText ?? text
    }

    enum CodingKeys: String, CodingKey {
        case modality
        case language
        case text
        case rawText = "raw_text"
    }
}
