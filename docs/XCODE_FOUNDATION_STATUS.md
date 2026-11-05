# Xcode foundation status

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Status:** HermesCore verified on Jose's Mac with the Xcode toolchain; iOS/watchOS app targets still need generation and device/simulator builds.

## Added

```text
apple/HermesCapture/
├── project.yml
├── Apps/
│   ├── iOS/
│   ├── Watch/
│   └── Shared/
└── Packages/HermesCore/
```

## HermesCore currently includes

- `CapturePayloadV1` contract matching `mobile_capture.v1`.
- Canonical route/domain enums:
  - `megan.expense_capture`
  - `aura.reminder_capture`
  - `aura.grocery_capture`
  - `aura.home_action`
  - `aura.general_life_capture`
  - `argos.general_capture`
- HMAC V2 signer:
  - `X-Webhook-Timestamp`
  - `X-Webhook-Signature-V2 = HMAC_SHA256(secret, "<timestamp>.<raw_body>")`
- `WebhookClient` that signs and sends exact encoded JSON bytes.
- `FileOutboxStore` actor for local idempotent queue primitives.
- `QuickActionKind` + `CaptureFactory` for Watch-first actions.
- `CaptureResponseV1` decoding for BFF response JSON.

## XcodeGen

Generate project on macOS:

```bash
cd apple/HermesCapture
brew install xcodegen # if needed
xcodegen generate
open HermesCapture.xcodeproj
```

## SwiftPM tests on macOS

```bash
cd apple/HermesCapture/Packages/HermesCore
xcrun swift test
```

Expected XCTest coverage. On macOS, run SwiftPM with the Xcode-selected toolchain (`xcrun swift test`). Standalone Swift.org/Homebrew toolchains may omit Apple's XCTest/Testing modules and are not the supported Apple-app validation path:

- payload JSON contract keys;
- HMAC V2 known vector;
- required webhook headers;
- dry-run quick-action payload factory.

### Verified result — Jose's Mac

Verified on 2026-07-13 using:

```text
XcodeDefault toolchain
Apple Swift 6.3.3
Target: arm64-apple-macosx26.0
```

Result:

```text
Build complete! (24.80s)
Executed 4 tests, with 0 failures (0 unexpected)
```

The trailing Swift Testing line reporting `0 tests in 0 suites` is a separate runner; the four XCTest cases above it are the authoritative HermesCore result.

## Additional Linux verification

The package was also exercised in Swift 5.10/6.0 Linux containers during toolchain troubleshooting. Additional verification includes:

- Python backend tests still pass.
- Python `py_compile` passes for backend files.
- Static repository checks confirm Apple scaffold files exist and no obvious sensitive local files are staged.

## Next implementation task

On Mac/Xcode:

1. Generate Xcode project.
2. Build iOS target.
3. Build watchOS target.
4. Implement real dictation flow in `WatchContentView`:
   - present system text input;
   - create `CapturePayloadV1`;
   - enqueue before network;
   - sign and POST to BFF;
   - show `display_message`.
