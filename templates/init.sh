#!/usr/bin/env bash
# collab-templates 初始化脚本
# 用法：
#   init.sh small   [目标目录] [-n 项目名] [--no-now]   新建小版本，并在 NOW 任务表加一行（--no-now 不加）
#   init.sh large   [目标目录] [-n 项目名] [--no-now]   新建大版本，同上
#   init.sh upgrade [目标目录]               小版本升级为大版本（在 collab/ 补齐文件，不动已有文件）
#   init.sh protocol [目标目录]              用最新模板更新 collab/协议.md（只此一个文件，保留「本项目补充」一节）
#   init.sh migrate [目标目录]               v1 旧布局（根目录 PROGRESS.md、协作/）迁到 collab/，并改好链接
#   init.sh now        [目录，默认 ~/Desktop]  建立 NOW 总看板（NOW.md + NOW/ 数据目录；生成代码留在模板仓库）
#   init.sh now-update [目录，默认 ~/Desktop]  旧版 NOW（目录里有 build.py）用最新模板覆盖代码；新版无需更新
#   init.sh team       [班子目录，默认 ~/.collab-team]  从示例建立你的 班子.json
#   init.sh team-sync  [目标目录] [--all] [--force]   按 班子.json 重新生成 collab/成员.md；--all 刷 NOW 任务表里的全部项目
#   init.sh hook       [目标目录]   给已有项目装 Claude Code Stop hook：每轮结束校验 collab/，有错交回 Claude 修（新建项目自动装）
# 班子：有 ~/.collab-team/班子.json（或环境变量 COLLAB_TEAM 指定的目录）时，新建项目的 成员.md 按它生成。
# 布局：协作文件全部在 collab/ 下；项目根目录只放 AGENTS.md，Claude Code 通过 .claude/CLAUDE.md 导入它。
# 原则：除 protocol、now-update 外从不覆盖已有文件；已有 AGENTS.md / CLAUDE.md 只追加入口说明。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"; shift || true
DEST="."; NAME=""; ALL=0; FORCE=""; NO_NOW=0
while [ $# -gt 0 ]; do
  case "$1" in
    -n) NAME="${2:?-n 需要项目名}"; shift 2 ;;
    --all) ALL=1; shift ;;
    --force) FORCE="--force"; shift ;;
    --no-now) NO_NOW=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) DEST="$1"; shift ;;
  esac
done

case "$MODE" in small|large|upgrade|protocol|migrate|now|now-update|team|team-sync|hook) ;; *) sed -n '2,16p' "$0"; exit 1 ;; esac
if [ "$MODE" = now ] || [ "$MODE" = now-update ]; then [ "$DEST" != "." ] || DEST="$HOME/Desktop"; fi
TEAM_DIR="${COLLAB_TEAM:-$HOME/.collab-team}"
TEAM_FILE="$TEAM_DIR/班子.json"
if [ "$MODE" = team ]; then
  [ "$DEST" != "." ] || DEST="$TEAM_DIR"
  python3 "$SRC/team.py" init "$DEST"; exit $?
fi
if [ "$MODE" = team-sync ]; then
  [ -f "$TEAM_FILE" ] || { echo "没有班子配置：$TEAM_FILE（先运行 init.sh team）"; exit 1; }
  if [ "$ALL" = 1 ]; then
    NOW_FILE="${NOW_PATH:-$HOME/Desktop/NOW.md}"
    rc=0
    while IFS= read -r proj; do
      python3 "$SRC/team.py" render "$TEAM_FILE" "$proj/collab/成员.md" $FORCE || rc=1
    done < <(python3 "$SRC/team.py" projects "$NOW_FILE")
    exit $rc
  fi
  DEST="$(cd "$DEST" && pwd)"
  [ -e "$DEST/collab/PROGRESS.md" ] || { echo "未找到 collab/PROGRESS.md：$DEST"; exit 1; }
  python3 "$SRC/team.py" render "$TEAM_FILE" "$DEST/collab/成员.md" $FORCE; exit $?
fi
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
[ -n "$NAME" ] || NAME="$(basename "$DEST")"
NOW_FILE="${NOW_PATH:-$HOME/Desktop/NOW.md}"
# PROGRESS.md 在 collab/ 下，NOW 链接相对 collab/ 计算
NOW_REL="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$NOW_FILE" "$DEST/collab")"
TODAY="$(date +%Y-%m-%d)"

created=(); skipped=(); appended=(); notes=()

# 复制单个文件；.md 做占位符替换
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
    case "$rel" in AGENTS.md|.claude/CLAUDE.md) continue ;; esac
    case "$rel" in NOW/build.py|NOW/template.html) continue ;; esac  # NOW 只放数据，代码留在模板仓库
    copy_one "$f" "$DEST/$rel"
  done < <(find "$base/$sub" -type f -print0 | sort -z)
}

# NOW 任务表加一行，链接到本项目 collab/PROGRESS.md；已有链接或找不到 NOW 时跳过
now_row() {
  [ "$NO_NOW" = 0 ] || return 0
  if [ ! -f "$NOW_FILE" ]; then
    notes+=("没找到 NOW：$NOW_FILE，未加进 NOW 任务表（用 NOW_PATH 指明，或加 --no-now）"); return 0
  fi
  local r
  r="$(python3 - "$NOW_FILE" "$DEST/collab/PROGRESS.md" "$NAME" <<'PY'
import os, re, sys
now, prog, name = sys.argv[1:]
t = open(now, encoding="utf-8").read()
base = os.path.dirname(os.path.abspath(now))
for m in re.finditer(r"\]\(<?([^)>]+)>?\)", t):
    if os.path.normpath(os.path.join(base, m.group(1))) == os.path.normpath(prog):
        print("exists"); sys.exit()
sec = re.search(r"(?m)^## 任务\s*$", t)
head = re.compile(r"(?m)^\|[ \t]*任务[ \t]*\|.*\n\|[-| :]+\|[ \t]*\n")
m = head.search(t, sec.end()) if sec else None
if not m:
    print("notable"); sys.exit()
rel = os.path.relpath(prog, base)
row = "| [%s](<%s>) |  |  | 进行中 |\n" % (name, rel)
open(now, "w", encoding="utf-8").write(t[:m.end()] + row + t[m.end():])
print("added")
PY
)"
  case "$r" in
    added) appended+=("NOW 任务表（$NOW_FILE；分类、等级待填）") ;;
    exists) skipped+=("NOW 任务表（已有本项目）") ;;
    *) notes+=("$NOW_FILE 里没找到「## 任务」表，未加行") ;;
  esac
}

# .claude/settings.json 里加 Stop hook：每轮结束运行 tools/check.py --hook；已有同一条时不重复加
CHECK_PY="$(cd "$SRC/.." && pwd)/tools/check.py"
hook_install() {
  local r
  r="$(python3 - "$DEST/.claude/settings.json" "$CHECK_PY" <<'PY'
import json, os, sys
path, check = sys.argv[1:]
cmd = "python3 '%s' --hook" % check
cfg = {}
if os.path.exists(path):
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except ValueError:
        print("bad"); sys.exit()
stop = cfg.setdefault("hooks", {}).setdefault("Stop", [])
for grp in stop:
    for h in grp.get("hooks", []):
        if "check.py" in h.get("command", "") and "--hook" in h.get("command", ""):
            if h["command"] == cmd:
                print("exists"); sys.exit()
            h["command"] = cmd
            json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print("updated"); sys.exit()
stop.append({"hooks": [{"type": "command", "command": cmd, "timeout": 30}]})
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("added")
PY
)"
  case "$r" in
    added) created+=(".claude/settings.json（Stop hook：每轮结束校验 collab/）") ;;
    updated) appended+=(".claude/settings.json（Stop hook 路径已更新）") ;;
    exists) skipped+=(".claude/settings.json 的 Stop hook") ;;
    *) notes+=(".claude/settings.json 不是合法 JSON，没装 Stop hook，请手动修好后运行 init.sh hook") ;;
  esac
}

# AGENTS.md：不存在则创建，存在则追加。
# Claude Code：根目录已有 CLAUDE.md 就在其中导入 @AGENTS.md，否则用 .claude/CLAUDE.md，保持根目录干净。
pointer() {  # $1 版本目录
  local a="$DEST/AGENTS.md" c="$DEST/CLAUDE.md" hc="$DEST/.claude/CLAUDE.md"
  if [ ! -e "$a" ]; then cp "$1/AGENTS.md" "$a"; created+=("AGENTS.md")
  elif ! grep -q 'collab-templates:pointer' "$a"; then printf '\n' >> "$a"; cat "$1/AGENTS.md" >> "$a"; appended+=("AGENTS.md")
  fi
  if [ -e "$c" ]; then
    grep -qx '@AGENTS.md' "$c" || { printf '\n@AGENTS.md\n' >> "$c"; appended+=("CLAUDE.md"); }
  elif [ ! -e "$hc" ]; then mkdir -p "$DEST/.claude"; cp "$1/.claude/CLAUDE.md" "$hc"; created+=(".claude/CLAUDE.md")
  elif ! grep -qx '@../AGENTS.md' "$hc"; then printf '\n@../AGENTS.md\n' >> "$hc"; appended+=(".claude/CLAUDE.md")
  fi
}

old_layout_hint() {
  if [ -e "$DEST/PROGRESS.md" ]; then
    echo "这是 v1 旧布局（根目录有 PROGRESS.md），先迁移：init.sh migrate \"$DEST\""; exit 1
  fi
}

case "$MODE" in
  small|large)
    old_layout_hint
    if [ -e "$DEST/collab/PROGRESS.md" ]; then
      echo "已存在 collab/PROGRESS.md，未做任何改动：$DEST"
      echo "小版本升级用：init.sh upgrade \"$DEST\""; exit 1
    fi
    copy_tree "$SRC/$MODE"; pointer "$SRC/$MODE"
    if [ -f "$TEAM_FILE" ] && printf '%s\n' "${created[@]}" | grep -qx 'collab/成员.md'; then
      rm "$DEST/collab/成员.md"
      python3 "$SRC/team.py" render "$TEAM_FILE" "$DEST/collab/成员.md" >/dev/null
      kept=(); for c in "${created[@]}"; do [ "$c" = "collab/成员.md" ] || kept+=("$c"); done
      created=("${kept[@]}" "collab/成员.md（按班子 $TEAM_FILE 生成）")
    fi
    hook_install
    now_row ;;
  hook)
    [ -e "$DEST/collab/PROGRESS.md" ] || { echo "未找到 collab/PROGRESS.md：$DEST"; exit 1; }
    hook_install ;;
  upgrade)
    old_layout_hint
    [ -e "$DEST/collab/PROGRESS.md" ] || { echo "未找到 collab/PROGRESS.md：$DEST"; exit 1; }
    copy_tree "$SRC/large" "collab"; pointer "$SRC/large" ;;
  protocol)
    old_layout_hint
    [ -e "$DEST/collab/协议.md" ] || { echo "未找到 collab/协议.md，这不是大版本项目：$DEST"; exit 1; }
    # 用新模板覆盖，但保留项目自己的「本项目补充」一节（旧协议没有这一节时用模板里的空节）
    python3 - "$SRC/large/collab/协议.md" "$DEST/collab/协议.md" <<'PY'
import re, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
new, old = src.read_text(encoding="utf-8"), dst.read_text(encoding="utf-8")
pat = re.compile(r"(?ms)^## (?:\d+\. )?本项目补充\s*\n.*\Z")
m = pat.search(old)
if m:
    body = m.group(0).split("\n", 1)[1]
    new = pat.sub(lambda n: n.group(0).split("\n", 1)[0] + "\n" + body, new)
dst.write_text(new, encoding="utf-8")
PY
    created+=("collab/协议.md（已更新，保留「本项目补充」）") ;;
  migrate)
    [ -e "$DEST/PROGRESS.md" ] || { echo "根目录没有 PROGRESS.md，无需迁移：$DEST"; exit 1; }
    [ ! -e "$DEST/collab" ] || { echo "已存在 collab/，请先手动处理：$DEST"; exit 1; }
    python3 - "$DEST" "$NOW_FILE" "$SRC/small/AGENTS.md" <<'PY'
import os, re, sys, shutil
dest, now_file, pointer_src = sys.argv[1:]
J = os.path.join

# 1. 旧路径 → 新路径（都相对项目根）
moves = {}
for f in ("PROGRESS.md", "任务.md", "归档.md"):
    if os.path.isfile(J(dest, f)):
        moves[f] = "collab/" + f
old_collab = J(dest, "协作")
if os.path.isdir(old_collab):
    for root, _, files in os.walk(old_collab):
        for f in files:
            rel = os.path.relpath(J(root, f), dest)
            moves[rel] = "collab/" + os.path.relpath(J(root, f), old_collab)
moved_dirs = {"协作": "collab"} if os.path.isdir(old_collab) else {}

LINK = re.compile(r"\]\((<[^>\n]+>|[^)\s]+)\)")

def remap(target_abs):
    """旧绝对路径 → 新绝对路径（未移动则原样返回）。"""
    rel = os.path.relpath(target_abs, dest)
    if rel in moves:
        return J(dest, moves[rel])
    for o, n in moved_dirs.items():
        if rel == o or rel.startswith(o + os.sep):
            return J(dest, n + rel[len(o):])
    return target_abs

def rewrite(text, old_dir, new_dir):
    def sub(m):
        raw = m.group(1); wrapped = raw.startswith("<")
        target = raw[1:-1] if wrapped else raw
        path, _, frag = target.partition("#")
        if not path or re.match(r"^[a-z][a-z0-9+.-]*:", path) or path.startswith(("/", "~")):
            return m.group(0)
        old_abs = os.path.normpath(J(old_dir, path))
        new_abs = remap(old_abs)
        if new_abs == old_abs and old_dir == new_dir:
            return m.group(0)
        new = os.path.relpath(new_abs, new_dir)
        if path.endswith("/") and not new.endswith("/"):
            new += "/"
        new = new + ("#" + frag if frag else "")
        if new == target:
            return m.group(0)
        return "](" + ("<%s>" % new if wrapped else new) + ")"
    return LINK.sub(sub, text)

# 2. 先改好内容，再移动
contents = {}
for old, new in moves.items():
    p = J(dest, old)
    if not old.endswith(".md"):
        continue
    t = open(p, encoding="utf-8").read()
    t = rewrite(t, os.path.dirname(p), os.path.dirname(J(dest, new)))
    t = t.replace("collab-templates: small v1", "collab-templates: small v2").replace("collab-templates: large v1", "collab-templates: large v2")
    # 纯文本里的旧路径（如加载地图中的 协作/协议.md）
    t = t.replace("`../PROGRESS.md`", "`PROGRESS.md`").replace("协作/", "")
    contents[new] = t
os.makedirs(J(dest, "collab"), exist_ok=True)
for old, new in moves.items():
    os.makedirs(os.path.dirname(J(dest, new)), exist_ok=True)
    shutil.move(J(dest, old), J(dest, new))
    if new in contents:
        open(J(dest, new), "w", encoding="utf-8").write(contents[new])
if os.path.isdir(old_collab):
    shutil.rmtree(old_collab)  # 此时只剩空目录
print("  迁移  " + "  ".join(sorted(moves)) + "  →  collab/")

# 3. 项目内其他 Markdown 中指向旧位置的链接
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "collab"}
for root, dirs, files in os.walk(dest):
    dirs[:] = [d for d in dirs if d not in SKIP and not (root == dest and d == "data")]
    for f in files:
        if not f.endswith(".md"):
            continue
        p = J(root, f)
        try:
            t = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        n = rewrite(t, root, root)
        if n != t:
            open(p, "w", encoding="utf-8").write(n)
            print("  改链接  " + os.path.relpath(p, dest))

# 4. NOW 总看板里指向本项目 PROGRESS.md 的链接
now_dir = os.path.dirname(os.path.abspath(os.path.expanduser(now_file)))
cands = [os.path.expanduser(now_file)] + [J(now_dir, "NOW", f) for f in ("已完成.md", "本月布局.md", "今天.md")]
old_prog, new_prog = J(dest, "PROGRESS.md"), J(dest, "collab", "PROGRESS.md")
for p in cands:
    if not os.path.isfile(p):
        continue
    t = open(p, encoding="utf-8").read()
    def sub(m):
        raw = m.group(1); wrapped = raw.startswith("<")
        target = raw[1:-1] if wrapped else raw
        if os.path.normpath(J(os.path.dirname(p), target)) != old_prog:
            return m.group(0)
        new = os.path.relpath(new_prog, os.path.dirname(p))
        return "](" + ("<%s>" % new if wrapped else new) + ")"
    n = LINK.sub(sub, t)
    if n != t:
        open(p, "w", encoding="utf-8").write(n)
        print("  改链接  " + p)

# 5. 入口说明
a = J(dest, "AGENTS.md")
new_ptr = open(pointer_src, encoding="utf-8").read()
if os.path.isfile(a):
    t = open(a, encoding="utf-8").read()
    m = re.search(r"<!-- collab-templates:pointer -->\n## 协作入口\n[^\n]*\n?", t)
    if m:
        t = t[:m.start()] + new_ptr + t[m.end():]
        open(a, "w", encoding="utf-8").write(t)
        print("  更新  AGENTS.md 入口说明")
    elif "PROGRESS.md" in t:
        print("  注意  AGENTS.md 是自定义内容，请手动把 PROGRESS.md 改成 collab/PROGRESS.md")
else:
    open(a, "w", encoding="utf-8").write(new_ptr)
    print("  新建  AGENTS.md")
c, hc = J(dest, "CLAUDE.md"), J(dest, ".claude", "CLAUDE.md")
if os.path.isfile(c) and open(c, encoding="utf-8").read().strip() == "@AGENTS.md" and not os.path.exists(hc):
    os.makedirs(os.path.dirname(hc), exist_ok=True)
    os.remove(c)
    open(hc, "w", encoding="utf-8").write("@../AGENTS.md\n")
    print("  移动  CLAUDE.md → .claude/CLAUDE.md")
elif not os.path.isfile(c) and not os.path.exists(hc):
    os.makedirs(os.path.dirname(hc), exist_ok=True)
    open(hc, "w", encoding="utf-8").write("@../AGENTS.md\n")
    print("  新建  .claude/CLAUDE.md")

gi = J(dest, ".gitignore")
if os.path.isfile(gi):
    lines = {l.strip() for l in open(gi, encoding="utf-8")}
    hit = lines & {"PROGRESS.md", "任务.md", "归档.md", "协作/", "协作"}
    if hit:
        print("  注意  .gitignore 忽略了 " + "、".join(sorted(hit)) + "，迁移后应改为 collab/")
PY
    ;;
  now)
    copy_tree "$SRC/now"
    python3 - "$DEST/NOW.md" "$SRC/now/NOW/build.py" "$DEST/NOW" <<'PY'
import os, sys
p, build, data = sys.argv[1:]
home = os.path.expanduser("~")
short = lambda x: "~" + x[len(home):] if x.startswith(home + "/") else x
t = open(p, encoding="utf-8").read()
cmd = "python3 %s --now-dir %s" % (short(build), short(data))
open(p, "w", encoding="utf-8").write(t.replace("`python3 NOW/build.py`", "`%s`" % cmd))
PY
    python3 "$SRC/now/NOW/build.py" --now-dir "$DEST/NOW" >/dev/null && created+=("NOW/看板.html（已生成）") ;;
  now-update)
    [ -d "$DEST/NOW" ] || { echo "未找到 NOW/：$DEST"; exit 1; }
    if [ ! -e "$DEST/NOW/build.py" ]; then echo "NOW/ 里没有代码（新版 NOW 由模板仓库的 build.py 生成），无需更新"; exit 0; fi
    for f in build.py template.html; do cp "$SRC/now/NOW/$f" "$DEST/NOW/$f"; created+=("NOW/$f（已更新）"); done
    python3 "$DEST/NOW/build.py" >/dev/null && created+=("NOW/看板.html（已生成）") ;;
esac

echo "目标：$DEST（$MODE）"
[ ${#created[@]}  -eq 0 ] || printf '  新建  %s\n' "${created[@]}"
[ ${#appended[@]} -eq 0 ] || printf '  追加  %s\n' "${appended[@]}"
[ ${#skipped[@]}  -eq 0 ] || printf '  跳过（已存在）  %s\n' "${skipped[@]}"
[ ${#notes[@]}    -eq 0 ] || printf '  注意  %s\n' "${notes[@]}"

if [ "$MODE" = now ]; then cat <<MSG

下一步：
  1. 改 NOW/config.json 里的分类（和 NOW.md 大方向的方向名对应）与截止来源
  2. 看板刷新二选一（见模板仓库 README「NOW 总看板」）：用常驻网页服务（推荐，打开即最新），或在 ~/.claude/settings.json 加 Stop hook 自动重生成 看板.html
  3. 之后建项目用 NOW_PATH=$DEST/NOW.md init.sh small，项目会自动加进 NOW 任务表
MSG
fi

if [ "$MODE" = upgrade ]; then cat <<MSG

还需要一次手动迁移（可直接交给 agent：“按 collab/协议.md 把小版本内容迁移到大版本”），以下路径都在 collab/ 下：
  1. 任务.md 中未完成的卡片拆成 任务/T00x-*.md；已完成的移到 归档/任务/；已完成的目标卡移到 归档/目标/
  2. PROGRESS「决议」每条展开写进 决议.md，PROGRESS 中改名为「生效决议」只留一行摘要
  3. PROGRESS 中「任务」改为「进行中」，补上「加载地图」（照抄 $SRC/large/collab/PROGRESS.md），删除末尾「协作规则」
  4. 目标.md 中的卡拆成 目标/G00x-*.md（流程表放「流程」一节）
  5. 归档.md 的内容分到 归档/ 下，然后删除 任务.md、目标.md 和 归档.md（日志.md 两版通用，不用动）
MSG
fi
