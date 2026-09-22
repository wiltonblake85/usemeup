#!/usr/bin/env bash
# Build UseMeUp.app: a SwiftUI menu bar front end with the Python server
# bundled inside it.
#
#   ./macapp/build.sh              build, install to /Applications, restart
#   ./macapp/build.sh --swift      skip PyInstaller, recompile Swift only
#   ./macapp/build.sh --no-install leave it in build/ and touch nothing else
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
INSTALL=1
for a in "$@"; do
  [ "$a" = "--swift" ] && SWIFT_ONLY=1
  [ "$a" = "--no-install" ] && INSTALL=0
done

INSTALLED="/Applications/UseMeUp.app"
AGENT_LABEL="com.wiltonblake.usemeup.menubar"
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1. server

if [ "$SWIFT_ONLY" -eq 0 ]; then
  say "1/5  bundling the Python server"

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
  for m in agent burn cli config coverage daily history panel parse_usage pricing \
           rate_limits server status statusline store verify; do
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

say "2/5  assembling the bundle"
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

say "3/5  compiling Swift"
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 \
  -framework AppKit -framework ServiceManagement \
  "$MACAPP/Sources"/*.swift \
  -o "$APP/Contents/MacOS/UseMeUp"

# ---------------------------------------------------------------- 4. sign

say "4/5  signing"
# Ad-hoc. Enough to run here, NOT enough for "Open at login" (SMAppService
# wants a real signature) and not enough to hand to anyone else. Set
# USEMEUP_SIGN_ID to a Developer ID Application identity when that day comes.
SIGN_ID="${USEMEUP_SIGN_ID:--}"
codesign --force --deep --options runtime --sign "$SIGN_ID" "$APP" 2>/dev/null \
  || codesign --force --deep --sign "$SIGN_ID" "$APP"

# ---------------------------------------------------------------- 5. install

# The login agent points at /Applications, not at build/, because step 2 deletes
# build/ on every run. Installing here keeps the thing that runs at login and the
# thing that was just compiled from drifting apart: a build that skipped this
# step would leave the menu bar silently running last week's binary.
if [ "$INSTALL" -eq 1 ]; then
  say "5/5  installing and restarting"

  osascript -e 'quit app "UseMeUp"' 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/$AGENT_LABEL" 2>/dev/null || true
  sleep 1

  rm -rf "$INSTALLED"
  cp -R "$APP" "$INSTALLED"
  echo "  installed to $INSTALLED"

  if [ ! -f "$AGENT_PLIST" ]; then
    cat > "$AGENT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AGENT_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$INSTALLED/Contents/MacOS/UseMeUp</string>
  </array>
  <!-- Start at login. NOT KeepAlive: quitting from the panel has to mean quit,
       not "relaunch me in two seconds". -->
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$HOME/.usemeup/menubar.log</string>
  <key>StandardErrorPath</key><string>$HOME/.usemeup/menubar.err</string>
</dict>
</plist>
PLIST
    echo "  wrote $AGENT_PLIST"
  fi

  launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST"
  echo "  started via launchd"
else
  say "5/5  install skipped (--no-install)"
fi

echo
echo "built:     $APP"
du -sh "$APP" | sed 's/^/size:      /'
[ "$INSTALL" -eq 1 ] && echo "installed: $INSTALLED"
echo
echo "restart:   launchctl kickstart -k gui/\$(id -u)/$AGENT_LABEL"
echo "stop:      osascript -e 'quit app \"UseMeUp\"'"
echo "uninstall: launchctl bootout gui/\$(id -u)/$AGENT_LABEL; rm -f $AGENT_PLIST; rm -rf $INSTALLED"
