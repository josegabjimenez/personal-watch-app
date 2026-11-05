# Personal Watch App — Apple applications

This folder contains the native iPhone and Apple Watch applications for Personal Watch App.

## Shape

```text
PersonalWatchApp/
├── project.yml                         # XcodeGen project definition
├── Packages/PersonalWatchCore/          # Shared Swift package
├── Apps/iOS/                           # iPhone setup, capture review and history
└── Apps/Watch/                         # Mercado, Recordatorio and Gasto
```

## Generate Xcode project on macOS

```bash
cd apple/PersonalWatchApp
brew install xcodegen # if needed
xcodegen generate
open PersonalWatchApp.xcodeproj
```

After every `project.yml` change, close/reopen Xcode after regenerating the project:

```bash
xcodegen generate
open PersonalWatchApp.xcodeproj
```

The app and test targets use `GENERATE_INFOPLIST_FILE: YES`; do not add a source-controlled plist unless a later entitlement or privacy requirement needs one explicitly.

## Build target direction

- iOS app: configuration, endpoint, Keychain bootstrap, history/debug, WatchConnectivity fallback.
- watchOS app: fast capture buttons, dictation, outbox, direct HTTPS to BFF.
- PersonalWatchCore: shared payload contract, HMAC V2 signer, webhook client, outbox primitives.

The current schemes are `PersonalWatchAppiOS` and `PersonalWatchAppWatch`. Tests use `PersonalWatchAppUITests`, `PersonalWatchCoreTests` and the `PersonalWatchCoreTestHost` app. The display name is **Personal Watch App** on both platforms.

## Compatibility identifiers

The installed bundle IDs remain `dev.josegabjimenez.HermesCapture` and `dev.josegabjimenez.HermesCapture.watchkitapp`. The app Swift module identities `HermesCaptureiOS` and `HermesCaptureWatch`, existing App Intent identities, storage/Keychain keys and wire client values are preserved for installed data, saved Shortcuts and backend compatibility. These legacy names are not product branding. The [MVP contract](../../docs/PERSONAL_WATCH_APP_MVP.md) describes the unchanged three-action scope.

Both product targets include `Config/Signing.xcconfig`. Its optional ignored `Signing.xcconfig.local` supplies the existing personal Team and standard Apple Development selector; keep private identifiers out of source control.

## Current endpoint contract

Development builds should target a tailnet-only HTTPS BFF endpoint:

```text
https://<TAILSCALE_DNS_NAME>:8650/webhooks/mobile-capture-v1
```

Never commit the real route secret. Store it in Keychain and sync to Watch via a reviewed bootstrap flow.

## SwiftPM tests on macOS

Use the installed Apple toolchain with a command-scoped developer directory so XCTest is available:

```bash
cd apple/PersonalWatchApp/Packages/PersonalWatchCore
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer xcrun --find swift
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer xcrun swift --version
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer xcrun swift test
```

If plain `swift test` reports `no such module 'XCTest'` or `no such module 'Testing'`, inspect `which -a swift`, `echo "$TOOLCHAINS"`, and `xcode-select -p`; a standalone Swift toolchain is being selected instead of Xcode. Do not add `swift-corelibs-xctest` to this Apple package as a workaround because its macOS build expects private Foundation build flags.

## Linux limitation

Building the Apple applications requires macOS and Xcode. The shared package supports SwiftPM tests on Linux, but those tests do not validate Apple app compilation or physical-device behavior. Current verification and the unresolved Watch installation are recorded in [the device setup record](../../docs/XCODE_DEVICE_SETUP.md).
