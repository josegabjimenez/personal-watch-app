import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

public enum OutboxStatus: String, Codable, Equatable, Sendable {
    case pending
    case sending
    case sent
    case failed
    case cancelled
}

public enum CaptureDeliveryPath: String, Codable, Equatable, Sendable {
    case directHTTPS = "direct_https"
    case iPhoneFallback = "iphone_fallback"
}

public struct OutboxItem: Codable, Equatable, Identifiable, Sendable {
    public var id: String { payload.requestID }
    public var payload: CapturePayloadV1
    public var status: OutboxStatus
    public var attempts: Int
    public var lastError: String?
    public var lastDeliveryPath: CaptureDeliveryPath?
    public var lastResponse: CaptureResponseV1?
    public var confirmationInvalidatedAt: String?
    public var confirmationInvalidationReason: String?
    public var cancellationMayHaveBeenSent: Bool?
    public var createdAt: String
    public var updatedAt: String

    public init(
        payload: CapturePayloadV1,
        status: OutboxStatus = .pending,
        attempts: Int = 0,
        lastError: String? = nil,
        lastDeliveryPath: CaptureDeliveryPath? = nil,
        lastResponse: CaptureResponseV1? = nil,
        createdAt: String,
        updatedAt: String
    ) {
        self.payload = payload
        self.status = status
        self.attempts = attempts
        self.lastError = lastError
        self.lastDeliveryPath = lastDeliveryPath
        self.lastResponse = lastResponse
        self.confirmationInvalidatedAt = nil
        self.confirmationInvalidationReason = nil
        self.cancellationMayHaveBeenSent = nil
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case payload
        case status
        case attempts
        case lastError = "last_error"
        case lastDeliveryPath = "last_delivery_path"
        case lastResponse = "last_response"
        case confirmationInvalidatedAt = "confirmation_invalidated_at"
        case confirmationInvalidationReason = "confirmation_invalidation_reason"
        case cancellationMayHaveBeenSent = "cancellation_may_have_been_sent"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public enum OutboxStoreError: Error, Equatable, LocalizedError {
    case requestIDConflict
    case notEnqueued
    case deliveryInProgress
    case notDeliverable
    case operationCancelled

    public var errorDescription: String? {
        switch self {
        case .requestIDConflict: return "Identificador ya utilizado · envío detenido"
        case .notEnqueued: return "Primero guarda la captura"
        case .deliveryInProgress: return "Envío en curso · resultado pendiente de verificar"
        case .notDeliverable: return "Esta captura ya no se puede enviar"
        case .operationCancelled: return "Confirmación cancelada en este dispositivo"
        }
    }
}

public enum OutboxDeliveryClaim: Sendable {
    case claimed(OutboxItem)
    case alreadySent(CaptureResponseV1?)
}

/// Batch delivery stays bounded. Only a deliberate retry of a selected grocery
/// may recover a connection failure after that limit, retaining its identity.
public enum CaptureRetryMode: Equatable, Sendable {
    case standard
    case manualGroceryRecovery
}

public actor FileOutboxStore {
    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(fileURL: URL, encoder: JSONEncoder = .captureEncoder(), decoder: JSONDecoder = .captureDecoder()) {
        self.fileURL = fileURL
        self.encoder = encoder
        self.decoder = decoder
    }

    /// All writers, retry claims and cancellation use this same cross-instance lock.
    /// Persist the cancellation tombstone and suppress unsent confirmations together.
    public func enqueue(_ payload: CapturePayloadV1, now: String) throws -> OutboxItem {
        try withExclusiveFileLock {
            var items = try loadAllUnlocked()
            if let existing = items.first(where: { $0.id == payload.requestID }) {
                guard existing.payload == payload else { throw OutboxStoreError.requestIDConflict }
                return existing
            }
            let operationID = payload.context.operationID
            if payload.route == .expense, let operationID {
                if payload.context.userCancellation == true {
                    guard OutboxDeliveryService.isSafeRequest(payload) else { throw OutboxStoreError.notDeliverable }
                    if let existing = items.first(where: {
                        $0.payload.route == .expense && $0.payload.context.userCancellation == true
                            && $0.payload.context.operationID == operationID
                    }) { return existing }
                    var cancellation = OutboxItem(payload: payload, createdAt: now, updatedAt: now)
                    cancellation.cancellationMayHaveBeenSent = items.contains {
                        $0.payload.route == .expense && $0.payload.context.userConfirmation
                            && $0.payload.context.operationID == operationID
                            && ($0.attempts > 0 || [.sending, .sent, .failed].contains($0.status))
                    }
                    invalidateConfirmations(in: &items, operationID: operationID, excludingRequestID: nil,
                                            reason: "local_cancellation", now: now)
                    for index in items.indices where items[index].payload.route == .expense
                        && items[index].payload.context.userConfirmation
                        && items[index].payload.context.operationID == operationID
                        && [.pending, .failed].contains(items[index].status) {
                        items[index].status = .cancelled
                        items[index].lastError = "operation_cancelled"
                        items[index].updatedAt = now
                    }
                    items.append(cancellation)
                    try saveAllUnlocked(items)
                    return cancellation
                }
                if payload.context.userConfirmation {
                    guard !isCancelled(operationID, in: items) else { throw OutboxStoreError.operationCancelled }
                    guard !terminalExpenseOperationIDs(in: items).contains(operationID) else { throw OutboxStoreError.notDeliverable }
                    let attempts = items.filter {
                        $0.payload.route == .expense && $0.payload.context.userConfirmation
                            && $0.payload.context.operationID == operationID
                    }
                    // The same token always retains its original immutable request ID.
                    if let existing = attempts.last(where: {
                        $0.payload.context.confirmationToken == payload.context.confirmationToken
                    }) { return existing }
                    if !attempts.isEmpty {
                        // A new token grants no authority unless it is exactly the
                        // latest active server preview. Never scan older refreshes.
                        let activePreview = items.last(where: {
                            $0.payload.route == .expense && $0.status == .sent
                                && $0.confirmationInvalidatedAt == nil
                                && $0.lastResponse?.status == "needs_confirmation"
                                && $0.lastResponse?.operationID == operationID
                        })
                        guard activePreview?.lastResponse?.confirmationToken == payload.context.confirmationToken else {
                            throw OutboxStoreError.notDeliverable
                        }
                    }
                }
            }
            let item = OutboxItem(payload: payload, createdAt: now, updatedAt: now)
            items.append(item)
            try saveAllUnlocked(items)
            return item
        }
    }

    public func claimDelivery(payload: CapturePayloadV1, now: String, maxAttempts: Int = 5,
                              retryMode: CaptureRetryMode = .standard) throws -> OutboxDeliveryClaim {
        try withExclusiveFileLock {
            var items = try loadAllUnlocked()
            guard let index = items.firstIndex(where: { $0.id == payload.requestID }) else {
                throw OutboxStoreError.notEnqueued
            }
            guard items[index].payload == payload else { throw OutboxStoreError.requestIDConflict }
            if items[index].lastResponse?.status == "needs_confirmation" {
                if let operationID = items[index].lastResponse?.operationID, isCancelled(operationID, in: items) {
                    throw OutboxStoreError.operationCancelled
                }
                guard items[index].confirmationInvalidatedAt == nil else { throw OutboxStoreError.notDeliverable }
            }
            if payload.route == .expense, payload.context.userConfirmation,
               let operationID = payload.context.operationID, isCancelled(operationID, in: items) {
                throw OutboxStoreError.operationCancelled
            }
            if items[index].status == .sent { return .alreadySent(items[index].lastResponse) }
            guard items[index].status != .sending else { throw OutboxStoreError.deliveryInProgress }
            let item = items[index]
            let connectionErrors = ["network_-1003", "network_-1004", "network_-1006", "network_-1009"]
            let manualRecovery = retryMode == .manualGroceryRecovery
                && item.status == .failed
                && payload.route == .grocery
                && !payload.context.dryRun
                && OutboxDeliveryService.isSafeRequest(payload)
                && connectionErrors.contains(item.lastError ?? "")
            guard [.pending, .failed].contains(item.status), item.attempts < Int.max,
                  item.attempts < maxAttempts || manualRecovery else {
                throw OutboxStoreError.notDeliverable
            }
            items[index].status = .sending
            items[index].attempts += 1
            items[index].lastDeliveryPath = nil
            items[index].updatedAt = now
            try saveAllUnlocked(items)
            return .claimed(items[index])
        }
    }

    private func isCancelled(_ operationID: String, in items: [OutboxItem]) -> Bool {
        items.contains {
            $0.payload.route == .expense && $0.payload.context.userCancellation == true
                && $0.payload.context.operationID == operationID
        }
    }

    private func terminalExpenseOperationIDs(in items: [OutboxItem]) -> Set<String> {
        Set(items.compactMap { item in
            guard item.status == .sent, let response = item.lastResponse,
                  response.status != "needs_confirmation", let operationID = response.operationID,
                  shouldConsumePriorExpenseConfirmation(response: response, payload: item.payload) else { return nil }
            return operationID
        })
    }

    public func loadAll() throws -> [OutboxItem] {
        try withExclusiveFileLock {
            try loadAllUnlocked()
        }
    }

    private func loadAllUnlocked() throws -> [OutboxItem] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return [] }
        let data = try Data(contentsOf: fileURL)
        if data.isEmpty { return [] }
        return try decoder.decode([OutboxItem].self, from: data)
    }

    public func loadDeliverable(maxAttempts: Int = 5, limit: Int = 10) throws -> [OutboxItem] {
        let items = try loadAll()
        return Array(items.filter { item in
            guard [.pending, .failed].contains(item.status), item.attempts < maxAttempts else { return false }
            if item.payload.route == .expense, item.payload.context.userConfirmation,
               let operationID = item.payload.context.operationID, isCancelled(operationID, in: items) { return false }
            return true
        }.prefix(limit))
    }

    public func markSending(requestID: String, now: String) throws {
        guard let payload = try loadAll().first(where: { $0.id == requestID })?.payload else {
            throw OutboxStoreError.notEnqueued
        }
        _ = try claimDelivery(payload: payload, now: now)
    }

    @discardableResult
    public func markSent(
        requestID: String,
        now: String,
        deliveryPath: CaptureDeliveryPath? = nil,
        response: CaptureResponseV1? = nil
    ) throws -> OutboxItem? {
        try withExclusiveFileLock {
            var items = try loadAllUnlocked()
            guard let index = items.firstIndex(where: { $0.payload.requestID == requestID }) else {
                return nil
            }

            items[index].status = .sent
            items[index].lastError = nil
            items[index].lastDeliveryPath = deliveryPath
            items[index].lastResponse = response
            items[index].updatedAt = now
            if let response, response.status == "needs_confirmation", let operationID = response.operationID,
               isCancelled(operationID, in: items) || terminalExpenseOperationIDs(in: items).contains(operationID) {
                items[index].confirmationInvalidatedAt = now
                items[index].confirmationInvalidationReason = "operation_terminal"
            }

            if
                let response,
                let operationID = response.operationID,
                shouldConsumePriorExpenseConfirmation(
                    response: response,
                    payload: items[index].payload
                )
            {
                invalidateConfirmations(
                    in: &items,
                    operationID: operationID,
                    excludingRequestID: requestID,
                    reason: response.status,
                    now: now
                )
            }

            try saveAllUnlocked(items)
            return items[index]
        }
    }

    public func loadPendingExpenseConfirmations() throws -> [OutboxItem] {
        let items = try loadAll()
        let cancelledOperationIDs = Set(items.compactMap { item -> String? in
            guard
                item.payload.route.domain == .meganExpenseCapture,
                item.payload.context.userCancellation == true,
                let operationID = item.payload.context.operationID,
                !operationID.isEmpty
            else { return nil }
            return operationID
        })
        let terminalOperationIDs = terminalExpenseOperationIDs(in: items)
        return items
            .filter { item in
                guard
                    item.status == .sent,
                    item.confirmationInvalidatedAt == nil,
                    item.payload.route.domain == .meganExpenseCapture,
                    let response = item.lastResponse,
                    response.status == "needs_confirmation",
                    let operationID = response.operationID,
                    !cancelledOperationIDs.contains(operationID),
                    !terminalOperationIDs.contains(operationID),
                    OutboxDeliveryService.isSafe(response: response, for: item.payload)
                else { return false }
                return true
            }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    @discardableResult
    public func consumeExpenseConfirmation(operationID: String, now: String) throws -> Bool {
        try withExclusiveFileLock {
            var items = try loadAllUnlocked()
            let changed = invalidateConfirmations(
                in: &items,
                operationID: operationID,
                excludingRequestID: nil,
                reason: "consumed",
                now: now
            )
            if changed {
                try saveAllUnlocked(items)
            }
            return changed
        }
    }

    private func shouldConsumePriorExpenseConfirmation(
        response: CaptureResponseV1,
        payload: CapturePayloadV1
    ) -> Bool {
        guard
            payload.route.domain == .meganExpenseCapture,
            OutboxDeliveryService.isSafe(response: response, for: payload)
        else {
            return false
        }
        if response.status == "needs_confirmation" {
            return true
        }
        if payload.context.userCancellation == true {
            return ["cancelled", "duplicate"].contains(response.status)
        }
        if !payload.context.userConfirmation, ["rejected", "needs_input"].contains(response.status),
           response.operationID != nil {
            return true
        }
        return payload.context.userConfirmation
            && response.operationID == payload.context.operationID
            && (["rejected", "needs_input"].contains(response.status)
                || (["committed", "duplicate"].contains(response.status) && response.writeCommitted == true)
                || (response.status == "duplicate" && response.writeCommitted == false))
    }

    @discardableResult
    private func invalidateConfirmations(
        in items: inout [OutboxItem],
        operationID: String,
        excludingRequestID: String?,
        reason: String,
        now: String
    ) -> Bool {
        var changed = false
        for index in items.indices {
            let item = items[index]
            guard
                item.payload.requestID != excludingRequestID,
                item.status == .sent,
                item.confirmationInvalidatedAt == nil,
                item.payload.route.domain == .meganExpenseCapture,
                let response = item.lastResponse,
                response.status == "needs_confirmation",
                response.operationID == operationID,
                OutboxDeliveryService.isSafe(response: response, for: item.payload)
            else { continue }
            items[index].confirmationInvalidatedAt = now
            items[index].confirmationInvalidationReason = reason
            items[index].updatedAt = now
            changed = true
        }
        return changed
    }

    public func markFailed(requestID: String, message: String, now: String) throws {
        try withExclusiveFileLock {
            var items = try loadAllUnlocked()
            guard let index = items.firstIndex(where: { $0.id == requestID }), items[index].status == .sending else { return }
            let item = items[index]
            let cancelled = item.payload.route == .expense && item.payload.context.userConfirmation
                && item.payload.context.operationID.map { isCancelled($0, in: items) } == true
            items[index].status = cancelled ? .cancelled : .failed
            items[index].lastError = message
            items[index].lastDeliveryPath = nil
            items[index].updatedAt = now
            try saveAllUnlocked(items)
        }
    }

    private func saveAllUnlocked(_ items: [OutboxItem]) throws {
        let directory = fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let data = try encoder.encode(items)
        #if os(iOS) || os(watchOS)
        let writeOptions: Data.WritingOptions = [
            .atomic,
            .completeFileProtectionUntilFirstUserAuthentication
        ]
        #else
        let writeOptions: Data.WritingOptions = [.atomic]
        #endif
        try data.write(to: fileURL, options: writeOptions)
    }

    private func withExclusiveFileLock<T>(_ body: () throws -> T) throws -> T {
        let directory = fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let lockURL = fileURL.appendingPathExtension("lock")
        let descriptor = lockURL.path.withCString {
            open($0, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        }
        guard descriptor >= 0 else {
            throw NSError(
                domain: "PersonalWatchCore.OutboxLock",
                code: Int(errno),
                userInfo: [NSLocalizedDescriptionKey: "Could not open outbox lock"]
            )
        }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_EX) == 0 else {
            throw NSError(
                domain: "PersonalWatchCore.OutboxLock",
                code: Int(errno),
                userInfo: [NSLocalizedDescriptionKey: "Could not lock outbox"]
            )
        }
        defer { flock(descriptor, LOCK_UN) }
        return try body()
    }
}
