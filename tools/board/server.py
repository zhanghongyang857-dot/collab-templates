#!/usr/bin/env python3
"""看板网页服务：NOW 页（复用模板里的 build.py 读 NOW 数据目录，生成到临时文件）和各项目的御前看板页。

用法：python3 tools/board/server.py [--port 3100] [--now-dir ~/Collab/NOW] [--project 目录 ...]
只读：不写 NOW、各项目 collab/ 或运行记录；唯一的动作是 /api/open 用系统默认应用打开本机文件。
只用 Python 标准库，只监听 127.0.0.1。
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import collab_parse  # noqa: E402  与派活脚本共用的任务卡、目标卡解析
import check as collab_check  # noqa: E402  collab/ 校验，与派活脚本、Stop hook 共用
STATIC = HERE / "static"
STATIC_FILES = {"board.css": "text/css", "project.js": "text/javascript", "project.html": "text/html",
                "now-inject.css": "text/css", "now-inject.js": "text/javascript"}
RUNS_DIR = "运行记录"
OPEN_SUFFIXES = {".md", ".html", ".txt", ".json", ".jsonl", ".py", ".pdf", ".csv", ".png", ".jpg"}
BUILD_LOCK = threading.Lock()
BUILD_PY = HERE.parent.parent / "templates" / "now" / "NOW" / "build.py"  # NOW 看板代码，template.html 在同目录

ARGS: argparse.Namespace


# ---------- NOW：每次请求重新加载 build.py，让它把看板生成到临时文件 ----------

def load_build():
    # build.py 在导入时按 NOW_DATA_DIR 定数据目录；代码用工具包里模板那份，NOW 目录只放数据
    os.environ["NOW_DATA_DIR"] = str(Path(ARGS.now_dir).expanduser())
    path = BUILD_PY
    spec = importlib.util.spec_from_file_location("now_build", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def now_html() -> str:
    with BUILD_LOCK, tempfile.TemporaryDirectory() as tmp:
        mod = load_build()
        mod.OUT = Path(tmp) / "看板.html"
        argv = sys.argv
        sys.argv = ["build.py"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                mod.main()
        finally:
            sys.argv = argv
        page = mod.OUT.read_text(encoding="utf-8")
    inject = '<link rel="stylesheet" href="/static/now-inject.css"><script src="/static/now-inject.js" defer></script>'
    return page.replace("</head>", inject + "</head>", 1)


# ---------- 项目发现：NOW 任务表和已完成表里链接的 collab/PROGRESS.md，加上 --project ----------

def project_id(root: Path) -> str:
    return hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]


def discover() -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}

    def add(progress: Optional[Path], name: str, source: str, state: str = "") -> None:
        if not progress or progress.name != "PROGRESS.md" or progress.parent.name != "collab" or not progress.exists():
            return
        root = progress.parent.parent
        pid = project_id(root)
        if pid not in found:
            try:
                summary = collab_parse.goal_summary(collab_parse.load_goals(progress.parent),
                                                    collab_parse.load_issues(progress.parent))
            except Exception:
                summary = ""
            found[pid] = {"id": pid, "name": name or root.name, "root": str(root), "progress": str(progress),
                          "source": source, "state": state, "summary": summary}

    try:
        mod = load_build()
        for md, src in ((mod.NOW_MD, "now"), (mod.DONE_MD, "done")):
            if md.exists():
                for r in mod.table_dicts(md.read_text(encoding="utf-8").splitlines()):
                    name, path = mod.link_parts(r.get("任务", ""), md.parent)
                    add(path, name, src, r.get("状态", "") if src == "now" else "已完成")
    except Exception as e:  # NOW 坏了也不影响 --project 指定的项目
        print(f"读取 NOW 失败：{e}", file=sys.stderr)
    for p in ARGS.project or []:
        root = Path(p).expanduser().resolve()
        add(root / "collab" / "PROGRESS.md", "", "extra")
    return list(found.values())


# ---------- 项目数据 ----------

def is_placeholder(t: str) -> bool:
    t = t.strip()
    return not t or (t.startswith("<") and t.endswith(">")) or t in ("待填", "…", "无")


def kv_line(line: str) -> Dict[str, str]:
    return collab_parse.kv_line(line)


def html_task(mod, t: Dict[str, Any], base: Path) -> Dict[str, Any]:
    out = dict(t)
    out["flow"] = [{k: mod.inline(v, base) for k, v in r.items()} for r in t["flow"]]
    out["fields"] = [{"k": f["k"], "v": mod.inline(f["v"], base)} for f in t["fields"]]
    return out


def html_goal(mod, g: Dict[str, Any], by_id: Dict[str, Any], base: Path) -> Dict[str, Any]:
    out = dict(g)
    out["fields"] = [{"k": f["k"], "v": mod.inline(f["v"], base)} for f in g["fields"]]
    stages = []
    for s in g["stages"]:
        st = dict(s)
        for k in ("output", "cond"):
            st[k] = mod.inline(s[k], base)
        st["taskInfo"] = [{"id": i, "title": by_id[i]["title"], "state": by_id[i]["state"], "cls": by_id[i]["cls"],
                           "exec": by_id[i]["head"].get("执行", ""), "archived": by_id[i]["archived"],
                           "flow": by_id[i]["flow"]}
                          for i in s["tasks"] if i in by_id]
        stages.append(st)
    out["stages"] = stages
    return out


def html_issue(mod, i: Dict[str, Any], base: Path) -> Dict[str, Any]:
    out = dict(i)
    for k in ("question", "summary", "ruling"):
        out[k] = [{"k": f["k"], "v": mod.inline(f["v"], base)} for f in i[k]]
    return out


def pid_alive(pid: Any) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError):
        return True
    return True


def parse_runs(collab: Path) -> List[Dict[str, Any]]:
    d = collab / RUNS_DIR
    runs = []
    if not d.exists():
        return runs
    for f in d.glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("status") == "运行中" and not pid_alive(r.get("pid")):
            r["status"] = "异常结束"
        r.pop("pid", None)
        r.pop("child_pid", None)
        runs.append(r)
    runs.sort(key=lambda r: (r.get("started") or "", r.get("run") or ""))
    return runs


def project_data(p: Dict[str, Any]) -> Dict[str, Any]:
    mod = load_build()
    progress = Path(p["progress"])
    collab = progress.parent
    text = collab_parse.strip_comments(progress.read_text(encoding="utf-8"))
    secs = mod.sections(text)
    name = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), p["name"])
    meta_line = next((l for l in text.splitlines() if l.startswith("目标：")), "")

    def bullets(prefix: str) -> List[str]:
        _, ls = mod.find_section(secs, prefix)
        return [mod.inline(l.strip()[2:], collab) for l in ls
                if l.strip().startswith("- ") and not is_placeholder(l.strip()[2:])]

    def plain(prefix: str) -> List[str]:
        _, ls = mod.find_section(secs, prefix)
        out = []
        for l in ls:
            s = l.strip()
            if not s or s.startswith(("|", ">")) or is_placeholder(s):
                continue
            out.append(mod.inline(s[2:] if s.startswith("- ") else s, collab))
        return out

    tasks = collab_parse.load_tasks(collab)
    goals = collab_parse.load_goals(collab, tasks)
    by_id = {t["id"]: t for t in tasks}
    _, task_lines = mod.find_section(secs, "任务")
    done_line = next((l for l in task_lines if l.startswith("已完成")), "")
    decisions = [mod.inline(re.sub(r"^\s*-\s*", "", l.strip()), collab) for t in ("决议", "生效决议")
                 for l in mod.find_section(secs, t)[1] if re.match(r"\s*-?\s*D\d+", l)]
    issues = collab_parse.load_issues(collab)
    archived = sum(1 for t in tasks if t["archived"])
    try:
        checked = collab_check.check(Path(p["root"]))
    except Exception as e:  # 校验器出错不影响看板
        checked = [{"level": "warn", "file": "", "line": None, "msg": f"校验器出错：{e}"}]
    return {
        "id": p["id"], "name": name, "root": p["root"],
        "progressHref": "file://" + quote(str(progress)),
        "meta": {k: v for k, v in kv_line(meta_line).items()},
        "next": " ".join(plain("▶ 下一步")),
        "status": bullets("现状"),
        "pending": plain("卡点"),
        "decisions": decisions,
        "doneLine": mod.inline(done_line, collab),
        "archived": archived,
        "archivedCount": {"goals": sum(1 for g in goals if g["archived"]), "tasks": archived,
                          "issues": sum(1 for i in issues if i["archived"])},
        "cards": [html_task(mod, t, collab) for t in tasks if not t["archived"]],
        "goals": [html_goal(mod, g, by_id, collab) for g in goals if not g["archived"]],
        "issues": [html_issue(mod, i, collab) for i in issues if not i["archived"]],
        "runs": parse_runs(collab),
        "check": checked,
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def archive_data(p: Dict[str, Any]) -> Dict[str, Any]:
    """归档页：只有归档的目标、任务、议题和它们的派活记录；打开时加载一次，不轮询。"""
    mod = load_build()
    progress = Path(p["progress"])
    collab = progress.parent
    text = collab_parse.strip_comments(progress.read_text(encoding="utf-8"))
    name = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), p["name"])
    tasks = collab_parse.load_tasks(collab)
    goals = collab_parse.load_goals(collab, tasks)
    by_id = {t["id"]: t for t in tasks}
    issues = [i for i in collab_parse.load_issues(collab) if i["archived"]]
    cards = [t for t in tasks if t["archived"]]
    ids = {t["id"] for t in cards} | {i["id"] for i in issues}
    return {
        "id": p["id"], "name": name, "root": p["root"], "archive": True,
        "progressHref": "file://" + quote(str(progress)),
        "meta": {}, "pending": [], "decisions": [],
        "cards": [html_task(mod, t, collab) for t in cards],
        "goals": [html_goal(mod, g, by_id, collab) for g in goals if g["archived"]],
        "issues": [html_issue(mod, i, collab) for i in issues],
        "runs": [r for r in parse_runs(collab) if r.get("task") in ids],
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "board/1"

    def log_message(self, fmt, *args):
        if ARGS.verbose:
            super().log_message(fmt, *args)

    def send(self, code: int, body: bytes, ctype: str, csp: bool = True) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if csp:
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def json(self, data: Any, code: int = 200) -> None:
        self.send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path in ("/", "/now"):
                return self.send(200, now_html().encode("utf-8"), "text/html", csp=False)
            if path == "/api/projects":
                return self.json(discover())
            m = re.fullmatch(r"/api/p/([0-9a-f]{8})(/archive)?", path)
            if m:
                p = next((x for x in discover() if x["id"] == m.group(1)), None)
                if not p:
                    return self.json({"error": "找不到项目"}, 404)
                return self.json(archive_data(p) if m.group(2) else project_data(p))
            if re.fullmatch(r"/p/[0-9a-f]{8}(/archive)?", path):
                return self.send(200, (STATIC / "project.html").read_bytes(), "text/html")
            m = re.fullmatch(r"/static/([\w.-]+)", path)
            if m and m.group(1) in STATIC_FILES:
                return self.send(200, (STATIC / m.group(1)).read_bytes(), STATIC_FILES[m.group(1)])
            self.send(404, "找不到页面".encode("utf-8"), "text/plain")
        except Exception as e:
            self.send(500, f"出错：{e}".encode("utf-8"), "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/open" or self.headers.get("X-Board") != "1":
            return self.send(403, b"forbidden", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            target = Path(json.loads(self.rfile.read(n) or b"{}").get("path", "")).expanduser()
        except (ValueError, TypeError):
            return self.json({"error": "参数错误"}, 400)
        home = Path.home().resolve()
        try:
            target = target.resolve()
        except OSError:
            return self.json({"error": "路径无效"}, 400)
        ok = target.exists() and home in target.parents and (target.is_dir() or target.suffix.lower() in OPEN_SUFFIXES)
        if not ok:
            return self.json({"error": f"不允许打开：{target}"}, 400)
        subprocess.Popen(["open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.json({"ok": True})


def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser(description="NOW 与御前看板网页服务")
    ap.add_argument("--port", type=int, default=3100)
    ap.add_argument("--now-dir", default=os.environ.get("COLLAB_NOW_DIR", "~/Collab/NOW"))
    ap.add_argument("--project", action="append", help="额外显示的项目目录（含 collab/PROGRESS.md），可重复")
    ap.add_argument("--verbose", action="store_true")
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print(f"看板：http://127.0.0.1:{ARGS.port}  （Ctrl+C 停止）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
