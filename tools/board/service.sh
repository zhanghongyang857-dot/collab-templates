#!/bin/bash
# 御前看板常驻服务：用 launchd 用户代理启动「御前看板」小应用，由应用常驻拉起 server.py。
#
# 为什么要小应用：launchd 直接启动的 python 没有 macOS「文稿」访问权（实测
# Operation not permitted）。应用包在首次访问 ~/Documents 时由系统询问一次，总裁点
# 「允许」后，它启动的 python 子进程继承该授权；不需要「完全磁盘访问」。
#
# 用法：bash tools/board/service.sh install|uninstall|status|restart|logs [-f] [行数]
#
# 只写自己的文件：~/Applications/御前看板.app、
# ~/Library/LaunchAgents/com.local.ai-company-board.plist、~/Library/Logs/ai-company-board/。
set -euo pipefail

LABEL="com.local.ai-company-board"
APP_NAME="御前看板.app"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SERVER="$HERE/server.py"
PYTHON="/usr/bin/python3"
PORT="${BOARD_PORT:-3100}"
# NOW 目录：BOARD_NOW_DIR > 已安装配置里的 NOW_DIR > ~/Collab/NOW
_conf_now="$(awk -F= '/^NOW_DIR=/{sub(/^NOW_DIR=/, ""); print; exit}' "$HOME/Library/Application Support/ai-company-board/config" 2>/dev/null || true)"
NOW_DIR="${BOARD_NOW_DIR:-${_conf_now:-$HOME/Collab/NOW}}"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENTS_DIR/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/ai-company-board"
OUT_LOG="$LOG_DIR/board.log"
ERR_LOG="$LOG_DIR/board.err.log"
LAUNCHER_OUT="$LOG_DIR/launcher.log"
LAUNCHER_ERR="$LOG_DIR/launcher.err.log"
TCC_PROBE="$LOG_DIR/tcc-probe.txt"
PROBE_REQUEST="$LOG_DIR/probe.request"
CONFIG_DIR="$HOME/Library/Application Support/ai-company-board"
CONFIG="$CONFIG_DIR/config"
APP_BUILD="$HERE/build/$APP_NAME"
APP_DEST="$HOME/Applications/$APP_NAME"
APP_BIN="$APP_DEST/Contents/MacOS/launcher"
DOMAIN="gui/$(id -u)"
JOB="$DOMAIN/$LABEL"

say() { printf '%s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

# 已安装的端口（以配置为准，避免 status 时端口对不上）
installed_port() {
  local p=""
  if [ -f "$CONFIG" ]; then
    p="$(awk -F= '/^PORT=/{print $2; exit}' "$CONFIG")"
  fi
  printf '%s' "${p:-$PORT}"
}

loaded() { launchctl print "$JOB" >/dev/null 2>&1; }

# launchd 记录的启动器 PID（应用主程序）
launcher_pid() {
  launchctl print "$JOB" 2>/dev/null | awk '/pid = / { print $3; exit }' || true
}

# 监听指定端口的 PID（只取 LISTEN）
listener_pid() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
}

# PID 是否就是本看板服务（用 pgrep；沙箱里 ps 是 setuid，常被拒绝执行）
is_board_pid() {
  local pid="$1"
  [ -n "$pid" ] || return 1
  pgrep -fl 'tools/board/server\.py' 2>/dev/null | awk -v p="$pid" '$1 == p { found = 1 } END { exit !found }'
}

wait_port() {  # wait_port <端口> <秒> <要出现:up|消失:down>
  local port="$1" limit="$2" want="$3" i=0 pid
  while [ "$i" -lt $((limit * 5)) ]; do
    pid="$(listener_pid "$port")"
    if [ "$want" = up ] && [ -n "$pid" ]; then return 0; fi
    if [ "$want" = down ] && [ -z "$pid" ]; then return 0; fi
    sleep 0.2; i=$((i + 1))
  done
  return 1
}

http_ok() {  # http_ok <端口>
  "$PYTHON" - "$1" <<'PY' 2>/dev/null
import json, sys, urllib.request
port = sys.argv[1]
with urllib.request.urlopen("http://127.0.0.1:%s/api/projects" % port, timeout=5) as r:
    ok = r.status == 200 and isinstance(json.loads(r.read().decode("utf-8")), list)
sys.exit(0 if ok else 1)
PY
}

# 生成（或复用）工作区里的应用；安装只拷贝，改 server.py 不需要重建
ensure_app() {
  local script="$HERE/build_app.sh"
  [ -x "$script" ] || die "找不到 $script"
  [ -f "$HERE/launcher.c" ] || die "找不到 $HERE/launcher.c"
  local need=0
  if [ ! -x "$APP_BUILD/Contents/MacOS/launcher" ]; then
    need=1
  elif [ "$HERE/launcher.c" -nt "$APP_BUILD/Contents/MacOS/launcher" ]; then
    need=1
  elif [ "$script" -nt "$APP_BUILD/Contents/MacOS/launcher" ]; then
    need=1
  fi
  if [ "$need" = 1 ]; then
    say "生成启动器应用…"
    "$script" >/dev/null || die "生成启动器应用失败"
  else
    say "使用已生成的应用：$APP_BUILD"
  fi
  [ -x "$APP_BUILD/Contents/MacOS/launcher" ] || die "应用缺少主程序：$APP_BUILD"
  codesign --verify --deep --strict "$APP_BUILD" 2>/dev/null || die "应用签名校验失败：$APP_BUILD"
}

write_config() {
  local port="$1"
  mkdir -p "$CONFIG_DIR"
  cat > "$CONFIG" <<CONFIGFILE
PYTHON=$PYTHON
SERVER=$SERVER
ROOT=$ROOT
PORT=$port
NOW_DIR=$NOW_DIR
LOG_DIR=$LOG_DIR
CONFIGFILE
  [ -s "$CONFIG" ] || die "配置写入失败：$CONFIG"
}

write_plist() {
  local tmp
  mkdir -p "$AGENTS_DIR" "$LOG_DIR"
  tmp="$(mktemp "$AGENTS_DIR/.$LABEL.XXXXXX")"
  cat > "$tmp" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_BIN</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key>
    <string>en_US.UTF-8</string>
    <key>PYTHONIOENCODING</key>
    <string>utf-8</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LAUNCHER_OUT</string>
  <key>StandardErrorPath</key>
  <string>$LAUNCHER_ERR</string>
</dict>
</plist>
PLIST
  mv -f "$tmp" "$PLIST"
  plutil -lint "$PLIST" >/dev/null
}

bootout() {
  launchctl bootout "$JOB" 2>/dev/null || launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
}

LAUNCHER_MATCH="御前看板.app/Contents/MacOS/launcher"
launcher_running() { pgrep -f "$LAUNCHER_MATCH" >/dev/null 2>&1; }

# bootout 后等启动器退出；超时就发信号，避免旧的 LSUIElement 实例让 open 探针空等
stop_launcher_if_left() {
  local i=0
  while [ "$i" -lt 25 ]; do
    launcher_running || return 0
    sleep 0.2; i=$((i + 1))
  done
  say "旧启动器仍在运行，停止它…"
  pkill -TERM -f "$LAUNCHER_MATCH" 2>/dev/null || true
  i=0
  while [ "$i" -lt 25 ]; do
    launcher_running || return 0
    sleep 0.2; i=$((i + 1))
  done
  pkill -KILL -f "$LAUNCHER_MATCH" 2>/dev/null || true
  sleep 1
  launcher_running && die "旧启动器仍在运行（${APP_BIN}）"
  return 0
}

# 用 LaunchServices 启动应用做一次文稿权限检查；总裁在弹窗点「允许」。
# 必须用 open（而不是直接执行应用内二进制），这样系统才会把这次授权记到「御前看板」这个应用上。
check_documents() {
  rm -f "$TCC_PROBE"
  : > "$PROBE_REQUEST"
  say "检查「文稿」访问权限：若弹出「允许访问文稿文件夹」，请点“允许”…"
  /usr/bin/open -g -W -a "$APP_DEST" >/dev/null 2>&1 &
  local opid=$!
  local i=0
  while [ ! -f "$TCC_PROBE" ] && [ "$i" -lt 900 ]; do
    kill -0 "$opid" 2>/dev/null || break
    sleep 0.2; i=$((i + 1))
  done
  if [ "$i" -ge 900 ]; then
    kill "$opid" 2>/dev/null || true
    pkill -TERM -f "$LAUNCHER_MATCH" 2>/dev/null || true
    rm -f "$PROBE_REQUEST"
    die "等待「文稿」授权超时（3 分钟）。请重试；若弹窗还在，点“允许”后再执行 install"
  fi
  wait "$opid" 2>/dev/null || true
  sleep 0.5
  rm -f "$PROBE_REQUEST"
  if [ ! -f "$TCC_PROBE" ]; then
    stop_launcher_if_left
    die "权限检查没有结果，应用可能没有启动。可手动运行 open -a \"$APP_DEST\"，允许后再执行 install"
  fi
  if [ "$(head -1 "$TCC_PROBE")" != "ok" ]; then
    say "权限检查：$(sed -n '2p' "$TCC_PROBE")"
    die "还没有「文稿」访问权。请点弹窗的“允许”；若已点过拒绝，到 系统设置 → 隐私与安全性 → 文件与文件夹，给「御前看板」打开「文稿文件夹」，再重新 install"
  fi
  say "「文稿」权限正常"
}

require_writable() {
  local d probe
  for d in "$LOG_DIR" "$AGENTS_DIR" "$CONFIG_DIR" "$HOME/Applications"; do
    mkdir -p "$d" 2>/dev/null || die "无法创建目录：$d"
    probe="$d/.board-write-test.$$"
    if ! touch "$probe" 2>/dev/null; then
      die "没有写权限：${d}（沙箱或 macOS 隐私保护拦截；见 README「常驻运行」）"
    fi
    rm -f "$probe" 2>/dev/null || true
  done
}

install() {
  [ -f "$SERVER" ] || die "找不到 $SERVER"
  [ -x "$PYTHON" ] || die "找不到 $PYTHON"
  [ -x /usr/bin/open ] || die "找不到 /usr/bin/open"
  [ -d "$NOW_DIR" ] || die "NOW 目录不存在：${NOW_DIR}（可用 BOARD_NOW_DIR 指定）"

  local port="$PORT" old_port
  old_port="$(installed_port)"
  rm -f "$PROBE_REQUEST"
  require_writable
  ensure_app
  write_config "$port"

  # 先停旧的 launchd 服务
  if loaded; then
    say "已在运行，先停掉旧服务…"
    bootout
    wait_port "$old_port" 5 down || true
  fi
  stop_launcher_if_left

  # 安装应用（拷贝已签好的包，签名不变）
  rm -rf "$APP_DEST"
  ditto "$APP_BUILD" "$APP_DEST" || die "拷贝应用到 $APP_DEST 失败"
  codesign --verify --deep --strict "$APP_DEST" 2>/dev/null || die "安装后的应用签名校验失败：$APP_DEST"
  say "已安装应用：$APP_DEST"

  check_documents

  # 权限确认后再停占用端口的临时看板进程（只动 server.py，不动其他进程）
  local pid; pid="$(listener_pid "$port")"
  if [ -n "$pid" ]; then
    if is_board_pid "$pid"; then
      say "端口 $port 被临时启动的看板进程 $pid 占用，停掉它…"
      kill "$pid" 2>/dev/null || true
      wait_port "$port" 5 down || kill -9 "$pid" 2>/dev/null || true
      wait_port "$port" 5 down || die "端口 $port 仍被占用（PID ${pid}）"
    else
      die "端口 $port 被其他进程（$(lsof -p "$pid" 2>/dev/null | awk 'NR==2 {print $1}')，PID ${pid}）占用，未做改动；详情看 lsof -nP -iTCP:$port -sTCP:LISTEN"
    fi
  fi

  rm -f "$OUT_LOG" "$ERR_LOG" "$LAUNCHER_OUT" "$LAUNCHER_ERR"
  write_plist
  say "已写入 $PLIST"

  launchctl enable "$JOB" 2>/dev/null || true
  if ! launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    launchctl load -w "$PLIST" >/dev/null 2>&1 || true
  fi

  if ! loaded; then
    say "launchd 没有加载服务，最近日志："
    logs_show 20
    die "启用失败：launchctl 拒绝 ${PLIST}（用户代理需在 ~/Library/LaunchAgents 下且可读）"
  fi
  if ! wait_port "$port" 20 up; then
    say "服务未在 20 秒内监听 ${port}，最近日志："
    logs_show 30
    die "启用失败"
  fi
  if ! http_ok "$port"; then
    say "端口已监听但接口不通，最近日志："
    logs_show 30
    die "接口检查失败"
  fi
  say "已启用：http://127.0.0.1:$port （启动器 PID $(launcher_pid)，服务 PID $(listener_pid "$port")）"
}

uninstall() {
  local port; port="$(installed_port)"
  if [ ! -f "$PLIST" ] && ! loaded && [ ! -d "$APP_DEST" ] && [ ! -d "$CONFIG_DIR" ]; then
    say "未安装常驻服务，无需停用（手动启动的看板不受影响）"
    return 0
  fi
  local was_loaded=0
  if loaded; then was_loaded=1; bootout; fi
  stop_launcher_if_left
  rm -f "$PLIST"
  local pid; pid="$(listener_pid "$port")"
  if [ "$was_loaded" = 1 ] && [ -n "$pid" ] && is_board_pid "$pid"; then
    say "服务进程 $pid 仍在，停止它…"
    kill "$pid" 2>/dev/null || true
    wait_port "$port" 5 down || kill -9 "$pid" 2>/dev/null || true
  fi
  wait_port "$port" 8 down || die "端口 $port 仍被占用（PID $(listener_pid "$port")）"
  if [ -d "$APP_DEST" ]; then
    rm -rf "$APP_DEST"
    say "已删除应用：$APP_DEST"
  fi
  rm -f "$PROBE_REQUEST"
  if [ -d "$CONFIG_DIR" ]; then
    rm -rf "$CONFIG_DIR"
    say "已删除配置：$CONFIG_DIR"
  fi
  say "已停用并删除 ${PLIST}（日志保留在 ${LOG_DIR}）"
  say "「文稿」授权仍留在系统设置里；要彻底清掉，可在 系统设置 → 隐私与安全性 → 文件与文件夹 删除「御前看板」"
}

status() {
  local port; port="$(installed_port)"
  local pid; pid="$(listener_pid "$port")"
  local lpid; lpid="$(launcher_pid)"
  local rc=0
  say "服务：$LABEL"
  if [ -f "$PLIST" ]; then say "plist：$PLIST"; else say "plist：未安装"; rc=1; fi
  if [ -f "$CONFIG" ]; then say "配置：${CONFIG}（端口 ${port}）"; else say "配置：未安装"; rc=1; fi
  if [ -d "$APP_DEST" ]; then
    if codesign --verify --deep --strict "$APP_DEST" 2>/dev/null; then
      say "应用：${APP_DEST}（签名正常）"
    else
      say "应用：${APP_DEST}（签名校验失败）"; rc=1
    fi
  else
    say "应用：未安装（${APP_DEST}）"; rc=1
  fi
  if loaded; then say "launchd：已加载"; else say "launchd：未加载"; rc=1; fi
  if [ -n "$lpid" ] && [ "$lpid" != "0" ]; then
    say "启动器：运行中（PID ${lpid}）"
  else
    say "启动器：未运行"; rc=1
  fi
  if [ -n "$pid" ] && is_board_pid "$pid"; then
    if loaded; then say "服务进程：运行中（PID ${pid}，launchd 管理）"; else say "服务进程：运行中（PID ${pid}，手动启动，非常驻）"; fi
    say "监听：$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -F n 2>/dev/null | sed -n 's/^n//p' | paste -sd, -)"
  elif [ -n "$pid" ]; then
    say "服务进程：端口 $port 被其他进程占用（PID ${pid}）"; rc=1
  else
    say "服务进程：未运行"; rc=1
  fi
  if http_ok "$port"; then
    say "接口：http://127.0.0.1:$port/api/projects 正常"
  else
    say "接口：http://127.0.0.1:$port/api/projects 不通"; rc=1
  fi
  local f
  for f in "$LAUNCHER_ERR" "$ERR_LOG"; do
    if [ -f "$f" ] && [ -s "$f" ]; then
      say "最近错误日志（${f}）："
      tail -n 3 "$f" | sed 's/^/  /'
    fi
  done
  return "$rc"
}

logs_show() {
  local n="${1:-80}" f
  for f in "$LAUNCHER_OUT" "$LAUNCHER_ERR" "$OUT_LOG" "$ERR_LOG"; do
    say "== ${f}（最后 $n 行）=="
    if [ -f "$f" ]; then tail -n "$n" "$f"; else say "（无）"; fi
  done
}

logs() {
  local follow=0 n=80 a
  for a in "$@"; do
    case "$a" in
      -f|--follow) follow=1 ;;
      ''|*[!0-9]*) die "用法：logs [-f] [行数]" ;;
      *) n="$a" ;;
    esac
  done
  if [ "$follow" = 1 ]; then
    touch "$LAUNCHER_OUT" "$LAUNCHER_ERR" "$OUT_LOG" "$ERR_LOG"
    say "跟踪日志，Ctrl+C 退出："
    tail -n "$n" -f "$LAUNCHER_OUT" "$LAUNCHER_ERR" "$OUT_LOG" "$ERR_LOG"
  else
    logs_show "$n"
  fi
}

restart() {
  local port; port="$(installed_port)"
  if ! loaded; then
    say "服务未加载，执行 install…"
    install
    return
  fi
  launchctl kickstart -k "$JOB" || die "kickstart 失败，看 $LAUNCHER_ERR"
  if wait_port "$port" 15 up && http_ok "$port"; then
    say "已重启：http://127.0.0.1:$port （启动器 PID $(launcher_pid)，服务 PID $(listener_pid "$port")）"
  else
    logs_show 30
    die "重启后检查失败"
  fi
}

# 供测试 source：只加载函数，不执行子命令
if [ "${BASH_SOURCE[0]}" != "$0" ]; then return 0; fi

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  status) status ;;
  restart) restart ;;
  logs) shift; logs "$@" ;;
  *) say "用法：bash tools/board/service.sh install|uninstall|status|restart|logs [-f] [行数]"; exit 2 ;;
esac
