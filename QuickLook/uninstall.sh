#!/bin/zsh
# Remove the VariableStroke Quick Look extension and give .glyphs files back to
# Glyphs' own Quick Look previews.
cd "${0:A:h}"
GLYPHS=(com.GeorgSeifert.Glyphs3.GlyphsQuickLookExtension com.GeorgSeifert.Glyphs4.GlyphsQuickLookExtension)
TARGET="$HOME/Applications/VariableStroke Quick Look.app"
for id in $GLYPHS; do
  pluginkit -m -i $id | grep -q $id && pluginkit -e default -i $id
done
[[ -d "$TARGET" ]] && pluginkit -r "$TARGET/Contents/PlugIns/VariableStrokePreview.appex"
rm -rf "$TARGET"
qlmanage -r >/dev/null 2>&1 || true
pluginkit -m -v -p com.apple.quicklook.preview | grep -E "VariableStroke|GlyphsQuickLook"
echo "Uninstalled."
