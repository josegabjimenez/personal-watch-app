import Foundation

public struct CaptureResponseV1: Codable, Equatable, Sendable {
    public var status: String
    public var dryRun: Bool?
    public var requestID: String?
    public var domain: String?
    public var runID: String?
    public var operationID: String?
    public var confirmationToken: String?
    public var confirmation: ExpenseConfirmationSummary?
    public var displayMessage: String?
    public var question: String?
    public var duplicate: Bool?
    public var writeCommitted: Bool?
    public var plan: CaptureResponsePlan?
    public var gatesMissing: [String]?
    public var interpretedDue: CaptureInterpretedDue?
    public var calendarSynced: Bool?
    public var calendarSyncPending: Bool?
    public var externalIDs: [String: CaptureJSONValue]?
    public var notificationVerified: Bool?
    public var deliveryStatus: String?

    public init(
        status: String,
        dryRun: Bool? = nil,
        requestID: String? = nil,
        domain: String? = nil,
        runID: String? = nil,
        operationID: String? = nil,
        confirmationToken: String? = nil,
        confirmation: ExpenseConfirmationSummary? = nil,
        displayMessage: String? = nil,
        question: String? = nil,
        duplicate: Bool? = nil,
        writeCommitted: Bool? = nil,
        plan: CaptureResponsePlan? = nil,
        gatesMissing: [String]? = nil,
        interpretedDue: CaptureInterpretedDue? = nil,
        calendarSynced: Bool? = nil,
        calendarSyncPending: Bool? = nil,
        externalIDs: [String: CaptureJSONValue]? = nil,
        notificationVerified: Bool? = nil,
        deliveryStatus: String? = nil
    ) {
        self.status = status
        self.dryRun = dryRun
        self.requestID = requestID
        self.domain = domain
        self.runID = runID
        self.operationID = operationID
        self.confirmationToken = confirmationToken
        self.confirmation = confirmation
        self.displayMessage = displayMessage
        self.question = question
        self.duplicate = duplicate
        self.writeCommitted = writeCommitted
        self.plan = plan
        self.gatesMissing = gatesMissing
        self.interpretedDue = interpretedDue
        self.calendarSynced = calendarSynced
        self.calendarSyncPending = calendarSyncPending
        self.externalIDs = externalIDs
        self.notificationVerified = notificationVerified
        self.deliveryStatus = deliveryStatus
    }

    enum CodingKeys: String, CodingKey {
        case status
        case dryRun = "dry_run"
        case requestID = "request_id"
        case domain
        case runID = "run_id"
        case operationID = "operation_id"
        case confirmationToken = "confirmation_token"
        case confirmation
        case displayMessage = "display_message"
        case question
        case duplicate
        case writeCommitted = "write_committed"
        case plan
        case gatesMissing = "gates_missing"
        case interpretedDue = "interpreted_due"
        case calendarSynced = "calendar_synced"
        case calendarSyncPending = "calendar_sync_pending"
        case externalIDs = "external_ids"
        case notificationVerified = "notification_verified"
        case deliveryStatus = "delivery_status"
    }
}

public struct ExpenseConfirmationSummary: Codable, Equatable, Sendable {
    public var amountCOP: Int
    public var description: String
    public var sourceName: String
    public var categoryName: String
    public var budgetName: String

    public init(
        amountCOP: Int,
        description: String,
        sourceName: String,
        categoryName: String,
        budgetName: String
    ) {
        self.amountCOP = amountCOP
        self.description = description
        self.sourceName = sourceName
        self.categoryName = categoryName
        self.budgetName = budgetName
    }

    enum CodingKeys: String, CodingKey {
        case amountCOP = "amount_cop"
        case description
        case sourceName = "source_name"
        case categoryName = "category_name"
        case budgetName = "budget_name"
    }
}

public struct CaptureResponsePlan: Codable, Equatable, Sendable {
    public var sideEffects: [String]?
    public var wouldWrite: Bool?
    public var notes: String?

    public init(sideEffects: [String]? = nil, wouldWrite: Bool? = nil, notes: String? = nil) {
        self.sideEffects = sideEffects
        self.wouldWrite = wouldWrite
        self.notes = notes
    }

    enum CodingKeys: String, CodingKey {
        case sideEffects = "side_effects"
        case wouldWrite = "would_write"
        case notes
    }
}

public struct CaptureInterpretedDue: Codable, Equatable, Sendable {
    public var start: String
    public var timezone: String
    public var display: String?

    public init(start: String, timezone: String, display: String? = nil) {
        self.start = start
        self.timezone = timezone
        self.display = display
    }
}

/// External audit identifiers may contain strings and arrays (Firefly journals).
public enum CaptureJSONValue: Codable, Equatable, Sendable, ExpressibleByStringLiteral {
    case string(String)
    case number(Decimal)
    case bool(Bool)
    case array([CaptureJSONValue])
    case object([String: CaptureJSONValue])
    case null

    public init(stringLiteral value: String) { self = .string(value) }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode(Decimal.self) { self = .number(value) }
        else if let value = try? container.decode([CaptureJSONValue].self) { self = .array(value) }
        else { self = .object(try container.decode([String: CaptureJSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}
