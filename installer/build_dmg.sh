#!/usr/bin/env bash
#
# Build the macOS download:  dist/AI-Bridge-Mac.dmg
#
# The dmg holds one app — "Gemini Studio (Ableton)" — that the user drags into
# Applications, the way every Mac app arrives. The app carries its own Python
# (a relocatable build with tkinter and pip inside), so the user installs
# nothing else. On every launch the app first runs install.py quietly, which
# keeps the bridge inside Live's User Library current, then opens the Studio
# window.
#
# Runs on a macOS machine (in practice: the GitHub Actions runner). Not signed,
# not notarized — the README's "If your computer warns you" section exists for
# exactly that. Signing is added the day there is an Apple Developer ID.
#
# Like build.ps1, the app content comes from git HEAD: what you committed is
# what ships, and a dirty working copy cannot leak into the download.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(sed -n 's/^version *= *"\([^"]*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"
PYMINOR="3.14"
APP_NAME="Gemini Studio (Ableton)"
STAGE="$ROOT/dist/dmg-stage"
APP="$STAGE/$APP_NAME.app"
RES="$APP/Contents/Resources"

step() { printf '\n== %s\n' "$1"; }

step "AI Bridge $VERSION — macOS app + dmg"
rm -rf "$STAGE"
mkdir -p "$RES/app" "$APP/Contents/MacOS"

step "Staging the bridge (git archive of HEAD)"
if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
    echo "   WARNING: uncommitted changes are NOT included:"
    git -C "$ROOT" status --porcelain | head -5 | sed 's/^/   /'
fi
git -C "$ROOT" archive HEAD | tar -x -C "$RES/app"

step "Staging a private Python ($PYMINOR, relocatable, with tkinter)"
# python-build-standalone: CPython built to run from any folder — no /Library
# framework paths baked in, which is what lets it live inside an app bundle.
# "install_only" variants include tkinter and pip.
ARCH="$(uname -m)"
case "$ARCH" in
    arm64)  PBS_ARCH="aarch64-apple-darwin" ;;
    x86_64) PBS_ARCH="x86_64-apple-darwin" ;;
    *) echo "unsupported build machine: $ARCH"; exit 1 ;;
esac
AUTH=()
[ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")
URL="$(curl -fsSL "${AUTH[@]}" \
        https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest |
      python3 -c '
import json, sys
assets = json.load(sys.stdin)["assets"]
want = [a["browser_download_url"] for a in assets
        if "cpython-'"$PYMINOR"'." in a["name"]
        and "'"$PBS_ARCH"'" in a["name"]
        and a["name"].endswith("install_only.tar.gz")]
print(want[0] if want else "")')"
if [ -z "$URL" ]; then
    echo "no cpython-$PYMINOR install_only build for $PBS_ARCH in the latest python-build-standalone release"
    exit 1
fi
echo "   $URL"
curl -fsSL "$URL" -o /tmp/pbs-python.tar.gz
tar -xzf /tmp/pbs-python.tar.gz -C "$RES"     # unpacks to Resources/python/
PY="$RES/python/bin/python3"

step "Smoke test of the bundled Python"
"$PY" -c "import tkinter, sys; print('   python', sys.version.split()[0], ' tk', tkinter.TkVersion)"
"$PY" -m compileall -q "$RES/app/tools/gemini_studio.py" "$RES/app/install.py"

step "Adding numpy"
"$PY" -m pip install --quiet --disable-pip-version-check numpy
"$PY" -c "import numpy; print('   numpy', numpy.__version__)"

step "Launcher"
cat > "$APP/Contents/MacOS/GeminiStudio" <<'LAUNCH'
#!/bin/bash
# Keep the bridge inside Live current (quiet, harmless if Live is missing),
# then open the Studio window with the Python this app carries.
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
PY="$CONTENTS/Resources/python/bin/python3"
APPDIR="$CONTENTS/Resources/app"
"$PY" "$APPDIR/install.py" >/dev/null 2>&1 || true
cd "$APPDIR"
exec "$PY" "$APPDIR/tools/gemini_studio.py"
LAUNCH
chmod +x "$APP/Contents/MacOS/GeminiStudio"

step "Icon"
# .icns from the shipped high-resolution PNG; sips and iconutil are macOS stock.
ICONSET=/tmp/ai-bridge.iconset
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
    sips -z $s $s       "$ROOT/assets/ai-bridge-lit.png" --out "$ICONSET/icon_${s}x${s}.png"      >/dev/null
    sips -z $((s*2)) $((s*2)) "$ROOT/assets/ai-bridge-lit.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/ai-bridge.icns"

step "Info.plist"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>Gemini Studio</string>
    <key>CFBundleDisplayName</key>     <string>Gemini Studio (Ableton)</string>
    <key>CFBundleIdentifier</key>      <string>com.twistbyte.ai-bridge.gemini-studio</string>
    <key>CFBundleShortVersionString</key> <string>$VERSION</string>
    <key>CFBundleVersion</key>         <string>$VERSION</string>
    <key>CFBundleExecutable</key>      <string>GeminiStudio</string>
    <key>CFBundleIconFile</key>        <string>ai-bridge</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>NSHumanReadableCopyright</key> <string>Apache-2.0 — Twistbyte</string>
</dict>
</plist>
PLIST

step "dmg"
ln -shf /Applications "$STAGE/Applications"
rm -f "$ROOT/dist/AI-Bridge-Mac.dmg"
hdiutil create -volname "AI Bridge" -srcfolder "$STAGE" -ov -format UDZO \
    "$ROOT/dist/AI-Bridge-Mac.dmg" >/dev/null
du -h "$ROOT/dist/AI-Bridge-Mac.dmg" | awk '{print "   " $2 "  (" $1 ")"}'
