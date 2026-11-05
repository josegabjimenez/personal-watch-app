# Personal Watch App — personal MVP

The product was renamed **Personal Watch App** on October 28, 2026. The three-action scope established October 25 is unchanged. Existing bundle IDs, storage keys, protocol values and external operation IDs retain their earlier identifiers for compatibility.

Source task: existing W-R02 (planning record kept private). Jose approved implementation for his own iPhone and Apple Watch. This delivery prepares code and a reviewable PR; it does not authorize merge, production deployment, financial transactions, or acceptance of device gates.

## Three actions

| Action | Behavior | Evidence required for real use |
| --- | --- | --- |
| Mercado | Add to the existing official list. A committed helper result is required before claiming success. | Reviewed exact Aura helper, official-list identity, normalized matching/pagination, concurrent duplicates, and lost-ACK recovery without changing existing item state. |
| Recordatorio | Create only the requested new reminder with an unambiguous future Bogotá date/time. Unsupported/ambiguous schedules ask for clarification before writing. | Structured AI proposal, deterministic date validation, durable Todoist command and exact Inbox task readback; default alert configuration and phone notification require separate verification. |
| Gasto | One COP withdrawal from an existing active asset account. Preview the frozen amount, account, category and budget; require a separate explicit confirmation. | Existing catalogue/rules, COP agreement, durable operation ledger and external-ID readback. A live financial check needs separate authorization. |

General capture/Capturar and Argos dispatch are excluded. Old protocol values/history remain readable; the old general intent is disabled and is not advertised. There are no favorite actions. Cards, installments, transfers, incomes, new accounts/categories/budgets, and modification of preexisting reminders are excluded.

## Confirmation, cancellation, and recovery

Expense preview and confirmation are distinct payloads. The BFF freezes and signs the plan; confirmation does not reinterpret the dictation. Repeated taps reuse the same confirmation request and operation. The adapter uses `external_id=hermes-watch:{operation_id}` and reconciles an uncertain result before any further decision; it never posts again after an uncertain claim.

Cancelling first persists a per-operation tombstone and suppresses unsent confirmations under the same file lock used by delivery claims. Persistence failure does not produce a local success message. A confirmation already attempted or in flight may have committed: local cancellation cannot promise reversal. The UI states that uncertainty and preserves the audit identity. A correlated terminal rejection invalidates a preview durably; a network failure preserves it for safe retry/cancellation. Relaunch does not grant renewed permission.

The `.sent` outbox state means a validated response was received, which can be a preview, clarification, cancellation or rejection. Only `write_committed=true` establishes business success. `writing`/`uncertain` expenses require reconciliation; stale preview responses never claim that an earlier expense did not occur.

Live Watch traffic requires direct HTTPS. The existing phone relay accepts dry-run requests only. A relay smoke test cannot certify Watch live-write connectivity.

## Reminder alert handoff

The Reminder action uses the existing standalone BFF with a schema-only AI interpreter and a direct Todoist Inbox adapter. The AI proposes a task title and schedule; server code alone permits the destination, action and future Bogotá instant. The original capture timestamp anchors relative schedules. A validated interpretation and stable Sync command UUID are committed before the external write, then reused after retries or crashes. There is no Reminder fallback to the Aura helper, Notion, Calendar or Slack.

Only exact task readback establishes `write_committed=true`. Responses include `interpreted_due`, `external_ids.todoist_task_id`, `notification_verified=false`, and `delivery_status=pending_verification`. `auto_reminder=true` requests Todoist's user-default alert for the timed task; it does not establish the account's alert configuration or device receipt. Deadlines, recurring tasks and custom alerts remain unsupported in this increment. No existing reminders are migrated.

See [the operator guide](REMINDER_AI_TODOIST.md) for private server configuration, replay/recovery behavior, offline verification and the remaining secure-authorization, deployment, live task and phone-alert gates. Grocery retains the existing exact official-list helper; the confirmed COP expense flow remains separate.

## Build and physical-device handoff

Use the installed Xcode with command-scoped `DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer`; do not change the global developer directory. Generate the project with `xcodegen generate` in `apple/PersonalWatchApp`, then open `PersonalWatchApp.xcodeproj`. The current schemes are `PersonalWatchAppiOS` and `PersonalWatchAppWatch`, with the shared `PersonalWatchCore` package. The legacy bundle IDs stay `dev.josegabjimenez.HermesCapture` and `dev.josegabjimenez.HermesCapture.watchkitapp`, preserving the companion relationship and installed data. This recovered candidate remains **0.3.4 (7)**; provenance/commit identify this new artifact, not the version number alone.

Simulator UI tests use a Debug/simulator-only, explicit launch-argument fixture and an isolated per-case outbox. The fixture intercepts every request and cannot reach real services or write configuration/Keychain. Device and Release builds exclude it. Core/backend tests use synthetic data.

The [device setup follow-up](XCODE_DEVICE_SETUP.md) records the earlier candidate under its former Hermes display name: the existing authorized Team and automatic signing produced a verified signed iOS/Watch build, and version **0.3.4 (7)** installed and launched on the iPhone after developer trust. The rebranded candidate has not been installed. Physical Watch installation remains unresolved: its Bluetooth tunnel failed, and Jose reported companion installation returning to the Install button. The actual installer rejection is unknown. The rename does not require another device action while Jose is away from the Mac. Bundle IDs remain unchanged. Once the rebranded Watch candidate is installed and launch is verified, exercise:

- each Siri phrase on iPhone and Watch; expense opens review and requires tapping Register;
- Watch direct HTTPS with the phone absent; locked/unlocked and cold-start/bootstrap behavior;
- offline capture, retry of the same item, double tap, ACK loss, relaunch, cancellation and expiry;
- truthful per-action confirmation and actual reminder notification arrival;
- privacy manifests in the signed archive and local data retention/protection.

Unsigned builds, simulator tests, signed archive creation, physical installation, live-domain behavior, and human acceptance are separate gates. Current results and remaining gates are in [the verification report](verification/2026-10-25/REPORT.md).
