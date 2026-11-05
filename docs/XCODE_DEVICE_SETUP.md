# Personal Watch App — physical-device setup record

> **Historical device evidence · October 25, 2026.** The product was renamed Personal Watch App on October 28. The signed/installed candidate described below used the former Hermes display name and original project/scheme names; those are preserved as evidence of the actual artifacts. The renamed build has not been installed, and Watch installation remains unresolved. No new device action is required by the rename while Jose is away from the Mac.

Current source setup uses `apple/PersonalWatchApp/PersonalWatchApp.xcodeproj`, the `PersonalWatchAppiOS` and `PersonalWatchAppWatch` schemes, and the `PersonalWatchCore` package. The unchanged bundle IDs and signing identity preserve compatibility; current setup commands are in [the Apple app README](../apple/PersonalWatchApp/README.md).

**October 29 follow-up:** a fresh phone Console reproduction identified `MIInstallerErrorDomain 111`: the free-provisioned Watch bundle is rejected for the companion Watch-app installation source. The canonical renamed project, Watch scheme and physical destination are now visibly selected in Xcode. Xcode built and signed the renamed Watch product with the original certificate, then failed to connect/install with `CoreDevice4000/RemotePairing1035`; Device Hub pairing/trust is in progress. See [the current diagnostic record](verification/2026-10-29/INSTALL_DIAGNOSTICS.md). The earlier attached-device log collector ignored its predicate, so its raw private archive is broader than intended; that helper is disabled.

Jose requested Xcode setup so that he only needs to test the app on his Watch. This record continues PR #1 and the earlier candidate; it does not authorize merging or deploying the backend.

## Configured locally

The generated project is `apple/HermesCapture/HermesCapture.xcodeproj`. Both product targets reference `Config/Signing.xcconfig`, which optionally includes the ignored `Signing.xcconfig.local` in that directory. The local file supplies the existing personal development Team and the standard `Apple Development` identity selector. Automatic signing remains enabled, the bundle IDs and companion relationship are unchanged, and regenerating the Xcode project preserves this setup.

The Team was resolved from the recovered project and independently matched to the current Apple Development certificate. There is no Team choice left for Jose to make. The existing certificate is valid through August 19, 2027. No new identity, certificate or device registration was created.

## Actual device/build state

| Item | Verified state |
| --- | --- |
| Toolchain | Installed Xcode 27.0 (27A5237l), command-scoped `DEVELOPER_DIR`; global selection unchanged. |
| iPhone | Existing paired physical phone, iOS 27.2; Developer Mode enabled; available as an iOS build destination over the local network. |
| Apple Watch | Existing paired physical Watch, watchOS 27.0; Developer Mode enabled; available as a watchOS build destination. |
| Schemes | `HermesCaptureiOS` builds the companion with embedded Watch; `HermesCaptureWatch` targets the physical Watch. Both generated launch actions point to the corresponding app. |
| Local configuration check | Both schemes inherit the resolved Team and automatic signing, with no manual provisioning-profile override. A certificate fingerprint pin was rejected by Xcode under Automatic signing; restoring its standard `Apple Development` selector resolved the conflict. |
| Signed build | **Passed**, iOS with embedded Watch, from source commit `be43c532332a667ed6b20e44dce2ac8bac181616`. No implementation source changed during this resumed setup. |
| Signature/profile verification | Both bundles pass strict/deep codesign verification, reuse the original existing certificate, match the unchanged Team and bundle IDs, and contain their corresponding paired device in the renewed profile. Both contain the privacy manifest with tracking disabled. |
| Profile refresh | **Resolved** after Jose signed into the existing account. Automatic provisioning renewed profiles using the original certificate; no new identity/certificate was created. Both profiles expire November 1, 2026, at approximately 15:05 America/Bogota. |
| iPhone install | **Succeeded** as an in-place update. CoreDevice readback confirms Hermes **0.3.4 (7)**. No uninstall or app-data deletion was performed. |
| iPhone launch | Initially denied by iOS with a signature/entitlement/developer-trust error. After Jose confirmed trusting the existing developer profile, normal launch succeeded and a running Hermes process was read back. No capture/retry/confirmation action was invoked. |
| Watch direct install | Blocked by its Bluetooth tunnel. Earlier attempts returned CoreDevice 4000 / RemotePairing 1035, “The device rejected the Bluetooth connection attempt.” After destination refresh, a successful normal pairing-refresh command, and confirmation that both devices were unlocked nearby, the final attempt returned CoreDevice 4 / RemotePairing 1007: Bluetooth invalidated before tunnel creation, with an L2CAP open-channel failure. No device was unpaired or erased. |
| Watch companion install | Jose reports tapping Install starts an attempt but ends with the Install button appearing again. Installation/launch is therefore **not verified**. The phone/Watch installer rejection reason is unavailable; the Mac's separate tunnel error does not establish why the companion attempt failed. |
| Additional bundle audit | Independent read-only audit found no concrete mismatch in embedded Watch location, companion IDs, version/build, device family, architecture/minimum OS, profile-allowed signed entitlements, nested dylib signatures or dependencies. No source/signing change is justified by this evidence. |
| USB follow-up | After Jose connected the phone, CoreDevice confirms wired/paired/connected with DDI services available, and IOKit detects the iPhone. The older `SPUSBDataType` query is unsupported on this macOS and its empty result was invalid; the available datatype is `SPUSBHostDataType`. A new Watch install still failed before transfer with CoreDevice 4000 / RemotePairing 1035. |
| Xcode GUI | Jose confirmed the Mac is unlocked. Native Xcode/Finder UI access still timed out; opening the project and selecting the physical Watch in the visible GUI remain **unverified**. No shell UI automation or private UI-state editing was used. |

Personal account, Team, certificate/profile and device identifiers, endpoint metadata, raw logs, and resume commands remain in ignored local storage. Public evidence intentionally omits those identifiers.

The sanitized [setup results](verification/2026-10-25/SETUP_RESULTS.json) record these checks separately from the original implementation test run.

## Recorded remaining Watch check

Apple login, profile renewal, iPhone installation and iPhone developer trust are resolved. They do not need repeating.

The installed iPhone app includes the signed Watch app. Jose tried Apple's [companion-app installation path](https://support.apple.com/en-ie/109023); the attempt returned to Install without completing. Repeating that action alone is not the next diagnostic step.

On October 25 the paired phone's USB connection was verified; that did not resolve the Watch tunnel failure. At that point the companion installer rejection was unknown, and attached-device collection required local administrator authentication. That historical diagnostic gap was resolved by the fresh October 29 Console reproduction: `MIInstallerErrorDomain 111` rejects the free-provisioned app for the companion installation source. The current remaining step is Watch developer pairing/trust, followed by Xcode Run.

An ignored private helper was prepared at `.private/setup/collect-hermes-installer-error.sh` with an intended 30-minute/5 MB installer-message filter. Jose ran it on October 29. Apple's actual collection output warned that `--predicate` is ignored for attached devices: the raw private archive is therefore broader than intended. Only the subsequent `log show` display was filtered; it returned zero entries. The archive was not broadly inspected, remains in an owner-only private directory, and the collection helper is now disabled. No administrator password was entered in chat or extracted.

The empty display did not establish an installation cause. The original October 25 failure was outside the October 29 collection's recent-history window, and the process/message filter can omit context. A fresh companion attempt was then observed in the paired phone's Console view, filtered to the unchanged app bundle ID. It exposed the source-policy rejection, and streaming was paused after the attempt. No attached-device archive collection should be repeated under the earlier app-only description.

The recorded alternative was to inspect the existing phone in Console, filter installer messages for its legacy bundle ID and reproduce once. The October 25 Xcode run selection was **HermesCaptureWatch → the paired physical Watch**; its renamed source equivalent is **PersonalWatchAppWatch → the paired physical Watch** in **Window → Devices and Simulators**. Apple's [device-run instructions](https://help.apple.com/xcode/mac/current/en.lproj/dev5a825a1ca.html) cover the paired-phone connection and Watch destination.

The additional audit followed Apple's [installation troubleshooting guidance](https://developer.apple.com/library/archive/technotes/tn2319/) by inspecting the Watch app embedded inside the installed phone build. Its Debug dylib layout is [documented by Apple](https://developer.apple.com/documentation/xcode/understanding-build-product-layout-changes). The October 25 bounded Mac developer-log query found the known tunnel errors but no companion installer rejection; that Mac query did not collect device diagnostics. The separate October 29 attached-device archive has the broader scope disclosed above.

The private `.private/setup/RESUME.json` records the resolved destinations, build/install commands and project path for resumption. Raw device/signing logs remain private. The development profiles require renewal after their actual expiry.

## App installation versus usable live actions

A normal-TLS, unauthenticated read of the existing configured backend returned HTTP 200 with only `aura.grocery_capture` enabled. The private endpoint was obtained from the installed app's preferences; no captures, Keychain secret or financial records were read.

Therefore installing this candidate does not make all three live actions ready. The exact external Aura adapter is not available in the reviewed source, reminder alert delivery is unverified, and the deployed backend has not enabled reminders or expenses. The [separate rollout proposal](MVP_BACKEND_ROLLOUT_PROPOSAL.md) specifies the smallest missing source handoff and deployment sequence. No production setting was changed.
