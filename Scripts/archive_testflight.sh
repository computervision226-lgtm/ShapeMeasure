#!/usr/bin/env bash
# Create a signed archive on your Mac; upload it using Xcode Organizer.
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }
usage() {
  printf '%s\n' \
    'Usage: bash Scripts/archive_testflight.sh TEAM_ID BUNDLE_ID [BUILD_NUMBER]' \
    'Use your paid developer Team ID, your registered bundle ID, and a new build number.' \
    'Sign in to your Apple Account in Xcode before running this command.'
}

if [[ $# -lt 2 || $# -gt 3 ]]; then usage; exit 2; fi
team_id="$1"
bundle_id="$2"
build_number="${3:-1}"
[[ "$team_id" =~ ^[A-Z0-9]{10}$ ]] || fail 'Team ID must be your 10-character Apple Developer Team ID.'
[[ "$bundle_id" =~ ^[A-Za-z0-9][A-Za-z0-9-]*(\.[A-Za-z0-9][A-Za-z0-9-]*)+$ ]] || fail 'Use a reverse-domain bundle ID, such as com.yourcompany.shapemeasure with your own domain/name.'
case "$bundle_id" in
  com.example.*|com.yourcompany.*) fail 'Replace the example bundle ID with your own registered, unique bundle ID.' ;;
esac
[[ "$build_number" =~ ^[1-9][0-9]*$ && ${#build_number} -le 9 ]] || fail 'Build number must be a positive integer of at most 9 digits.'
[[ "$(uname -s)" == Darwin ]] || fail 'This archive must be built on a Mac with full Xcode installed.'
command -v xcodebuild >/dev/null 2>&1 || fail 'Install Xcode, open it once, and install the iOS platform support.'
if ! xcode_version="$(xcodebuild -version 2>/dev/null)"; then
  fail 'Open Xcode and choose it under Settings > Locations > Command Line Tools; finish first-launch setup.'
fi
xcode_major="$(printf '%s\n' "$xcode_version" | awk '/^Xcode / {split($2, v, "."); print v[1]; exit}')"
[[ "$xcode_major" =~ ^[0-9]+$ ]] || fail 'Could not determine the selected Xcode version.'
(( xcode_major >= 26 )) || fail 'Current TestFlight uploads require Xcode 26 or later. Install a compatible non-beta release.'
if ! sdk_version="$(xcrun --sdk iphoneos --show-sdk-version 2>/dev/null)"; then
  fail 'Install the iOS platform support in Xcode Settings, then try again.'
fi
sdk_major="${sdk_version%%.*}"
[[ "$sdk_major" =~ ^[0-9]+$ ]] || fail 'Could not determine the installed iOS SDK version.'
(( sdk_major >= 26 )) || fail 'Current TestFlight uploads require the iOS 26 SDK or later.'

project_root="$(cd "$(dirname "$0")/.." && pwd)"
archive_path="$project_root/Builds/ShapeMeasure-$build_number.xcarchive"
[[ ! -e "$archive_path" ]] || fail "Archive already exists: $archive_path. Choose a new build number."
mkdir -p "$project_root/Builds"
printf 'Archiving ShapeMeasure build %s with Xcode %s and iOS SDK %s.\n' "$build_number" "$xcode_major" "$sdk_version"
xcodebuild \
  -project "$project_root/ShapeMeasure.xcodeproj" \
  -scheme ShapeMeasure \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "$archive_path" \
  -allowProvisioningUpdates \
  DEVELOPMENT_TEAM="$team_id" \
  PRODUCT_BUNDLE_IDENTIFIER="$bundle_id" \
  CURRENT_PROJECT_VERSION="$build_number" \
  CODE_SIGN_STYLE=Automatic \
  SKIP_INSTALL=NO \
  archive

printf '\nSigned archive created: %s\n' "$archive_path"
printf '%s\n' 'In Xcode Organizer, validate the archive, then choose Distribute App > App Store Connect > Upload.'
printf '%s\n' 'The archive has not been uploaded. Follow TESTFLIGHT.md to add it to a testing group.'
open "$archive_path" || printf '%s\n' 'Open the archive above with Xcode to continue.'
