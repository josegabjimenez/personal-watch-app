/// Presents durable local uncertainty without changing a request or receipt.
public enum OutboxPresentation {
    public static func retentionDetail(for item: OutboxItem) -> String? {
        guard item.isRetrySuppressed else { return nil }
        if item.payload.route == .reminder {
            return "El resultado anterior se conserva; no se canceló en Todoist."
        }
        if item.attempts > 0, item.status == .sending || item.status == .failed {
            return "El resultado del envío anterior sigue por verificar."
        }
        return nil
    }

    public static func message(
        for item: OutboxItem,
        response: CaptureResponseV1? = nil
    ) -> String? {
        let receipt = response ?? item.lastResponse
        if item.isRetrySuppressed, item.payload.route == .expense, receipt?.status == "needs_confirmation" {
            return "Vista previa recibida · conservada en Historial"
        }
        guard item.payload.route == .expense,
              item.payload.context.userCancellation == true,
              item.cancellationMayHaveBeenSent == true else {
            return receipt?.displayMessage ?? receipt?.question
        }

        // A cancellation receipt acknowledges this request. Keep the durable
        // warning about an earlier confirmation, even if server display copy
        // describes only the cancellation's own no-write outcome.
        if let receipt,
           ["cancelled", "duplicate"].contains(receipt.status),
           OutboxDeliveryService.isSafe(response: receipt, for: item.payload) {
            return "Cancelación recibida · verifica si hubo un registro previo en Firefly"
        }
        return "Cancelación solicitada · el envío anterior puede haberse registrado; verifica en Firefly"
    }
}
