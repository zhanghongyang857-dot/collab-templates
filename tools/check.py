#!/usr/bin/env python3
"""校验项目 collab/ 是否按协议写：编号、卡头、流转、PROGRESS 表与卡片一致、批准记录、「待你决定」格式。

派活脚本（派活前）、Claude Code 的 Stop hook（每轮结束）和看板（项目页顶部）共用。只读，只用 Python 标准库。

用法：python3 tools/check.py [项目目录]          列出问题；有错误时退出码 1
      python3 tools/check.py [项目目录] --json   JSON 输出
      python3 tools/check.py --hook              作为 Claude Code Stop hook：从标准输入读 hook 数据，
                                                 有错误就阻止本轮结束，把问题交回给 Claude 修
错误（error）会挡住派活和 hook；提醒（warn）只显示。
"""
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collab_parse as cp  # noqa: E402

TASK_STATES = ("已批准", "已交接", "待派发", "待开始", "执行中", "待验收", "通过", "需补充", "受阻", "未通过")
GOAL_STATES = ("待批准", "进行中", "暂停", "完成", "取消")
PEND_KINDS = ("批准框架", "受阻", "裁决", "返工", "授权")
FLOW_COLS = ("日期", "状态", "经办人", "接手人")
GOAL_COLS = ("阶段", "席位", "人选")
MAX_ACTIVE_GOALS = 3


def base_state(s: str) -> str:
    """「受阻：缺登录」→「受阻」；去掉括号注记和链接符号。"""
    s = re.sub(r"[`*\[\]]", "", s or "").strip()
    return re.split(r"[：:（(\s]", s, 1)[0]


def plain(cell: str) -> str:
    """单元格去掉 Markdown 链接和符号：[T001](…) → T001。"""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell or "").replace("`", "").replace("*", "").strip()


def find_project(start: Path) -> Optional[Path]:
    for d in [start, *start.parents]:
        if (d / "collab" / "PROGRESS.md").is_file():
            return d
    return None


class Report:
    def __init__(self, root: Path):
        self.root = root
        self.items: List[Dict[str, Any]] = []
        self._text: Dict[str, List[str]] = {}

    def line_of(self, file: Path, needle: str) -> Optional[int]:
        key = str(file)
        if key not in self._text:
            try:
                self._text[key] = file.read_text(encoding="utf-8").splitlines()
            except OSError:
                self._text[key] = []
        return next((i + 1 for i, l in enumerate(self._text[key]) if needle and needle in l), None)

    def add(self, level: str, file: Path, msg: str, needle: str = "") -> None:
        rel = str(file.relative_to(self.root)) if str(file).startswith(str(self.root)) else str(file)
        self.items.append({"level": level, "file": rel, "line": self.line_of(file, needle), "msg": msg})

    def err(self, file: Path, msg: str, needle: str = "") -> None:
        self.add("error", file, msg, needle)

    def warn(self, file: Path, msg: str, needle: str = "") -> None:
        self.add("warn", file, msg, needle)


def progress_sections(path: Path) -> Dict[str, List[str]]:
    text = cp.strip_comments(path.read_text(encoding="utf-8"))
    return {s["title"]: s["lines"] for s in cp.h2_sections(text) if s["title"]}


def section(secs: Dict[str, List[str]], prefix: str) -> Optional[List[str]]:
    return next((v for k, v in secs.items() if k.startswith(prefix)), None)


def check(project: Path) -> List[Dict[str, Any]]:
    collab = project / "collab"
    prog = collab / "PROGRESS.md"
    rep = Report(project)
    if not prog.is_file():
        rep.err(prog, "没有 collab/PROGRESS.md")
        return rep.items

    tasks = cp.load_tasks(collab)
    goals = cp.load_goals(collab, tasks)
    issues = cp.load_issues(collab)
    task_by = {t["id"]: t for t in tasks}
    goal_by = {g["id"]: g for g in goals}

    # 重复编号
    for kind, name in (("T", "任务"), ("G", "目标")):
        seen: Dict[str, str] = {}
        for c in cp.raw_cards(collab, kind):
            if c["id"] in seen:
                rep.err(Path(c["file"]), f"{name}编号 {c['id']} 重复（另一张在 {Path(seen[c['id']]).name}）", c["id"])
            else:
                seen[c["id"]] = c["file"]

    # 任务卡
    for t in tasks:
        f, tid = Path(t["file"]), t["id"]
        if t["archived"]:
            continue
        head = t["head"]
        if not head:
            rep.err(f, f"{tid} 缺卡头行「状态：… ｜ 执行：… ｜ 验收：…」", tid)
            continue
        st = base_state(head.get("状态", ""))
        if not st:
            rep.err(f, f"{tid} 卡头没有「状态」", tid)
        elif not st.startswith(TASK_STATES):
            rep.err(f, f"{tid} 状态「{head.get('状态')}」不在约定里（{' / '.join(TASK_STATES)}）", tid)
        for k in ("执行", "验收"):
            if not head.get(k, "").strip(" —-"):
                rep.err(f, f"{tid} 卡头没写「{k}」", tid)
        if not t["flow"]:
            rep.err(f, f"{tid} 没有「流转」表（列：{' | '.join(FLOW_COLS)}）", tid)
        else:
            cols = t["flow"][0].keys()
            miss = [c for c in FLOW_COLS if not any(k.startswith(c) for k in cols)]
            if miss:
                rep.err(f, f"{tid} 流转表缺列：{'、'.join(miss)}", tid)
            for r in t["flow"]:
                rs = base_state(r.get("状态", ""))
                if rs and not rs.startswith(TASK_STATES):
                    rep.err(f, f"{tid} 流转里的状态「{r.get('状态')}」不在约定里", r.get("状态", ""))
            last = base_state(t["flow"][-1].get("状态", ""))
            if st and last and st != last:
                rep.err(f, f"{tid} 卡头状态「{st}」和流转最后一行「{last}」不一致", tid)
        started = t["cls"] not in ("todo", "unknown")
        if t["goal"]:
            if re.match(r"G\d+-T\d+$", tid) and not head.get("阶段", "").strip():
                rep.err(f, f"{tid} 属于目标，卡头要写「阶段：n」", tid)
            g = goal_by.get(t["goal"])
            if not g:
                rep.err(f, f"{tid} 所属目标 {t['goal']} 没有目标卡", tid)
            else:
                if t["stage"] is not None and g["stages"] and t["stage"] not in [s["no"] for s in g["stages"]]:
                    rep.err(f, f"{tid} 写的阶段 {t['stage']} 不在 {g['id']} 的流程表里", tid)
                if started and not base_state(g["state"]).startswith(("进行中", "完成")):
                    rep.err(f, f"{tid} 已开始，但目标 {g['id']} 状态是「{g['state'] or '未写'}」，没有批准进行", tid)
        elif started and not cp.approved(t):
            rep.err(f, f"{tid} 是零散任务，已开始执行但没有总裁批准记录"
                       "（流转里加一行经办人「总裁」，或卡头写「批准：总裁 MM-DD …」）", tid)

    # 目标卡
    active = 0
    for g in goals:
        f, gid = Path(g["file"]), g["id"]
        if g["archived"]:
            continue
        st = base_state(g["state"])
        if not st:
            rep.err(f, f"{gid} 卡头没有「状态」", gid)
        elif not st.startswith(GOAL_STATES):
            rep.err(f, f"{gid} 状态「{g['state']}」不在约定里（{' / '.join(GOAL_STATES)}）", gid)
        if st.startswith("进行中"):
            active += 1
        if st.startswith(("进行中", "暂停", "完成")) and not cp.approved(g):
            rep.err(f, f"{gid} 状态是「{st}」，但卡头没有「批准：总裁 MM-DD …」记录", gid)
        if not g["stages"]:
            rep.err(f, f"{gid} 没有流程表（列：{' | '.join(GOAL_COLS)} | 产出 | 通过条件 | 子任务）", gid)
        for s in g["stages"]:
            for m in s["missing"]:
                rep.err(f, f"{gid} 流程表阶段 {s['no']} 列的子任务 {m} 没有任务卡", m)
    if active > MAX_ACTIVE_GOALS:
        rep.err(prog, f"同时进行中的目标有 {active} 个，协议上限 {MAX_ACTIVE_GOALS} 个；多的改「暂停」或留「待批准」")

    # PROGRESS 与卡片一致
    secs = progress_sections(prog)
    gsec = section(secs, "目标")
    if gsec is not None:
        listed = set()
        for tb in cp.tables(gsec):
            for r in tb:
                raw = plain(r.get("编号", ""))
                if not raw or cp.PLACEHOLDER.match(raw):
                    continue
                if not re.fullmatch(r"G\d+", raw):
                    rep.err(prog, f"「目标」表里的「{raw}」不是目标卡编号；目标要写成卡片（大版本 目标/G00x-*.md，小版本 目标.md）",
                            r.get("编号", raw))
                    continue
                listed.add(raw)
                g = goal_by.get(raw)
                if not g:
                    rep.err(prog, f"「目标」表里的 {raw} 没有目标卡", raw)
                elif not g["archived"] and base_state(r.get("状态", "")) != base_state(g["state"]):
                    rep.err(prog, f"「目标」表 {raw} 状态「{plain(r.get('状态', ''))}」和目标卡「{g['state']}」不一致", raw)
        for g in goals:
            if not g["archived"] and g["id"] not in listed:
                rep.err(prog, f"目标 {g['id']} 有卡片，但「目标」表里没有它", "## 目标")
    tsec = section(secs, "任务")
    if tsec is not None:
        listed = set()
        for tb in cp.tables(tsec):
            for r in tb:
                raw = plain(r.get("编号", ""))
                if not raw or cp.PLACEHOLDER.match(raw):
                    continue
                if not cp.TASK_ID.fullmatch(raw):
                    rep.err(prog, f"「任务」表里的「{raw}」不是任务编号（G00x-Tn 或 T00x）", r.get("编号", raw))
                    continue
                listed.add(raw)
                t = task_by.get(raw)
                if not t:
                    rep.err(prog, f"「任务」表里的 {raw} 没有任务卡", raw)
                elif t["archived"]:
                    rep.warn(prog, f"{raw} 已归档，「任务」表里可以删掉这一行", raw)
                elif base_state(r.get("状态", "")) != base_state(t["state"]):
                    rep.err(prog, f"「任务」表 {raw} 状态「{plain(r.get('状态', ''))}」和任务卡「{t['state']}」不一致", raw)
        for t in tasks:
            if not t["archived"] and t["id"] not in listed:
                rep.err(prog, f"任务 {t['id']} 有卡片，但「任务」表里没有它", "## 任务")

    # 待你决定
    psec = section(secs, "卡点")
    ids = set(task_by) | set(goal_by) | {i["id"] for i in issues}
    for l in psec or []:
        s = l.strip()
        if not s.startswith("- ") or cp.PLACEHOLDER.match(s[2:].strip()):
            continue
        f6 = [x.strip() for x in s[2:].split("｜")]
        head = plain(f6[0]).replace("（示例）", "").strip()
        if len(f6) != 6:
            rep.err(prog, f"「待你决定」{head or s[:20]} 有 {len(f6)} 段，约定 6 段："
                          "编号｜类型：要决定什么｜可选方案与代价｜总管建议｜验收意见｜提出者", s[:30])
        if head not in ids:
            rep.err(prog, f"「待你决定」编号「{head}」不是本项目的目标、任务或议题编号", s[:30])
        if len(f6) > 1 and not re.match(r"(%s)[：:]" % "|".join(PEND_KINDS), plain(f6[1])):
            rep.err(prog, f"「待你决定」{head} 第 2 段要以类型开头（{' / '.join(PEND_KINDS)}：）", s[:30])

    # 议题
    for i in issues:
        if not i["archived"] and not i["head"].get("状态"):
            rep.warn(Path(i["file"]), f"{i['id']} 卡头没有「状态」", i["id"])

    # 提醒
    if not any(not g["archived"] for g in goals) and any(not t["archived"] for t in tasks):
        rep.warn(prog, "没有目标卡：看板的流程图只给目标画。属于同一件事的多个步骤，先建 G00x 目标卡并写流程表，再派 G00x-Tn")
    rdir = collab / "运行记录"
    if rdir.is_dir():
        for jf in sorted(rdir.glob("*.json")):
            try:
                r = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            tid = r.get("task", "")
            if cp.TASK_ID.fullmatch(tid or "") and tid not in task_by and not r.get("adhoc"):
                rep.warn(jf, f"派活 {r.get('run')} 的任务 {tid} 没有任务卡")
    return rep.items


def errors(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [x for x in items if x["level"] == "error"]


def fmt(items: List[Dict[str, Any]]) -> str:
    errs, warns = errors(items), [x for x in items if x["level"] == "warn"]
    if not items:
        return "校验通过：collab/ 符合协议"
    out = []
    for title, group, mark in (("错误", errs, "✗"), ("提醒", warns, "!")):
        if group:
            out.append(f"{title} {len(group)} 项：")
            out += [f"  {mark} {x['file']}{':' + str(x['line']) if x['line'] else ''}  {x['msg']}" for x in group]
    return "\n".join(out)


def hook() -> int:
    """Claude Code Stop hook：有错误就阻止结束（同一轮只挡一次，避免死循环）。"""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    if data.get("stop_hook_active"):
        return 0
    project = find_project(Path(data.get("cwd") or ".").resolve())
    if not project:
        return 0
    items = check(project)
    if errors(items):
        reason = ("collab/ 校验没通过，按协议改好再结束（规则见 collab/协议.md 或 PROGRESS 末尾的协作规则；"
                  "改完可运行 python3 " + str(Path(__file__).resolve()) + " 自查）。"
                  "补全格式、补建卡片，不要为了通过校验删掉内容或编造批准；缺的信息（例如总裁是否批准）拿不准就停下来问总裁。\n"
                  + fmt(errors(items)))
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def main(argv: List[str]) -> int:
    if "--hook" in argv:
        return hook()
    args = [a for a in argv if not a.startswith("--")]
    project = find_project(Path(args[0] if args else ".").resolve())
    if not project:
        print("找不到 collab/PROGRESS.md", file=sys.stderr)
        return 2
    items = check(project)
    if "--json" in argv:
        print(json.dumps({"project": str(project), "items": items}, ensure_ascii=False, indent=1))
    else:
        print(fmt(items))
    return 1 if errors(items) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
