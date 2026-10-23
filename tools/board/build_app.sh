#!/bin/bash
# 生成「御前看板」启动器应用（不安装）：编译 launcher.c，写 Info.plist，做 ad-hoc 签名并自检。
#
# 产物：tools/board/build/御前看板.app
# 用法：bash tools/board/build_app.sh
#
# 只用 macOS 自带工具（clang、plutil、codesign），不联网、不写 ~/Applications。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build"
APP="$BUILD/御前看板.app"
BUNDLE_ID="com.local.ai-company-board"
CLANG="${CLANG:-/usr/bin/clang}"

[ -x "$CLANG" ] || { echo "错误：找不到 clang（${CLANG}）" >&2; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# 注意：每次重新编译 Mach-O UUID 会变，签名和 cdhash 随之变化，系统可能重新询问
# 「文稿」权限。安装只拷贝已生成的应用；改看板代码（server.py）不需要重建应用。
"$CLANG" -Os -Wall -Wextra -o "$APP/Contents/MacOS/launcher" "$HERE/launcher.c"
chmod 755 "$APP/Contents/MacOS/launcher"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>zh_CN</string>
  <key>CFBundleExecutable</key>
  <string>launcher</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>御前看板</string>
  <key>CFBundleDisplayName</key>
  <string>御前看板</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSDocumentsFolderUsageDescription</key>
  <string>御前看板需要读取 ~/Documents 下的项目数据和 NOW 看板数据。</string>
</dict>
</plist>
PLIST

plutil -lint "$APP/Contents/Info.plist" >/dev/null
codesign --force --sign - --identifier "$BUNDLE_ID" "$APP"
codesign --verify --deep --strict "$APP"

echo "已生成：$APP"
echo "  标识：$BUNDLE_ID"
echo "  主程序：$APP/Contents/MacOS/launcher"
codesign -dv --verbose=2 "$APP" 2>&1 | sed -n 's/^/  /p'
