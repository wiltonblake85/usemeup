#!/usr/bin/env bash
# Build a UseMeUp.app anyone can open: Developer ID, hardened runtime,
# notarized, stapled, in a signed and notarized DMG.
#
#   ./macapp/release.sh
#
# Output: macapp/build/UseMeUp-<version>.dmg
#
# Modelled on Transom's release.sh (~/Developer/Marqee), and uses the same
# team and the same notarytool keychain profile. Nothing is published: this
# script ends at a DMG on disk.
#
# Apple silicon only. The bundled server is built by PyInstaller from the
# Homebrew python3.12, which is arm64; a universal build would need a
# universal2 Python and is not worth it until someone on Intel asks.
set -euo pipefail

TEAM_ID="4G2DZU69L8"
NOTARY_PROFILE="${USEMEUP_NOTARY_PROFILE:-transom-notary}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MACAPP="$REPO/macapp"
BUILD="$MACAPP/build"
APP="$BUILD/UseMeUp.app"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)"
DMG="$BUILD/UseMeUp-$VERSION.dmg"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31mrelease: %s\033[0m\n' "$*" >&2; exit 1; }

# Resolved by SHA-1 hash, not name: this keychain holds two certificates with
# the same name, and a name selector makes codesign fail as ambiguous.
if [ -z "${USEMEUP_SIGN_ID:-}" ]; then
  ID_LINE="$(security find-identity -v -p codesigning | grep "Developer ID Application" | grep "($TEAM_ID)" | head -1)"
  [ -n "$ID_LINE" ] || die "no Developer ID Application identity for team $TEAM_ID in the keychain"
  USEMEUP_SIGN_ID="$(printf '%s\n' "$ID_LINE" | awk '{print $2}')"
fi
xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 \
  || die "notarytool profile '$NOTARY_PROFILE' missing; see Transom's release.sh for the one-time setup"

say "1/6  building (not installed)"
"$MACAPP/build.sh" --no-install

say "2/6  signing every binary inside, innermost first"
# Notarization rejects a bundle where any Mach-O is unsigned, ad-hoc, missing
# the hardened runtime, or missing a secure timestamp. PyInstaller drops ~60
# of them (the Python framework, .so extension modules, dylibs). codesign
# --deep does not reach files it does not recognise as bundle code, so each
# is found and signed explicitly, deepest path first.
sign() { codesign --force --timestamp --options runtime --sign "$USEMEUP_SIGN_ID" "$@"; }
n=0
while IFS= read -r f; do
  if file -b "$f" | grep -q "Mach-O"; then sign "$f"; n=$((n+1)); fi
done < <(find "$APP/Contents/Resources" -type f | awk '{print length, $0}' | sort -rn | cut -d' ' -f2-)
echo "  $n nested binaries signed"
[ -d "$APP/Contents/Resources/server/_internal/Python.framework" ] \
  && sign "$APP/Contents/Resources/server/_internal/Python.framework"
sign "$APP"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -2

say "3/6  notarizing the app"
ZIP="$BUILD/UseMeUp-notarize.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait \
  | tee "$BUILD/notary-app.log"
grep -q "status: Accepted" "$BUILD/notary-app.log" \
  || die "app notarization was not accepted: xcrun notarytool log <id> --keychain-profile $NOTARY_PROFILE"
rm -f "$ZIP"

say "4/6  stapling and checking with Gatekeeper"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute --verbose=2 "$APP" 2>&1 | tail -2

say "5/6  building the DMG"
STAGE="$BUILD/dmg-stage"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/UseMeUp.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Install UseMeUp" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
codesign --force --timestamp --sign "$USEMEUP_SIGN_ID" "$DMG"

say "6/6  notarizing and stapling the DMG"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait \
  | tee "$BUILD/notary-dmg.log"
grep -q "status: Accepted" "$BUILD/notary-dmg.log" || die "DMG notarization was not accepted"
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG" 2>&1 | tail -2

printf '\nrelease: %s  (%s)\n' "$DMG" "$(du -h "$DMG" | cut -f1)"
