#!/bin/zsh
# Build "VariableStroke Quick Look.app" (with its Quick Look extension) into
# QuickLook/build, signed to run on this Mac only.
set -e
cd "${0:A:h}"
APP="build/VariableStroke Quick Look.app"
EXT="$APP/Contents/PlugIns/VariableStrokePreview.appex"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$EXT/Contents/MacOS"
swiftc -O -target arm64-apple-macos13.0 -module-name VariableStrokePreview -parse-as-library \
  -Xlinker -e -Xlinker _NSExtensionMain -framework Quartz -framework QuickLookThumbnailing \
  -o "$EXT/Contents/MacOS/VariableStrokePreview" PreviewProvider.swift
cp Extension-Info.plist "$EXT/Contents/Info.plist"
swiftc -O -target arm64-apple-macos13.0 -o "$APP/Contents/MacOS/VariableStrokeQuickLook" Host.swift
cp Host-Info.plist "$APP/Contents/Info.plist"
codesign -f -s - --entitlements Extension.entitlements "$EXT"
codesign -f -s - "$APP"
echo "Built $APP"
