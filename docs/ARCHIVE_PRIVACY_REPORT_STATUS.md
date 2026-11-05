# Archive privacy report status

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Version:** 0.3.1 (4)

**Date:** 2026-08-04 America/Bogota

**Status:** physical Release archive privacy gate passed with direct artifact verification.

## Organizer result

Jose generated the iOS archive with its embedded watchOS companion using the watchOS 27-compatible Xcode 27 beta toolchain and reported:

```text
Archive: succeeded
Organizer warnings: none
Tracking: false
Tracking domains: none
Collected data types: Other User Content, Purchase History, Device ID
Purposes: App Functionality
Used for tracking: no
```

The generated Privacy Report UI did not display the required-reason API entry, so the built `.xcarchive` was inspected directly instead of assuming that the source manifest had been copied.

## Built-artifact verification

Four bundled manifests were found:

```text
iOS application / PrivacyInfo.xcprivacy
watchOS application / PrivacyInfo.xcprivacy
iOS swift-crypto_Crypto.bundle / PrivacyInfo.xcprivacy
watchOS swift-crypto_Crypto.bundle / PrivacyInfo.xcprivacy
```

The application and Watch manifests both contained:

```text
NSPrivacyTracking = false
NSPrivacyTrackingDomains = []
NSPrivacyAccessedAPICategoryUserDefaults = CA92.1
```

Both Swift Crypto manifests contained:

```text
NSPrivacyTracking = false
NSPrivacyTrackingDomains = []
```

No unexpected tracking domains or data categories were reported.

## Decision

**PASS.** The absence of `CA92.1` from the visible Xcode 27 beta report was treated as a report-presentation limitation because the required declaration was verified in both built application artifacts. Source-only inspection was not accepted as sufficient evidence.

The private archive path, bundle metadata, Team ID, provisioning material, endpoint, HMAC secret, and device identifiers are intentionally omitted from this repository.

## Remaining distribution checks

- perform a fresh TestFlight install on paired devices;
- reconcile App Store Connect App Privacy answers with this manifest;
- repeat archive privacy verification after adding any SDK or capability;
- do not enable external writes solely because this privacy gate passed.
