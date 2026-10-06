#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v xcodebuild >/dev/null 2>&1; then
  echo "Run this check on a Mac with Xcode and an iOS SDK installed."
  exit 1
fi
xcodebuild -project "$project_root/ShapeMeasure.xcodeproj" -scheme ShapeMeasure \
  -configuration Debug -destination 'generic/platform=iOS' \
  -derivedDataPath "$project_root/.build-ios" CODE_SIGNING_ALLOWED=NO build
