#!/bin/zsh
# Build and install the VariableStroke Quick Look extension for this user, and
# have Quick Look use it for .glyphs files instead of Glyphs' own preview
# (Glyphs 3 and 4 each have one; with several, Quick Look picks one of them).
# uninstall.sh undoes all of it.
set -e
cd "${0:A:h}"
ID=com.codex.VariableStroke.QuickLook.Preview
GLYPHS=(com.GeorgSeifert.Glyphs3.GlyphsQuickLookExtension com.GeorgSeifert.Glyphs4.GlyphsQuickLookExtension)
TARGET="$HOME/Applications/VariableStroke Quick Look.app"
EXT="$TARGET/Contents/PlugIns/VariableStrokePreview.appex"

./build.sh
[[ -d "$EXT" ]] && pluginkit -r "$EXT" 2>/dev/null || true
mkdir -p "$HOME/Applications"
rm -rf "$TARGET"
cp -R "build/VariableStroke Quick Look.app" "$TARGET"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$TARGET"
# Registering right after the app was replaced does not always stick: try again.
for attempt in 1 2 3 4 5; do
  pluginkit -a "$EXT"
  sleep 1
  pluginkit -m -i $ID | grep -q $ID && break
done
pluginkit -m -i $ID | grep -q $ID || { echo "The extension did not register." >&2; exit 1; }
pluginkit -e use -i $ID
for id in $GLYPHS; do
  pluginkit -m -i $id | grep -q $id && pluginkit -e ignore -i $id
done
qlmanage -r >/dev/null 2>&1 || true
pluginkit -m -v -p com.apple.quicklook.preview | grep -E "VariableStroke|GlyphsQuickLook"
echo "Installed. (+ is in use, - is set aside.)"
