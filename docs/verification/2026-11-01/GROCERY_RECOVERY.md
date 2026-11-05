# Grocery retry and new-item recovery

Verified on 1 November 2026 with Xcode 27.2. App version **0.3.5 (8)**.

## Problem and behavior

Five connection failures exhausted the saved Mercado request. Reopening the action restored that request and disabled its text field, but provided no way to compose another article.

- A deliberate retry of the selected live grocery request may recover after the batch limit only when its durable state is failed and its last error is a known connection/hostname/offline failure (-1003, -1004, -1006 or -1009). The locked claim preserves the original payload and request ID and increments the existing attempt count. Standard/batch retries remain capped at five. No launch, configuration update or restore initiates recovery.
- Sending/uncertain requests, unsafe payloads, HTTP failures and other transport errors do not receive this exception. Reminder, expense and general routes do not receive it. Existing correlated-response, HMAC, cancellation and financial confirmation checks remain in force.
- **Otro artículo** opens an empty grocery editor without modifying or removing the previous outbox row. It is for a distinct article. Reopening the action may restore the previous pending item and always exposes the same escape while idle; the new draft choice is not claimed to persist across relaunch.

## Verification

| Check | Result |
| --- | --- |
| Shared Swift suite on physical-app source | 64 passed |
| Shared Swift suite with preserved PR reminder response test | 65 passed |
| iOS simulator UI suite | 10 passed |
| Signed iPhone and exact embedded Watch builds | Passed |
| Existing signing identity, bundle IDs, physical-device profiles | Verified unchanged/valid |
| iPhone and Watch installed metadata | 0.3.5 (8), one matching app each |
| Plain launch on both physical devices | Passed |
| Both saved outboxes and endpoints after update | Exactly preserved |
| Real Watch Otro artículo | Blank composer visibly opened; outbox unchanged afterward |
| Independent review | No consequential findings; DNS/offline test coverage and iPhone copy refinements applied |

The tests exercise byte-identical original request bodies after five failures, monotonic attempts, a cached receipt without redispatch, concurrent store claims, rejected routes/errors, composer escape and restoration, plus existing expense confirmation/cancellation regressions. The network-failure UI fixture exists only in Debug simulator builds and uses no service or credentials.

The physical-app source is `52867ae`; its application/core source matches review commit `bc7d751`. The review branch preserves the existing AI reminder work. No backend code/service/configuration was deployed or restarted, no live capture was submitted or replayed, and AI integration remains on human hold. Phone DNS recovery was reported by Jose; this fix addresses the separate app retry/editor defect. Live grocery delivery and the full reminder/expense MVP remain separate acceptance work. Developer-profile renewal remains due on 8 November; no automatic reminder was created.

Private device identifiers, endpoint, signing material, screenshots and captured text are excluded. The earlier developer-support image mount failure cleared after the Watch was ready; the final install/readback above is authoritative.
