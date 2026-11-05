#if canImport(Security)
import Foundation
import Security

public enum KeychainRouteSecretError: Error, Equatable, Sendable {
    case unexpectedStatus(OSStatus)
    case invalidStoredData
}

public final class KeychainRouteSecretStore: RouteSecretStore, @unchecked Sendable {
    // Retain access to the installed app's existing Keychain item.
    public static let defaultService = "dev.josegabjimenez.HermesCapture.mobile-capture"
    public static let defaultAccount = "route-hmac-secret-v1"

    private let service: String
    private let account: String

    public init(
        service: String = KeychainRouteSecretStore.defaultService,
        account: String = KeychainRouteSecretStore.defaultAccount
    ) {
        self.service = service
        self.account = account
    }

    public func loadRouteSecret() async throws -> String? {
        try loadRouteSecretSynchronously()
    }

    public func loadRouteSecretSynchronously() throws -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess else {
            throw KeychainRouteSecretError.unexpectedStatus(status)
        }
        guard
            let data = result as? Data,
            let secret = String(data: data, encoding: .utf8)
        else {
            throw KeychainRouteSecretError.invalidStoredData
        }
        return secret
    }

    public func saveRouteSecret(_ secret: String) async throws {
        try saveRouteSecretSynchronously(secret)
    }

    public func saveRouteSecretSynchronously(_ secret: String) throws {
        let data = Data(secret.utf8)
        let update: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]
        let updateStatus = SecItemUpdate(baseQuery as CFDictionary, update as CFDictionary)

        if updateStatus == errSecSuccess {
            return
        }
        guard updateStatus == errSecItemNotFound else {
            throw KeychainRouteSecretError.unexpectedStatus(updateStatus)
        }

        var insert = baseQuery
        insert[kSecValueData as String] = data
        insert[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let insertStatus = SecItemAdd(insert as CFDictionary, nil)
        guard insertStatus == errSecSuccess else {
            throw KeychainRouteSecretError.unexpectedStatus(insertStatus)
        }
    }

    public func deleteRouteSecret() async throws {
        try deleteRouteSecretSynchronously()
    }

    public func deleteRouteSecretSynchronously() throws {
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainRouteSecretError.unexpectedStatus(status)
        }
    }

    private var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
    }
}
#endif
