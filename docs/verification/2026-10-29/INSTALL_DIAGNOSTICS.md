# Watch installation diagnosis · 29 October 2026

A fresh, app-filtered view of the paired phone's Console identified the companion install failure. The Watch received the app transfer, then rejected it with **MIInstallerErrorDomain 111 / ApplicationVerificationFailed**. Its reason was that the bundle uses a free provisioning profile and apps validated by that profile are not allowed to install from this source. `ACXErrorDomain 17` and `IXUserPresentableErrorDomain 1` wrapped that rejection.

This identifies the source-policy failure behind the iPhone Watch app's Install-button loop. It is separate from the Mac's previously observed Bluetooth/developer-tunnel errors. It does not establish an app-code defect, an invalid certificate, or a requirement to buy a developer membership.

## Evidence and corrected collection boundary

Jose completed the earlier helper at 11:31 Bogotá. Its filtered display file was a valid empty array. The collector itself warned that `--predicate` is ignored when collecting from an attached device, so the raw archive was broader than intended; only the subsequent display was filtered. The original October 25 install was also outside that collection's recent-history window. Empty output was therefore inconclusive.

The raw archive remains private in an owner-only directory and was not broadly inspected. The old collection helper has been disabled. No repeat attached-device archive collection is proposed. Earlier app-only collection claims in the setup record are corrected.

Console became available through supported native UI. The existing phone was selected and the view filtered to the unchanged app bundle prefix before a fresh companion Install attempt. Seventeen matching messages included the transfer and exact rejection at **11:39:33 America/Bogota**. Streaming was paused afterward. Only the sanitized error summary is published here; no raw logs, account/Team/device identifiers, credentials or unrelated messages are included.

## Current route

Apple supports [Personal Team testing through Xcode](https://developer.apple.com/help/account/basics/about-your-developer-account) and [running on a physical Watch](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices). The next attempt uses the existing account, identity, Team and bundle IDs through that development route. It does not change to paid distribution or another signing identity.

The canonical renamed project was opened in Xcode at `apple/PersonalWatchApp/PersonalWatchApp.xcodeproj`, with **PersonalWatchAppWatch → the physical Watch** visibly selected. The previously visible external historical project was not used. A normal Xcode Run built the renamed Watch product successfully; its strict signature check passed, the unchanged legacy bundle ID and new display name were verified, and its actual signing certificate matches the original existing certificate. Its cached profile remains valid through November 1, 2026.

The Run then failed in `IDEInstallCoreDeviceWorker` before installation, with `CoreDeviceError 4000` wrapping `RemotePairingError 1035`: the Watch rejected the Bluetooth connection. Xcode's generic “Finished running” toolbar wording after dismissing the error is not launch proof. Device Hub's supported **Add → Pair Nearby Device** sheet remains waiting for the Watch's developer pairing/trust interaction; Jose reports the Mac/pairing entry is visible on the Watch. See Apple's [Device Hub pairing guidance](https://developer.apple.com/documentation/xcode/managing-your-simulated-and-physical-devices-in-device-hub).

Watch installation and launch remain unverified until that developer connection and an actual Xcode Run succeed. No unpair/erase, backend deployment, domain activation, live capture or financial/reminder action was performed. The branded source remains the verified October 28 candidate; this diagnostic follow-up changes documentation only.

[Sanitized results](INSTALL_RESULTS.json) · [Historical setup](../../XCODE_DEVICE_SETUP.md) · [Rename verification](../2026-10-28/REPORT.md)
