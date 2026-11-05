#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Synthetic tests only. No running backend, .env, or live-domain flags are used.
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode-beta.app/Contents/Developer}"
# Keep the old variable names as optional aliases for existing local automation.
task_output="${PERSONAL_WATCH_VERIFICATION_OUTPUT:-${HERMES_VERIFICATION_OUTPUT:-.private/verification/reproduction}}"
task_destination="${PERSONAL_WATCH_SIMULATOR_DESTINATION:-${HERMES_SIMULATOR_DESTINATION:-platform=iOS Simulator,name=iPhone 17 Pro,OS=27.0}}"
mkdir -p "$task_output"
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v > "$task_output/backend.log" 2>&1
swift test --package-path apple/PersonalWatchApp/Packages/PersonalWatchCore > "$task_output/swift.log" 2>&1
xcodegen generate --spec apple/PersonalWatchApp/project.yml
xcodebuild -project apple/PersonalWatchApp/PersonalWatchApp.xcodeproj \
  -scheme PersonalWatchAppiOS -configuration Release -destination 'generic/platform=iOS' \
  -derivedDataPath .private/ReleaseDerivedData CODE_SIGNING_ALLOWED=NO build \
  > "$task_output/ios-build.log" 2>&1
xcodebuild -project apple/PersonalWatchApp/PersonalWatchApp.xcodeproj \
  -scheme PersonalWatchAppWatch -configuration Release -destination 'generic/platform=watchOS' \
  -derivedDataPath .private/WatchDerivedData CODE_SIGNING_ALLOWED=NO build \
  > "$task_output/watch-build.log" 2>&1
# Use a new output directory each time; Xcode does not overwrite result bundles.
xcodebuild -project apple/PersonalWatchApp/PersonalWatchApp.xcodeproj \
  -scheme PersonalWatchAppiOS -destination "$task_destination" \
  -derivedDataPath .private/DerivedData -resultBundlePath "$task_output/ios.xcresult" \
  -parallel-testing-enabled NO -test-timeouts-enabled YES \
  -default-test-execution-time-allowance 60 CODE_SIGNING_ALLOWED=NO test \
  > "$task_output/ios-tests.log" 2>&1
echo "Synthetic verification passed. Local logs: $task_output"
