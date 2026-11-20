/// Presents durable local uncertainty without changing a request or receipt.
public enum OutboxPresentation {
    public static func message(
        for item: OutboxItem,
        response: CaptureResponseV1? = nil
    ) -> String? {
        let receipt = response ?? item.lastResponse
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
