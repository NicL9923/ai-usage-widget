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
# Unbundled executables resolve resources next to themselves.
rm -rf "$repo_root/build/icons" && cp -R "$repo_root/plasmoid/contents/icons" "$repo_root/build/icons"
xcrun swiftc -parse-as-library -D TESTING -target "$(uname -m)-apple-macosx14.0" \
  -module-cache-path "$repo_root/build/swift-module-cache" \
  "$repo_root"/macos/*.swift "$repo_root/tests/macos/ModelTests.swift" -o "$repo_root/build/test-macos-model"
"$repo_root/build/test-macos-model"
if [[ "${1:-}" == --render ]]; then
  xcrun swiftc -parse-as-library -D TESTING -target "$(uname -m)-apple-macosx14.0" \
    -module-cache-path "$repo_root/build/swift-module-cache" \
    "$repo_root"/macos/*.swift "$repo_root/tests/macos/Render.swift" -o "$repo_root/build/render-macos"
  "$repo_root/build/render-macos" "$repo_root/build"
fi
