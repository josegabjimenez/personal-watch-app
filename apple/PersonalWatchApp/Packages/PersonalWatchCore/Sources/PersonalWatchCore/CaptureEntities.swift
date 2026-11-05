@preconcurrency import Foundation

public struct CaptureAmount: Codable, Equatable, Sendable {
    public var value: Decimal
    public var currency: String

    public init(value: Decimal, currency: String = "COP") {
        self.value = value
        self.currency = currency
    }
}

public struct CaptureEntities: Codable, Equatable, Sendable {
    public var amount: CaptureAmount?
    public var currency: String?
    public var merchant: String?
    public var concept: String?
    public var accountHint: String?
    public var cardHint: String?
    public var dueAt: String?
    public var calendarAt: String?
    public var tags: [String]

    public init(
        amount: CaptureAmount? = nil,
        currency: String? = "COP",
        merchant: String? = nil,
        concept: String? = nil,
        accountHint: String? = nil,
        cardHint: String? = nil,
        dueAt: String? = nil,
        calendarAt: String? = nil,
        tags: [String] = []
    ) {
        self.amount = amount
        self.currency = currency
        self.merchant = merchant
        self.concept = concept
        self.accountHint = accountHint
        self.cardHint = cardHint
        self.dueAt = dueAt
        self.calendarAt = calendarAt
        self.tags = tags
    }

    enum CodingKeys: String, CodingKey {
        case amount
        case currency
        case merchant
        case concept
        case accountHint = "account_hint"
        case cardHint = "card_hint"
        case dueAt = "due_at"
        case calendarAt = "calendar_at"
        case tags
    }
}

public struct CaptureContext: Codable, Equatable, Sendable {
    public var userConfirmation: Bool
    public var userCancellation: Bool?
    public var watchReachableToPhone: Bool
    public var shortcutCompatibility: Bool
    public var requiresConfirmation: Bool
    public var dryRun: Bool
    public var allowWrite: Bool
    public var allowFireflyWrite: Bool?
    public var confirmationToken: String?
    public var operationID: String?

    public init(
        userConfirmation: Bool = false,
        userCancellation: Bool? = nil,
        watchReachableToPhone: Bool = false,
        shortcutCompatibility: Bool = false,
        requiresConfirmation: Bool = false,
        dryRun: Bool = true,
        allowWrite: Bool = false,
        allowFireflyWrite: Bool? = nil,
        confirmationToken: String? = nil,
        operationID: String? = nil
    ) {
        self.userConfirmation = userConfirmation
        self.userCancellation = userCancellation
        self.watchReachableToPhone = watchReachableToPhone
        self.shortcutCompatibility = shortcutCompatibility
        self.requiresConfirmation = requiresConfirmation
        self.dryRun = dryRun
        self.allowWrite = allowWrite
        self.allowFireflyWrite = allowFireflyWrite
        self.confirmationToken = confirmationToken
        self.operationID = operationID
    }

    enum CodingKeys: String, CodingKey {
        case userConfirmation = "user_confirmation"
        case userCancellation = "user_cancellation"
        case watchReachableToPhone = "watch_reachable_to_phone"
        case shortcutCompatibility = "shortcut_compatibility"
        case requiresConfirmation = "requires_confirmation"
        case dryRun = "dry_run"
        case allowWrite = "allow_write"
        case allowFireflyWrite = "allow_firefly_write"
        case confirmationToken = "confirmation_token"
        case operationID = "operation_id"
    }
}
