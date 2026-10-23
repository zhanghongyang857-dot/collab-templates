#!/usr/bin/env python3
"""把 NOW.md（任务、大方向）、各任务 PROGRESS.md、今天.md、本月布局.md、已完成.md
和近期截止文件生成 看板.html。

用法：python3 NOW/build.py
      python3 NOW/build.py --if-changed   只在源文件比看板新时生成（供 Claude Code Stop hook 调用）
      python3 build.py --now-dir <NOW 目录>  代码和数据分开放时指定数据目录（也可用环境变量 NOW_DATA_DIR）
内容只改 Markdown；分类、配色和截止来源改 config.json；样式和布局改 template.html。
"""
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

CODE = Path(__file__).resolve().parent  # 本脚本和 template.html 所在目录


def _data_dir():
    """NOW 数据目录：--now-dir、环境变量 NOW_DATA_DIR，都没有时与代码同目录。"""
    if "--now-dir" in sys.argv[:-1]:
        return Path(sys.argv[sys.argv.index("--now-dir") + 1]).expanduser().resolve()
    env = os.environ.get("NOW_DATA_DIR")
    return Path(env).expanduser().resolve() if env else CODE


HERE = _data_dir()
CONFIG = HERE / "config.json"


def _load_config():
    """个人设置都在 config.json：分类与配色、近期截止来源。缺省时用通用分类、不显示截止。"""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    cfg.setdefault("categories", [])
    cfg.setdefault("rest_keywords", ["不计时"])
    cfg.setdefault("deadlines", {})
    return cfg


def _path(p, default=None):
    if not p:
        return default
    p = Path(p).expanduser()
    return p if p.is_absolute() else (HERE / p).resolve()


CFG = _load_config()
TODAY_MD = HERE / "今天.md"
MONTH_MD = HERE / "本月布局.md"
NOW_MD = _path(CFG.get("now_md"), HERE.parent / "NOW.md")
DONE_MD = HERE / "已完成.md"
DONE_SHOW = CFG.get("done_show", 5)  # 看板上显示最近几条已完成
DL = CFG["deadlines"]
EXAMS_MD = _path(DL.get("file"))
DL_GROUPS = DL.get("groups") or []  # 只显示这些分组（标题第一个词）；为空则全部显示
TEMPLATE = CODE / "template.html"
OUT = _path(CFG.get("output"), HERE / "看板.html")  # 可在 config.json 用 output 指定生成位置

# 关键词 → 分类配色键（顺序即优先级）：先「不计时」，再按 config 中分类的顺序
CATEGORIES = [(k, "rest") for k in CFG["rest_keywords"]] + [
    (k, "cat-" + c["color"]) for c in CFG["categories"] for k in c.get("keywords", [c["name"]])
]
CAT_NAMES = {"other": "其他", "rest": "不计时", **{"cat-" + c["color"]: c["name"] for c in reversed(CFG["categories"])}}


def inline(text, base):
    """Markdown 行内语法 → HTML：链接、粗体、删除线、代码。"""
    out, pos = [], 0
    for m in re.finditer(r"\[([^\]]+)\]\(<?([^)>]+)>?\)", text):
        out.append(_fmt(text[pos:m.start()]))
        href = m.group(2)
        if not re.match(r"[a-z]+:", href):
            href = "file://" + quote(str((base / href).resolve()))
        out.append(f'<a href="{html.escape(href)}">{_fmt(m.group(1))}</a>')
        pos = m.end()
    out.append(_fmt(text[pos:]))
    return "".join(out)


def _fmt(t):
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"~~(.+?)~~", r"<del>\1</del>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


def table_rows(lines):
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines if l.lstrip().startswith("|")]
    return rows[0], [r for r in rows[2:]]


def category(task):
    for key, cat in CATEGORIES:
        if key in task:
            return cat
    return "other"


def parse_today():
    text = TODAY_MD.read_text(encoding="utf-8")
    lines = text.splitlines()
    m = re.search(r"^## (\d{2}-\d{2})（(周.)）", text, re.M)
    note = " ".join(l.lstrip("> ").strip() for l in lines if l.startswith(">"))
    _, rows = table_rows(lines)
    slots, extras = [], []
    for when, what, task in (r[:3] for r in rows):
        t = re.match(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", when)
        if t:
            slots.append({"start": t.group(1), "end": t.group(2), "what": inline(what, HERE),
                          "task": task if task not in ("—", "-") else "", "cat": category(task)})
        else:
            extras.append({"label": when, "what": inline(what, HERE)})
    return {"date": m.group(1) if m else "", "weekday": m.group(2) if m else "",
            "note": inline(note, HERE), "slots": slots, "extras": extras}


def sections(text):
    """按 `## ` 标题切分，返回 [(标题, 行列表)]；标题前的内容标题为空串。"""
    out, title, buf = [], "", []
    for line in text.splitlines():
        if line.startswith("## "):
            out.append((title, buf))
            title, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out.append((title, buf))
    return out


def find_section(secs, prefix):
    for title, lines in secs:
        if title.startswith(prefix):
            return title, lines
    return "", []


def parse_month():
    secs = sections(MONTH_MD.read_text(encoding="utf-8"))
    rng, lines = next(((t, l) for t, l in secs if t and not t.startswith("本周三件事")), ("", []))
    paras = [l for l in lines if l.strip() and not l.startswith(("#", "|", "[←"))]
    head, rows = table_rows(lines) if any(l.lstrip().startswith("|") for l in lines) else ([""], [])
    weeks = []
    for r in rows:
        w = re.match(r"(第\s*\d+\s*周)\s*(\d{2}-\d{2})～(\d{2}-)?(\d{2})", r[0])
        weeks.append({
            "label": w.group(1) if w else r[0],
            "start": w.group(2) if w else "",
            "end": ((w.group(3) or w.group(2)[:3]) + w.group(4)) if w else "",
            "range": r[0][w.end(1):].strip() if w else "",
            "cells": [inline(c, HERE) for c in r[1:]],
        })
    wt, wl = find_section(secs, "本周三件事")
    three = [inline(re.sub(r"^\d+\.\s*", "", l.strip()), HERE) for l in wl if re.match(r"\s*\d+\.", l)]
    return {"range": rng, "intro": inline(paras[0], HERE) if paras else "",
            "pending": [inline(p, HERE) for p in paras[1:]], "cols": head[1:],
            "colCats": [category(c) for c in head[1:]], "weeks": weeks,
            "three_title": wt, "three": three}


def link_parts(cell, base):
    """表格单元里的 [名称](路径) → (名称, 绝对路径或 None)。"""
    m = re.search(r"\[([^\]]+)\]\(<?([^)>]+)>?\)", cell)
    if not m:
        return cell, None
    return m.group(1), (base / m.group(2)).resolve()


def table_dicts(lines):
    if not any(l.lstrip().startswith("|") for l in lines):
        return []
    head, rows = table_rows(lines)
    return [dict(zip(head, r)) for r in rows]


def placeholder(t):
    t = re.sub(r"（[^（）]*）$", "", t.strip())  # 去掉结尾的来源注记，如“待填（Pam）”
    return not t or (t.startswith("<") and t.endswith(">")) or t in ("待填", "…")


def parse_progress(path):
    """从任务的 PROGRESS.md 读取：目标、截止、状态、▶ 下一步、卡点数、最近日志日期。"""
    if not path or not path.exists():
        return {"missing": True}
    text = path.read_text(encoding="utf-8")
    base = path.parent
    info = {"missing": False, "href": "file://" + quote(str(path))}
    for key in ("目标", "截止", "状态"):
        m = re.search(key + r"：([^｜\n]*)", text)
        v = m.group(1).strip() if m else ""
        info[key] = "" if placeholder(v) else v
    secs = sections(text)
    _, nl = find_section(secs, "▶ 下一步")
    nxt = " ".join(l.strip() for l in nl if l.strip() and not placeholder(l))
    info["next"] = inline(nxt, base) if nxt else ""
    _, bl = find_section(secs, "卡点")
    info["blockers"] = sum(1 for l in bl if l.strip().startswith("- ") and not placeholder(l.strip()[2:]))
    log = log_lines(path, secs)
    m = next((re.match(r"^### (\d{4}-\d{2}-\d{2})", l) for l in log if l.startswith("### ")), None)
    info["last"] = m.group(1) if m else ""
    info["activity"] = log_activity(log)
    return info


def log_lines(path, secs):
    """日志行：优先同目录的 日志.md（v3），否则 PROGRESS.md 的「日志」一节（v1/v2）。"""
    f = path.parent / "日志.md"
    if f.exists():
        return f.read_text(encoding="utf-8").splitlines()
    return find_section(secs, "日志")[1]


def log_activity(lines):
    """日志中每个 `### YYYY-MM-DD` 下的条目数 → {日期: 条数}，用于近 14 天活跃度格子。"""
    act, day = {}, None
    for l in lines:
        m = re.match(r"^### (\d{4}-\d{2}-\d{2})", l)
        if m:
            day = m.group(1)
            act.setdefault(day, 0)
        elif day and l.lstrip().startswith("- "):
            act[day] += 1
    return act


def parse_now():
    secs = sections(NOW_MD.read_text(encoding="utf-8"))
    base = NOW_MD.parent
    tasks = []
    for r in table_dicts(find_section(secs, "任务")[1]):
        name, path = link_parts(r.get("任务", ""), base)
        cat = r.get("分类", "")
        tasks.append({"name": name, "cat": category(cat), "catName": cat, "level": r.get("等级", ""),
                      "state": r.get("状态", ""), **parse_progress(path)})
    dt_title, dl = find_section(secs, "大方向")
    dirs = [{"name": r.get("方向", ""), "goal": inline(r.get("半年后想要的结果", ""), base),
             "milestones": inline(r.get("关键节点", ""), base), "share": r.get("占比", ""),
             "cat": category(r.get("方向", ""))} for r in table_dicts(dl)]
    return {"tasks": tasks, "dirTitle": dt_title, "dirs": dirs}


def parse_done():
    if not DONE_MD.exists():
        return {"total": 0, "items": []}
    lines = DONE_MD.read_text(encoding="utf-8").splitlines()
    items = []
    for r in table_dicts(lines):
        name, path = link_parts(r.get("任务", ""), DONE_MD.parent)
        items.append({"name": name, "href": "file://" + quote(str(path)) if path else "",
                      "cat": category(r.get("分类", "")), "date": r.get("完成于", ""),
                      "result": inline(r.get("结果", ""), DONE_MD.parent)})
    return {"total": len(items), "items": items[:DONE_SHOW]}


def parse_deadlines():
    """截止文件格式：`## 分组 …` 下的 `- **事项（权重）**：… **YYYY-MM-DD（周X）时间**…`。"""
    if not EXAMS_MD or not EXAMS_MD.exists():
        return []
    final_kw = DL.get("final_keyword", "期末")
    items, course = [], None
    for line in EXAMS_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            course = line[3:].split()[0] if line[3:].strip() else None
            continue
        if not course or (DL_GROUPS and course not in DL_GROUPS) or not line.startswith("- **"):
            continue
        d = re.search(r"\*\*(\d{4}-\d{2}-\d{2})（(周.)）([^*]*)\*\*", line)
        name = re.match(r"- \*\*(.+?)\*\*", line).group(1)
        if not d:
            continue
        w = re.search(r"（(\d+%)）", name)
        title = re.sub(r"（\d+%）", "", name)
        when = d.group(3).strip()
        items.append({"date": d.group(1), "weekday": d.group(2), "time": when,
                      "course": course, "title": title, "weight": w.group(1) if w else "",
                      "final": final_kw in title,
                      "tentative": "暂定" in line, "pending": "待公告" in line})
    items.sort(key=lambda x: x["date"])
    return items


def sources():
    """看板依赖的全部文件：NOW 相关 Markdown、考核安排、模板、本脚本、任务与已完成里链接的 PROGRESS.md。"""
    files = [NOW_MD, TODAY_MD, MONTH_MD, DONE_MD, CONFIG, TEMPLATE, Path(__file__).resolve()]
    if EXAMS_MD:
        files.append(EXAMS_MD)
    for md, base in ((NOW_MD, NOW_MD.parent), (DONE_MD, DONE_MD.parent)):
        if md.exists():
            for r in table_dicts(md.read_text(encoding="utf-8").splitlines()):
                _, path = link_parts(r.get("任务", ""), base)
                if path:
                    files += [path, path.parent / "日志.md"]
    return [f for f in files if f.exists()]


def up_to_date():
    if not OUT.exists():
        return False
    built = OUT.stat().st_mtime
    return all(f.stat().st_mtime <= built for f in sources())


def main():
    if "--if-changed" in sys.argv and up_to_date():
        return
    data = {
        "built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "now": parse_now(),
        "done": parse_done(),
        "today": parse_today(),
        "month": parse_month(),
        "deadlines": parse_deadlines(),
        "catNames": CAT_NAMES,
        "deadlineNote": DL.get("note", ""),
        "deadlineCat": category(DL.get("category", "")) if DL.get("category") else "other",
        "buildCmd": ("python3 " + str(Path(__file__).resolve())
                     + ("" if HERE == CODE else f" --now-dir {HERE}")).replace(str(Path.home()), "~"),
        "sources": {
            "today": "file://" + quote(str(TODAY_MD)),
            "month": "file://" + quote(str(MONTH_MD)),
            "exams": "file://" + quote(str(EXAMS_MD)) if EXAMS_MD else "",
            "now": "file://" + quote(str(NOW_MD)),
            "done": "file://" + quote(str(DONE_MD)),
        },
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", blob), encoding="utf-8")
    print(f"已生成 {OUT}")


if __name__ == "__main__":
    main()
