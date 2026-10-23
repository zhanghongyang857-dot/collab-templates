"""读取项目 collab/ 下的目标卡和任务卡，推出各目标的阶段进度。派活脚本和看板共用。

只读，只用 Python 标准库。返回纯文本（Markdown 原文），由调用方决定怎样显示。
支持小版本（目标.md、任务.md、归档.md）和大版本（目标/、任务/、归档/目标/、归档/任务/）。
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

PLACEHOLDER = re.compile(r"^<.*>$")
# 编号：目标 G001；任务按目标编 G001-T1（目标内从 1 起），不属于目标的零散任务和旧任务用 T001
ID_RE = {"G": r"G\d+(?![\d-])", "T": r"(?:G\d+-)?T\d+"}
TASK_ID = re.compile(ID_RE["T"])
FILE_GLOBS = {"G": ["G*.md"], "T": ["T*.md", "G*-T*.md"]}


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def kv_line(line: str) -> Dict[str, str]:
    """`状态：x ｜ 执行：y` → {状态: x, 执行: y}"""
    out = {}
    for part in line.split("｜"):
        if "：" in part:
            k, v = part.split("：", 1)
            out[k.strip()] = v.strip()
    return out


def tables(lines: List[str]) -> List[List[Dict[str, str]]]:
    """块内所有 Markdown 表格 → [[{表头: 单元格}]]。"""
    out, cur = [], []
    for l in lines + [""]:
        if l.lstrip().startswith("|"):
            cur.append([c.strip() for c in l.strip().strip("|").split("|")])
        elif cur:
            if len(cur) >= 2:
                head = cur[0]
                out.append([dict(zip(head, r)) for r in cur[2:]])
            cur = []
    return out


def fields(lines: List[str]) -> List[Dict[str, str]]:
    out = []
    for l in lines:
        m = re.match(r"- ([^：]{1,12})：(.*)", l)
        if m and m.group(2).strip() and not PLACEHOLDER.match(m.group(2).strip()):
            out.append({"k": m.group(1), "v": m.group(2).strip()})
    return out


def task_class(state: str) -> str:
    s = state or ""
    if s.startswith("通过"):
        return "pass"
    if re.match(r"受阻|未通过", s):
        return "block"
    if s.startswith("需补充"):
        return "rework"
    if s.startswith("待验收"):
        return "review"
    if s.startswith("执行中"):
        return "doing"
    if s.startswith(("已交接", "待派发", "待开始")):
        return "todo"
    return "unknown"


def _card(kind: str, title: str, lines: List[str], file: Path, archived: bool) -> Optional[Dict[str, Any]]:
    m = re.match(r"#+\s*(%s)\s*(.*)" % ID_RE[kind], title)
    if not m:
        return None
    head = next((kv_line(l) for l in lines if l.startswith("状态：")), {})
    card = {"id": m.group(1), "title": m.group(2).strip(), "head": head, "fields": fields(lines),
            "file": str(file), "archived": archived, "tables": tables(lines)}
    return card


def _cards_in(path: Path, kind: str, archived: bool) -> List[Dict[str, Any]]:
    """一个文件里的卡：大版本一文件一卡（# 标题），小版本多卡（## 标题）。"""
    if not path.exists():
        return []
    text = strip_comments(path.read_text(encoding="utf-8"))
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.match(r"#{1,2}\s*%s" % ID_RE[kind], l)]
    out = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        c = _card(kind, lines[i], lines[i + 1:end], path, archived)
        if c:
            out.append(c)
    return out


def _collect(collab: Path, kind: str, single: str, folder: str) -> List[Dict[str, Any]]:
    cards = _cards_in(collab / single, kind, False)
    d = collab / folder
    if d.is_dir():
        for f in sorted({f for g in FILE_GLOBS[kind] for f in d.glob(g)}):
            cards += _cards_in(f, kind, False)
    cards += [c for c in _cards_in(collab / "归档.md", kind, True)]
    ad = collab / "归档" / folder
    if ad.is_dir():
        for f in sorted({f for g in FILE_GLOBS[kind] for f in ad.glob(g)}):
            cards += _cards_in(f, kind, True)
    seen, uniq = set(), []
    for c in cards:  # 同号以先出现的（未归档）为准
        if c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)
    return uniq


def load_tasks(collab: Path) -> List[Dict[str, Any]]:
    tasks = _collect(collab, "T", "任务.md", "任务")
    for t in tasks:
        flow = next((tb for tb in t.pop("tables") if tb and "经办人" in tb[0]), [])
        t["flow"] = flow
        last = flow[-1] if flow else {}
        t["state"] = t["head"].get("状态") or last.get("状态", "")
        if t["archived"] and not t["state"]:
            t["state"] = "通过"
        t["holder"] = last.get("接手人", "")
        pre = re.match(r"(G\d+)-T\d+$", t["id"])
        t["goal"] = pre.group(1) if pre else t["head"].get("目标", "").strip(" —-")
        st = re.search(r"\d+", t["head"].get("阶段", ""))
        t["stage"] = int(st.group()) if st else None
        t["cls"] = task_class(t["state"])
    return tasks


def load_goals(collab: Path, tasks: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    tasks = load_tasks(collab) if tasks is None else tasks
    goals = _collect(collab, "G", "目标.md", "目标")
    by_id = {t["id"]: t for t in tasks}
    for g in goals:
        tbs = g.pop("tables")
        flow = next((tb for tb in tbs if tb and "阶段" in tb[0] and "席位" in tb[0]), [])
        g["state"] = g["head"].get("状态", "")
        done = g["state"].startswith("完成") or g["archived"]
        stages = []
        for row in flow:
            no = re.search(r"\d+", row.get("阶段", ""))
            no = int(no.group()) if no else len(stages) + 1
            ids = TASK_ID.findall(row.get("子任务", ""))
            ids += [t["id"] for t in tasks if t["goal"] == g["id"] and t["stage"] == no and t["id"] not in ids]
            sub = [by_id[i] for i in ids if i in by_id]
            stages.append({"no": no, "seat": row.get("席位", ""), "who": row.get("人选", ""),
                           "output": row.get("产出", ""), "cond": row.get("通过条件", ""),
                           "tasks": ids, "missing": [i for i in ids if i not in by_id],
                           "status": _stage_status(sub, done)})
        g["stages"] = stages
        cur = next((s for s in stages if s["status"] != "通过"), None)
        g["current"] = cur["no"] if cur else None
    return goals


def _stage_status(sub: List[Dict[str, Any]], goal_done: bool) -> str:
    if not sub:
        return "通过" if goal_done else "未开始"
    cls = [t["cls"] for t in sub]
    if all(c == "pass" for c in cls):
        return "通过"
    if "block" in cls:
        return "受阻"
    if "rework" in cls:
        return "返工"
    if all(c in ("review", "pass") for c in cls):
        return "待验收"
    if all(c == "todo" for c in cls):
        return "未开始"
    return "进行中"


def goal_summary(goals: List[Dict[str, Any]], issues: Optional[List[Dict[str, Any]]] = None) -> str:
    """NOW 页用的一行摘要，如「G001 ②/5 · G002 受阻 · I003 待裁决」。只算进行中的目标和待裁决的议题。"""
    circled = "①②③④⑤⑥⑦⑧⑨⑩"
    parts = []
    for g in goals:
        if g["archived"] or not g["state"].startswith("进行中") or not g["stages"]:
            continue
        cur = next((s for s in g["stages"] if s["no"] == g["current"]), None)
        if cur and cur["status"] == "受阻":
            parts.append(f"{g['id']} 受阻")
        elif cur:
            mark = circled[cur["no"] - 1] if 0 < cur["no"] <= len(circled) else str(cur["no"])
            parts.append(f"{g['id']} {mark}/{len(g['stages'])}")
        else:
            parts.append(f"{g['id']} 待批准完成")
    for i in issues or []:
        if i["stage"] == "待裁决" and not i["archived"]:
            parts.append(f"{i['id']} 待裁决")
    return " · ".join(parts)


def check_dispatch(collab: Path, task_id: str, seat: str, member: str, aliases: List[str]) -> List[str]:
    """派活前按任务卡和目标流程检查，返回偏离说明（空表示符合）。没有任务卡的零散派活不检查。"""
    tasks = load_tasks(collab)
    t = next((x for x in tasks if x["id"] == task_id), None)
    if not t:
        return []
    probs = []

    def who_ok(who: str) -> bool:
        w = (who or "").lower()
        return not w.strip(" —-") or any(a.lower() in w for a in aliases)

    if t["archived"] or t["cls"] == "pass":
        probs.append(f"{task_id} 已通过或已归档")
    def head_check(key: str) -> None:
        if not who_ok(t["head"].get(key, "")):
            probs.append(f"任务卡写的{key}者是「{t['head'].get(key)}」，派的是 {member}")

    if not t["goal"]:
        if seat in ("执行", "验收"):
            head_check(seat)
        return probs

    goals = load_goals(collab, tasks)
    g = next((x for x in goals if x["id"] == t["goal"]), None)
    if not g:
        return probs + [f"{task_id} 所属目标 {t['goal']} 找不到"]
    if not g["state"].startswith("进行中"):
        probs.append(f"目标 {g['id']} 状态是「{g['state'] or '未写'}」，还没有批准进行")
    st = next((s for s in g["stages"] if s["no"] == t["stage"]), None)
    if not st:
        return probs + [f"目标 {g['id']} 的流程里没有阶段 {t['stage']}"]
    for s in g["stages"]:
        if s["no"] < st["no"] and s["status"] != "通过":
            probs.append(f"前面的阶段 {s['no']}「{s['seat']}」还没通过（{s['status']}）")
    if seat not in (st["seat"], "验收"):
        probs.append(f"{task_id} 属于阶段 {st['no']}「{st['seat']}」，派的席位是「{seat}」")
    elif seat == st["seat"]:
        if not who_ok(st["who"]):
            probs.append(f"流程表阶段 {st['no']} 的人选是「{st['who']}」，派的是 {member}")
        else:
            head_check("执行")  # 阶段可有多人，以这张卡写的执行者为准
    else:
        head_check("验收")
    return probs


# ---------- 议题（朝会） ----------

ROUND_RE = re.compile(r"第\s*(\d+)\s*轮")


def issue_files(collab: Path) -> List[Path]:
    out = []
    for d, archived in ((collab / "议题", False), (collab / "归档" / "议题", True)):
        if d.is_dir():
            out += [f for f in sorted(d.glob("I*.md"))]
    return out


def find_issue(collab: Path, issue_id: str) -> Optional[Path]:
    return next((f for f in issue_files(collab) if re.match(re.escape(issue_id) + r"(\D|$)", f.stem)), None)


def h2_sections(text: str) -> List[Dict[str, Any]]:
    """按 `## ` 切分：[{title, lines}]，第一项标题为空（卡头部分）。"""
    out = [{"title": "", "lines": []}]
    for l in text.splitlines():
        if l.startswith("## "):
            out.append({"title": l[3:].strip(), "lines": []})
        else:
            out[-1]["lines"].append(l)
    return out


def _filled(v: str) -> bool:
    v = (v or "").strip()
    return bool(v) and not PLACEHOLDER.match(v) and v not in ("→ D00x", "G00x / T00x", "—")


def parse_issue(path: Path) -> Optional[Dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    text = strip_comments(raw)
    lines = text.splitlines()
    title = next((l for l in lines if re.match(r"#\s*I\d+", l)), "")
    m = re.match(r"#\s*(I\d+)\s*(.*)", title)
    if not m:
        return None
    secs = h2_sections(text)
    head = next((kv_line(l) for l in secs[0]["lines"] if l.startswith("状态：")), {})
    issue = {"id": m.group(1), "title": m.group(2).strip(), "head": head, "file": str(path),
             "archived": "归档" in path.parts, "question": [], "attendees": [], "rounds": [],
             "summary": [], "ruling": []}
    for s in secs[1:]:
        t = s["title"]
        if t.startswith("问题"):
            issue["question"] = fields(s["lines"])
        elif t.startswith("与会"):
            tb = next(iter(tables(s["lines"])), [])
            issue["attendees"] = [{"member": r.get("成员", "").strip(), "view": r.get("视角", "").strip()}
                                  for r in tb if r.get("成员", "").strip() and not PLACEHOLDER.match(r.get("成员", "").strip())]
        elif ROUND_RE.match(t):
            no = int(ROUND_RE.match(t).group(1))
            speeches, cur = [], None
            for l in s["lines"]:
                if l.startswith("### "):
                    cur = {"who": l[4:].strip(), "lines": []}
                    speeches.append(cur)
                elif cur is not None:
                    cur["lines"].append(l)
            issue["rounds"].append({"no": no, "speeches": [{"who": x["who"], "text": "\n".join(x["lines"]).strip()}
                                                          for x in speeches]})
        elif t.startswith("总结"):
            issue["summary"] = [f for f in fields(s["lines"]) if _filled(f["v"])]
        elif t.startswith("裁决"):
            issue["ruling"] = [f for f in fields(s["lines"]) if _filled(f["v"])]
    done = [r["no"] for r in issue["rounds"] if r["speeches"]]
    issue["roundsDone"] = max(done) if done else 0
    cap = re.search(r"\d+", head.get("轮数上限", "3"))
    issue["maxRounds"] = min(int(cap.group()) if cap else 3, 3)
    state = head.get("状态", "")
    if issue["ruling"] or state.startswith("已裁决"):
        issue["stage"] = "已裁决"
    elif issue["summary"] or state.startswith("待裁决"):
        issue["stage"] = "待裁决"
    elif issue["roundsDone"]:
        issue["stage"] = f"第{issue['roundsDone']}轮完成"
    else:
        issue["stage"] = "开放"
    return issue


def load_issues(collab: Path) -> List[Dict[str, Any]]:
    out = []
    for f in issue_files(collab):
        try:
            i = parse_issue(f)
        except OSError:
            continue
        if i:
            out.append(i)
    return out
