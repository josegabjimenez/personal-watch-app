# Personal Watch App rename · 28 October 2026

The current product name is **Personal Watch App**. This is a branding change to the existing three-action MVP and PR #1. Mercado, Recordatorio and confirmed COP Gasto retain their existing scope and contracts. Backend framework/AI changes are outside this delivery.

## Repository and source

The canonical repository is [josegabjimenez/personal-watch-app](https://github.com/josegabjimenez/personal-watch-app), with the same [PR #1](https://github.com/josegabjimenez/personal-watch-app/pull/1). Before renaming, the authenticated owner/admin account, destination availability and existing repository/PR identities were checked. Afterward, repository identity, PR identity/open state, head commit/branch, default branch and public visibility matched. This checkout's `origin` now uses the canonical URL. No second repository or PR was created.

There were no Actions workflows, webhooks, Pages site or deployments attached before publication. GitHub's [repository rename behavior](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository) preserves redirects/history; the new canonical links are used for this delivery.

Current Apple source is `apple/PersonalWatchApp`, the generated project is `PersonalWatchApp.xcodeproj`, and the product schemes are `PersonalWatchAppiOS` and `PersonalWatchAppWatch`. The shared package and test host use `PersonalWatchCore` and `PersonalWatchCoreTestHost`. App display names, navigation/setup/dictation/result copy, Siri action titles/descriptions, current documentation and repository metadata use Personal Watch App. Shortcut phrases already interpolate the application name. The active contract is [PERSONAL_WATCH_APP_MVP.md](../../PERSONAL_WATCH_APP_MVP.md); the previous filename remains a compatibility pointer.

Routine internal runtime, defaults, entrypoint, package, enum and JSON-helper codenames were renamed. The source manifest pins the current implementation; the October 25 source hashes, results and screenshot retain their historical meaning. Reproduce with `PERSONAL_WATCH_VERIFICATION_OUTPUT=<fresh-private-directory> bash scripts/verify_mvp.sh`; freeze the reviewed clean commit with `python3 scripts/freeze_mvp_candidate.py`.

## Exact compatibility names retained

| Names | Reason |
| --- | --- |
| `dev.josegabjimenez.HermesCapture` and `.watchkitapp` | Existing bundle/signing/container identity and the companion relationship. No data or signing migration was requested. |
| `HermesCaptureiOS`, `HermesCaptureWatch`, `GeneralHermesCaptureIntent`, `HermesCaptureAppShortcuts` | Saved App Intent and shortcut type identities. Product targets/executables use the new names while explicit Swift module pins preserve metadata identity. The other actual action types also remain unchanged. |
| `hermes.baseURL`, `hermes.pseudonymousDeviceID`, `hermes.pendingExpensePreview`, Keychain service `dev.josegabjimenez.HermesCapture.mobile-capture`, Application Support `HermesCapture/outbox.json` | Existing configuration, secret lookup, outbox, cancellation and retry continuity. Private local signing configuration was copied unchanged into the new source location with mode 600. |
| `com.jose.hermes.mobile_capture`, `source.app=HermesCapture`, `HermesCapture/<surface>/<version>`, `X-Hermes-*` | Existing payload, client and HTTP contract. WatchConnectivity keys/commands, route values and persisted JSON keys remain unchanged. |
| `hermes-watch:`, `hermes-watch-v3`, `Hermes-Mobile-Expense/1`, `HermesMobileCaptureStaging/0.1` | Existing financial reconciliation/internal references and backend protocol identity. No duplicate-prone ledger/operation migration or service change was made. |
| `HERMES_HOME`, `.hermes`, existing `/etc/hermes`/service-state paths and `hermes-mobile-capture.service.example` | Existing external Hermes Agent/backend deployment compatibility. The example's visible service description is updated; deployment paths, account, state and live service are unchanged. |
| `HERMES_VERIFICATION_OUTPUT`, `HERMES_SIMULATOR_DESTINATION`, `HERMES_DISABLE_PHONE_FALLBACK`, `HERMES_SIMULATE_OFFLINE`, old `--hermes-*` synthetic flags | Optional legacy aliases for existing local verification. New preferred names use `PERSONAL_WATCH_*` / `--personal-watch-*`. Simulator-only interception and Release exclusions remain enforced. |
| `feat/hermes-personal-mvp`, older dated reports/manifests/screenshots and the old ignored generated project | Preserve the existing review and evidence. Renaming the head branch would close its PR under GitHub's [documented behavior](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-branches-in-your-repository/renaming-a-branch). Historical names are explicitly labeled. |
| Existing local checkout folder and saved Codex project path/label | The coordinator has no supported native mapping/label setter. The folder was deliberately not moved and no private app database/IPC was edited. |

These names are compatibility/history, not active app branding. No wire, Keychain, app-group, signing or push registration was silently replaced.

## Verification

| Check | Result |
| --- | --- |
| Runtime | Effective GPT-6.1 Sol, Ultra; `on-request`, `auto_review`, `workspace-write` verified from this execution's current runtime record. |
| Backend synthetic tests | 104 passed. Only backend branding prose/comments/example description changed. |
| Shared Swift package | 58 passed under the renamed package. |
| Xcode generation/listing | Renamed project resolves both product schemes and all five app/test targets. |
| Unsigned Release builds | iOS with embedded Watch and standalone watchOS passed. No signing, provisioning or physical-device installation was run. |
| iOS simulator core/UI | 58 core + 8 UI tests passed, zero failures, under the renamed scheme/test host. |
| Built metadata comparison | Both app display names are Personal Watch App; bundle/companion IDs and 0.3.4 (7) remain unchanged. All action identifiers, fully qualified/mangled type names, provider identity, authentication/discovery/open behavior and three advertised shortcut phrases match the prior build. |
| Release safety/privacy | Both products exclude synthetic host/secret and retain privacy manifests with tracking disabled. |
| Independent review | No actionable findings in compatibility literals, serialized values, project/package/test-host wiring or synthetic-mode boundaries. |
| Local Markdown links | All tracked relative Markdown links resolve. |

Private logs, `.xcresult`, generated projects and local signing configuration remain ignored. [RESULTS.json](RESULTS.json) records sanitized results and log hashes; [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json) pins implementation files. No tests were added solely to match wording.

Public documentation and the PR use the source task's short label; its private planning URL is omitted. Existing Git history is preserved.

## Device and live readiness

This renamed candidate has **not** been installed on the phone or Watch. The historical phone installation/launch and unresolved Watch installation remain exactly as recorded in [the device setup history](../../XCODE_DEVICE_SETUP.md). No unlock, administrator-log or installation request was issued during the rename. Future private resume commands now point to the renamed project/products and explicitly require a rebuild before installation; old signed products are retained as historical artifacts.

No backend rollout, domain activation, live capture, financial canary, reminder creation or credential transfer occurred. The earlier observed backend health enabled only Mercado; that observation was not refreshed during branding work. Reminder alert delivery and broader live use remain unverified. This PR does not change that readiness or adopt a new AI/backend framework.
