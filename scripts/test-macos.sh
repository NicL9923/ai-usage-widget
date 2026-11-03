#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(hostname -s)" == "Nicolass-Mini" && "${AI_USAGE_BUILD_LOCKED:-}" != 1 ]]; then
  exec "$HOME/.agents/tools/mac-heavy-lock.sh" env AI_USAGE_BUILD_LOCKED=1 bash "$0" "$@"
fi
mkdir -p "$repo_root/build"
xcrun swiftc -module-cache-path "$repo_root/build/swift-module-cache" \
  "$repo_root/macos/Usage.swift" "$repo_root/tests/macos/main.swift" \
  -o "$repo_root/build/test-macos"
"$repo_root/build/test-macos"
xcrun swiftc -parse-as-library -D TESTING -target "$(uname -m)-apple-macosx13.0" \
  -module-cache-path "$repo_root/build/swift-module-cache" \
  "$repo_root/macos/Usage.swift" "$repo_root/macos/AIUsageApp.swift" \
  "$repo_root/tests/macos/ModelTests.swift" -o "$repo_root/build/test-macos-model"
"$repo_root/build/test-macos-model"
if [[ "${1:-}" == --render ]]; then
  xcrun swiftc -parse-as-library -D TESTING -target "$(uname -m)-apple-macosx13.0" \
    -module-cache-path "$repo_root/build/swift-module-cache" \
    "$repo_root/macos/Usage.swift" "$repo_root/macos/AIUsageApp.swift" \
    "$repo_root/tests/macos/Render.swift" -o "$repo_root/build/render-macos"
  "$repo_root/build/render-macos" "$repo_root/build"
fi
