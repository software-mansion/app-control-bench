#!/usr/bin/env bash
# Build + install the native Immich iOS app onto a simulator, pointed at the frozen
# local server. Run ON radoniusz. First build is ~15-20 min.
#
#   ./build-ios-app.sh [SIM_UDID]     # default: the currently-booted sim
#
# ── WHY THIS SCRIPT IS SHAPED THE WAY IT IS (hard-won; do not "simplify" blindly) ──
# Immich's App Store / TestFlight builds are device-only IPAs that can't run on a
# simulator, so the sim surface MUST be built from source (~/dev/immich/mobile,
# Flutter). Getting a *debug simulator* build to compile took solving 3 real problems
# that Immich CI never hits (its iOS job is only `flutter build ios --config-only`,
# i.e. it never actually compiles the app):
#
#   1. TOOLCHAIN = Xcode 26.2 (Swift 6.2.3). This is the version Immich CI pins
#      (.github/workflows/build-mobile.yml). Xcode 26.5 fails the swift-syntax macro
#      plugin ("StructuredQueriesMacros produced malformed response"); Xcode 27.0
#      fails a swift-sharing compile. 26.2 is the sweet spot.
#         Install once:  xcodes install 26.2   (Apple-ID gated; ~15 GB)
#
#   2. TRANSLATION CODEGEN must run first. `context.t` (the i18n accessor) is generated
#      by Immich CI's `mise //mobile:codegen:translation`. Without it every widget using
#      `context.t.*` fails with "The getter 't' isn't defined for the type 'BuildContext'".
#      We run the underlying dart commands directly (mise not required).
#
#   3. BUILD WITH xcodebuild + `-destination`, NOT `flutter build ios --simulator`.
#      `flutter build ios --simulator` invokes `xcodebuild -sdk iphonesimulator`, which
#      forces EVERY target — including the host-only swift-syntax macro plugins — onto
#      the simulator SDK. That yields "Cannot load module 'SwiftBasicFormat' built with
#      SDK 'macosx' when using SDK 'iphonesimulator'". Driving xcodebuild with
#      `-destination 'platform=iOS Simulator,id=<udid>'` lets SPM build the macro plugins
#      for the host (macOS) while the app targets the simulator. So we use flutter only
#      for `--config-only` (to generate Flutter/Generated.xcconfig) then call xcodebuild.
#
# Output: build/ios/Debug-iphonesimulator/Immich-Debug.app  (bundle app.futo.immich.debug;
# a release build would be app.alextran.immich, but debug/simulator is what we need).
set -eo pipefail
export PATH="/opt/homebrew/bin:$HOME/.node/bin:$PATH"

MOBILE="$HOME/dev/immich/mobile"
SERVER_URL="http://localhost:2283"
UDID="${1:-$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)}"
[ -n "$UDID" ] || { echo "!! no booted simulator; boot one (or pass its UDID)"; exit 1; }
echo "==> target sim: $UDID"

# ── 1. locate Xcode 26.2 + flutter ────────────────────────────────────────────
XCODE=$(ls -d /Applications/Xcode-26.2*.app 2>/dev/null | head -1)
[ -n "$XCODE" ] || { echo "!! Xcode 26.2 not found. Install it:  xcodes install 26.2"; exit 1; }
export DEVELOPER_DIR="$XCODE/Contents/Developer"
echo "==> Xcode: $(xcodebuild -version | head -1)  ($DEVELOPER_DIR)"

FLUTTER="$(command -v flutter || true)"
for p in "$HOME/flutter/bin/flutter" "$HOME/development/flutter/bin/flutter"; do
  [ -z "$FLUTTER" ] && [ -x "$p" ] && FLUTTER="$p"
done
[ -n "$FLUTTER" ] || { echo "!! flutter not found (expected ~/flutter/bin/flutter)"; exit 1; }
DART="$(dirname "$FLUTTER")/dart"
echo "==> flutter: $($FLUTTER --version | head -1)"

cd "$MOBILE"
echo "==> flutter pub get"; "$FLUTTER" pub get

# ── 2. translation codegen (generates lib/generated/*.g.dart → defines context.t) ──
if [ ! -f lib/generated/translations.g.dart ]; then
  echo "==> translation codegen (easy_localization + generate_keys)"
  "$DART" run easy_localization:generate -S ../i18n
  "$DART" run bin/generate_keys.dart
fi
echo "==> translations present: $(ls lib/generated/translations.g.dart 2>/dev/null || echo MISSING)"

# ── 3. config-only (flutter) then compile with xcodebuild + -destination ──────
echo "==> flutter build ios --config-only (generates Generated.xcconfig, no compile)"
"$FLUTTER" build ios --simulator --debug --config-only --no-codesign

echo "==> xcodebuild (with -destination, so macros build for the host)  [~15 min first run]"
cd "$MOBILE/ios"
xcrun xcodebuild \
  -clonedSourcePackagesDirPath "$MOBILE/build/ios/SourcePackages" \
  -skipPackageUpdates -skipPackagePluginValidation -skipPackageSignatureValidation \
  -workspace Runner.xcworkspace -scheme Runner -configuration Debug \
  -destination "platform=iOS Simulator,id=$UDID" \
  BUILD_DIR="$MOBILE/build/ios" \
  CODE_SIGNING_ALLOWED=NO ONLY_ACTIVE_ARCH=YES \
  build

APP="$(find "$MOBILE/build/ios" -maxdepth 3 -iname 'Immich-Debug.app' -type d | head -1)"
[ -n "$APP" ] || { echo "!! build produced no Immich-Debug.app under build/ios"; exit 1; }
echo "==> built: $APP ($(du -sh "$APP" | cut -f1))"

# ── install + launch ──────────────────────────────────────────────────────────
xcrun simctl install "$UDID" "$APP"
BUNDLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Info.plist" 2>/dev/null || echo app.futo.immich.debug)"
xcrun simctl launch "$UDID" "$BUNDLE" || true

cat <<EOF

INSTALLED + LAUNCHED. bundle id = $BUNDLE
One-time login (session persists across relaunch, like Bluesky):
  1. Server Endpoint URL:  $SERVER_URL   (sim shares the host loopback)
  2. Log in:  admin@immich.local / admin1234

Wire Immich into the harness — edit runner/bench.py APPS (leave the rest of bench.py alone):
     "immich": {"bundle": "$BUNDLE", "open": None},
For benchmark runs, install into the GOLDEN simulator (tasks/setup/golden/make_golden.sh) —
runs clone it per run:  python3 runner/bench.py --all --apps immich
EOF
