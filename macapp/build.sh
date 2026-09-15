#!/usr/bin/env bash
# Build UseMeUp.app: a SwiftUI menu bar front end with the Python server
# bundled inside it.
#
#   ./macapp/build.sh            build everything
#   ./macapp/build.sh --swift    skip PyInstaller, recompile Swift only
#
# No Xcode project on purpose. The app is five Swift files and an Info.plist;
# a .pbxproj would be the largest and least readable file in the repo.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MACAPP="$REPO/macapp"
BUILD="$MACAPP/build"
APP="$BUILD/UseMeUp.app"
VENV="${USEMEUP_BUILD_VENV:-/tmp/umu-build}"
PY312="${USEMEUP_PYTHON:-/opt/homebrew/bin/python3.12}"

SWIFT_ONLY=0
[ "${1:-}" = "--swift" ] && SWIFT_ONLY=1

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1. server

if [ "$SWIFT_ONLY" -eq 0 ]; then
  say "1/4  bundling the Python server"

  if [ ! -x "$VENV/bin/pyinstaller" ]; then
    echo "  creating build venv at $VENV"
    "$PY312" -m venv "$VENV"
    "$VENV/bin/pip" -q install --upgrade pip
    "$VENV/bin/pip" -q install pyinstaller
  fi
  "$VENV/bin/pip" -q install -e "$REPO"

  # Every import in cli.py is lazy and inside a function, so PyInstaller's
  # static scan finds almost nothing. Name the modules explicitly.
  HIDDEN=()
  for m in agent burn cli config coverage daily panel parse_usage pricing \
           rate_limits server store verify; do
    HIDDEN+=(--hidden-import "usemeup.$m")
  done

  rm -rf "$BUILD/pyi-dist" "$BUILD/pyi-work"
  "$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
    --name usemeup-server \
    --distpath "$BUILD/pyi-dist" --workpath "$BUILD/pyi-work" --specpath "$BUILD" \
    --paths "$REPO/src" \
    --collect-data usemeup \
    "${HIDDEN[@]}" \
    "$MACAPP/server_entry.py"

  test -x "$BUILD/pyi-dist/usemeup-server/usemeup-server" \
    || { echo "PyInstaller produced no executable"; exit 1; }
fi

# ---------------------------------------------------------------- 2. bundle

say "2/4  assembling the bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)"
VERSION="${VERSION:-0.1.0}"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>UseMeUp</string>
  <key>CFBundleDisplayName</key><string>UseMeUp</string>
  <key>CFBundleExecutable</key><string>UseMeUp</string>
  <key>CFBundleIdentifier</key><string>com.wiltonblake.usemeup.menubar</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <!-- Menu bar only: no Dock tile, no menu bar of its own. -->
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

if [ -d "$BUILD/pyi-dist/usemeup-server" ]; then
  cp -R "$BUILD/pyi-dist/usemeup-server" "$APP/Contents/Resources/server"
else
  echo "  (no bundled server in this build; the app will attach to a running one)"
fi

# ---------------------------------------------------------------- 3. swift

say "3/4  compiling Swift"
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 \
  -framework AppKit -framework ServiceManagement \
  "$MACAPP/Sources"/*.swift \
  -o "$APP/Contents/MacOS/UseMeUp"

# ---------------------------------------------------------------- 4. sign

say "4/4  signing"
# Ad-hoc. Enough to run here, NOT enough for "Open at login" (SMAppService
# wants a real signature) and not enough to hand to anyone else. Set
# USEMEUP_SIGN_ID to a Developer ID Application identity when that day comes.
SIGN_ID="${USEMEUP_SIGN_ID:--}"
codesign --force --deep --options runtime --sign "$SIGN_ID" "$APP" 2>/dev/null \
  || codesign --force --deep --sign "$SIGN_ID" "$APP"

echo
echo "built: $APP"
du -sh "$APP" | sed 's/^/size:  /'
echo
echo "run:   open '$APP'"
echo "stop:  osascript -e 'quit app \"UseMeUp\"'"
