#!/usr/bin/env bash
# collab-templates 初始化脚本
# 用法：
#   init.sh small   [目标目录] [-n 项目名]   新建小版本
#   init.sh large   [目标目录] [-n 项目名]   新建大版本
#   init.sh upgrade [目标目录]               小版本升级为大版本（补齐 协作/，不动已有文件）
#   init.sh protocol [目标目录]              用最新模板覆盖 协作/协议.md（只此一个文件）
#   init.sh now        [目录，默认 ~/Desktop]  建立 NOW 总看板（NOW.md + NOW/ 目录）
#   init.sh now-update [目录，默认 ~/Desktop]  用最新模板覆盖 NOW/build.py 和 NOW/template.html（不动数据和配置）
# 原则：除 protocol、now-update 外从不覆盖已有文件；已有 AGENTS.md / CLAUDE.md 只追加入口说明。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"; shift || true
DEST="."; NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    -n) NAME="${2:?-n 需要项目名}"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) DEST="$1"; shift ;;
  esac
done

case "$MODE" in small|large|upgrade|protocol|now|now-update) ;; *) sed -n '2,10p' "$0"; exit 1 ;; esac
if [ "$MODE" = now ] || [ "$MODE" = now-update ]; then [ "$DEST" != "." ] || DEST="$HOME/Desktop"; fi
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
[ -n "$NAME" ] || NAME="$(basename "$DEST")"
NOW_FILE="${NOW_PATH:-$HOME/Desktop/NOW.md}"
NOW_REL="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$NOW_FILE" "$DEST")"
TODAY="$(date +%Y-%m-%d)"

created=(); skipped=(); appended=()

# 复制单个文件；PROGRESS.md 做占位符替换
copy_one() {  # $1 源文件  $2 目标文件
  local src="$1" dst="$2"
  if [ -e "$dst" ]; then skipped+=("${dst#$DEST/}"); return; fi
  mkdir -p "$(dirname "$dst")"
  if [ "${src##*.}" = "md" ]; then
    python3 - "$src" "$dst" "$NAME" "$NOW_REL" "$TODAY" <<'PY'
import sys, datetime as dt
src, dst, name, now, today = sys.argv[1:]
wd = "一二三四五六日"
d0 = dt.date.today(); d1 = d0 + dt.timedelta(days=14)
t = open(src, encoding="utf-8").read()
for k, v in {"项目名": name, "NOW路径": now, "日期": today, "今天": d0.strftime("%m-%d"),
             "周几": "周" + wd[d0.weekday()], "示例日期": d1.isoformat(), "示例周几": "周" + wd[d1.weekday()]}.items():
    t = t.replace("{{" + k + "}}", v)
open(dst, "w", encoding="utf-8").write(t)
PY
  else
    cp "$src" "$dst"
  fi
  created+=("${dst#$DEST/}")
}

# 复制整个目录（跳过入口说明文件，由 pointer 单独处理）
copy_tree() {  # $1 源目录  $2 只复制该子路径（可空）
  local base="$1" sub="${2:-}"
  while IFS= read -r -d '' f; do
    local rel="${f#$base/}"
    case "$rel" in AGENTS.md|CLAUDE.md) continue ;; esac
    copy_one "$f" "$DEST/$rel"
  done < <(find "$base/$sub" -type f -print0 | sort -z)
}

# AGENTS.md / CLAUDE.md：不存在则创建，存在则追加
pointer() {  # $1 版本目录
  local a="$DEST/AGENTS.md" c="$DEST/CLAUDE.md"
  if [ ! -e "$a" ]; then cp "$1/AGENTS.md" "$a"; created+=("AGENTS.md")
  elif ! grep -q 'collab-templates:pointer' "$a"; then printf '\n' >> "$a"; cat "$1/AGENTS.md" >> "$a"; appended+=("AGENTS.md")
  fi
  if [ ! -e "$c" ]; then cp "$1/CLAUDE.md" "$c"; created+=("CLAUDE.md")
  elif ! grep -qx '@AGENTS.md' "$c"; then printf '\n@AGENTS.md\n' >> "$c"; appended+=("CLAUDE.md")
  fi
}

case "$MODE" in
  small|large)
    if [ -e "$DEST/PROGRESS.md" ]; then
      echo "已存在 PROGRESS.md，未做任何改动：$DEST"
      echo "小版本升级用：init.sh upgrade \"$DEST\""; exit 1
    fi
    copy_tree "$SRC/$MODE"; pointer "$SRC/$MODE" ;;
  upgrade)
    [ -e "$DEST/PROGRESS.md" ] || { echo "未找到 PROGRESS.md：$DEST"; exit 1; }
    copy_tree "$SRC/large" "协作"; pointer "$SRC/large" ;;
  protocol)
    [ -d "$DEST/协作" ] || { echo "未找到 协作/，这不是大版本项目：$DEST"; exit 1; }
    cp "$SRC/large/协作/协议.md" "$DEST/协作/协议.md"; created+=("协作/协议.md（已更新）") ;;
  now)
    copy_tree "$SRC/now"
    python3 "$DEST/NOW/build.py" >/dev/null && created+=("NOW/看板.html（已生成）") ;;
  now-update)
    [ -d "$DEST/NOW" ] || { echo "未找到 NOW/：$DEST"; exit 1; }
    for f in build.py template.html; do cp "$SRC/now/NOW/$f" "$DEST/NOW/$f"; created+=("NOW/$f（已更新）"); done
    python3 "$DEST/NOW/build.py" >/dev/null && created+=("NOW/看板.html（已生成）") ;;
esac

echo "目标：$DEST（$MODE）"
[ ${#created[@]}  -eq 0 ] || printf '  新建  %s\n' "${created[@]}"
[ ${#appended[@]} -eq 0 ] || printf '  追加  %s\n' "${appended[@]}"
[ ${#skipped[@]}  -eq 0 ] || printf '  跳过（已存在）  %s\n' "${skipped[@]}"

if [ "$MODE" = now ]; then cat <<MSG

下一步：
  1. 改 NOW/config.json 里的分类（和 NOW.md 大方向的方向名对应）与截止来源
  2. 想让 Claude Code 每轮回答后自动刷新看板，在 ~/.claude/settings.json 加 Stop hook（见模板仓库 README「NOW 总看板」）
MSG
fi

if [ "$MODE" = upgrade ]; then cat <<MSG

还需要一次手动迁移（可直接交给 agent：“按 协作/协议.md 把小版本内容迁移到大版本”）：
  1. 任务.md 中未完成的卡片拆成 协作/任务/T00x-*.md；已完成的移到 协作/归档/任务/
  2. PROGRESS「决议」每条展开写进 协作/决议.md，PROGRESS 中改名为「生效决议」只留一行摘要
  3. PROGRESS 中「任务」改为「进行中」，补上「加载地图」（照抄 $SRC/large/PROGRESS.md），删除末尾「协作规则」
  4. 归档.md 的内容分到 协作/归档/ 下，然后删除 任务.md 和 归档.md
MSG
fi
