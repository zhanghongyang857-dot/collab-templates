#!/usr/bin/env python3
"""派活脚本：总管用它向命令行成员派活、续接、停止，并把每次派活写进项目的 collab/运行记录/。

用法（在项目目录或其子目录里运行，或用 --project 指定）：
  dispatch.py start T003 执行 codex "启动语"      新会话派活（先按任务卡和目标流程检查）
  dispatch.py continue T003 执行 "追问或返工"      续接该任务该席位最近的会话
  dispatch.py stop T003-02                         停止一次派活
  dispatch.py stop --all                           停止本项目所有正在跑的派活
  dispatch.py list [--all]                         列出派活（默认只列运行中和最近 10 条）
  dispatch.py show T003-02                         显示一次派活的记录和最终回复
  dispatch.py meet I003 [--round N]                朝会：召集议题「与会」表里的成员开下一轮，发言原文写入议题文件

启动语写 "-" 时从标准输入读取。
派活前三道关，不过就不派（退出码 2）：
  1. 校验 collab/（tools/check.py）没有错误；确需跳过加 --skip-check "理由"。
  2. 硬性条件：有任务卡（临时派活加 --adhoc "理由"）；卡没结束；目标已批准，零散任务有总裁批准记录。这一关不能跳过。
  3. 流程：前面的阶段都通过、席位和人选与流程表和卡头一致；确需偏离加 --override "理由"。
跳过和偏离的理由都记入运行记录，看板标为「偏离」。命令在 CLI 结束时才退出，适合总管以后台命令运行。
只用 Python 标准库。
"""
import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import collab_parse  # noqa: E402  与看板共用的任务卡、目标卡解析
import check as collab_check  # noqa: E402  collab/ 校验，与 Stop hook、看板共用
MEMBERS = HERE / "members.json"  # 没有班子配置时的后备
TEAM_FILE = Path(os.environ.get("COLLAB_TEAM", "~/.collab-team")).expanduser() / "班子.json"
RUNS_DIR = "运行记录"
HEARTBEAT_SECONDS = 5  # 事件密集时，最多每隔几秒写一次记录
ACTION_LIMIT = 100

RUNNING, DONE, FAILED, STOPPED, LOST = "运行中", "完成", "失败", "已中断", "异常结束"


# ---------- 通用 ----------

def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def clip(text: Any, limit: int = ACTION_LIMIT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def find_project(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "collab" / "PROGRESS.md").exists():
            return p
    sys.exit(f"找不到 collab/PROGRESS.md：{start} 及其上级目录都不是协作项目，用 --project 指定")


def runs_dir(project: Path) -> Path:
    d = project / "collab" / RUNS_DIR
    d.mkdir(exist_ok=True)
    return d


def write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_runs(project: Path) -> List[Dict[str, Any]]:
    """读出全部派活记录，按开始时间排序；标为运行中但进程已不在的，视为异常结束（只在返回值里改，不写回）。"""
    d = project / "collab" / RUNS_DIR
    runs = []
    if d.exists():
        for f in d.glob("*.json"):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if r.get("status") == RUNNING and not pid_alive(r.get("pid")):
                r["status"] = LOST
            runs.append(r)
    runs.sort(key=lambda r: (r.get("started") or "", r.get("run") or ""))
    return runs


def members_source() -> Path:
    return TEAM_FILE if TEAM_FILE.exists() else MEMBERS


def load_members() -> Dict[str, Any]:
    """成员来自班子配置（~/.collab-team/班子.json，或 COLLAB_TEAM 指定的目录）；没有时用同目录的 members.json。"""
    if TEAM_FILE.exists():
        team = json.loads(TEAM_FILE.read_text(encoding="utf-8"))
        out = {}
        for x in team.get("成员", []):
            did = x.get("派活名")
            if not did or not x.get("新会话命令"):
                continue
            out[did] = {"aliases": x.get("别名") or [did], "parser": x.get("事件解析", did),
                        "start": x["新会话命令"], "resume": x.get("续接命令", []),
                        "available": str(x.get("可用", "✓"))}
        return out
    data = json.loads(MEMBERS.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def new_run_id(rdir: Path, task: str) -> str:
    """任务编号 + 两位序号；用 O_EXCL 抢占文件，避免同时派活时撞号。"""
    n = 1
    for f in rdir.glob(f"{task}-*.json"):
        m = re.fullmatch(re.escape(task) + r"-(\d+)\.json", f.name)
        if m:
            n = max(n, int(m.group(1)) + 1)
    while True:
        rid = f"{task}-{n:02d}"
        try:
            fd = os.open(rdir / f"{rid}.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return rid
        except FileExistsError:
            n += 1


# ---------- 各成员事件解析：每行事件 → 会话 ID / 最近动作 / 最终回复 / 错误 ----------

def _tool_brief(name: str, inp: Any) -> str:
    if isinstance(inp, dict):
        for k in ("description", "command", "file_path", "path", "pattern", "url", "query"):
            if inp.get(k):
                return f"{name}：{inp[k]}"
    return name


def parse_dsh(ev: Dict[str, Any], out: Dict[str, Any]) -> None:
    t = ev.get("type")
    if t == "session" and ev.get("sessionId"):
        out["session"] = ev["sessionId"]
    elif t == "tool_call":
        out["action"] = _tool_brief(ev.get("tool", "工具"), ev.get("input"))
        out["step"] = True
    elif t == "final":
        out["final"] = ev.get("text", "")
    elif t == "status" and ev.get("phase"):
        out["phase"] = ev["phase"]
    elif t in ("error", "turn_end") and (ev.get("error") or ev.get("message")):
        out["error"] = clip(ev.get("error") or ev.get("message"), 300)


def parse_codex(ev: Dict[str, Any], out: Dict[str, Any]) -> None:
    t = ev.get("type")
    item = ev.get("item") or {}
    it = item.get("type")
    if t == "thread.started" and ev.get("thread_id"):
        out["session"] = ev["thread_id"]
    elif t == "item.started" and it == "command_execution":
        out["action"] = "运行：" + str(item.get("command", ""))
        out["step"] = True
    elif t == "item.completed" and it == "file_change":
        paths = [c.get("path", "") for c in item.get("changes") or []]
        out["action"] = "改文件：" + "、".join(Path(p).name for p in paths if p)
        out["step"] = True
    elif t == "item.started" and it in ("mcp_tool_call", "web_search"):
        out["action"] = _tool_brief(item.get("tool") or it, item.get("arguments") or {"query": item.get("query")})
        out["step"] = True
    elif t == "item.completed" and it == "agent_message":
        out["final"] = item.get("text", "")
    elif t == "turn.failed":
        out["error"] = clip((ev.get("error") or {}).get("message", "turn.failed"), 300)
    elif t == "error":
        out["error"] = clip(ev.get("message", "error"), 300)


def parse_claude(ev: Dict[str, Any], out: Dict[str, Any]) -> None:
    t = ev.get("type")
    if ev.get("session_id") and t in ("system", "result"):
        out["session"] = ev["session_id"]
    if t == "assistant":
        for c in (ev.get("message") or {}).get("content") or []:
            if c.get("type") == "tool_use":
                out["action"] = _tool_brief(c.get("name", "工具"), c.get("input"))
                out["step"] = True
    elif t == "result":
        out["final"] = ev.get("result", "")
        if ev.get("is_error"):
            out["error"] = clip(ev.get("result") or "is_error", 300)


PARSERS = {"dsh": parse_dsh, "codex": parse_codex, "claude": parse_claude}


# ---------- token 汇总：每次派活记一行（输入、缓存、输出、调用次数、最后上下文）；算错只记未知，不影响派活 ----------

def _n(v: Any) -> int:
    return v if isinstance(v, int) else 0


def usage_dsh(ev: Dict[str, Any], acc: Dict[str, Any]) -> None:
    """每次模型调用结束（status/step_end）带一次 usage，逐次相加；totalTokens 即这次调用的上下文。"""
    u = ev.get("usage") if ev.get("type") == "status" else None
    if isinstance(u, dict):
        acc["calls"] = acc.get("calls", 0) + 1
        acc["input"] = acc.get("input", 0) + _n(u.get("inputTokens")) + _n(u.get("cacheWriteTokens"))
        acc["cached"] = acc.get("cached", 0) + _n(u.get("cacheReadTokens"))
        acc["output"] = acc.get("output", 0) + _n(u.get("outputTokens"))
        acc["context"] = _n(u.get("totalTokens"))


def usage_codex(ev: Dict[str, Any], acc: Dict[str, Any]) -> None:
    """turn.completed 给的是整个会话的累计数，续接时减去上一次派活记下的累计（_base）。"""
    u = ev.get("usage") if ev.get("type") == "turn.completed" else None
    if isinstance(u, dict):
        cached = _n(u.get("cached_input_tokens"))
        cum = {"input": _n(u.get("input_tokens")) - cached, "cached": cached, "output": _n(u.get("output_tokens"))}
        base = acc.get("_base") or {}
        acc["session_cum"] = cum
        for k, v in cum.items():
            acc[k] = max(v - _n(base.get(k)), 0)


def usage_claude(ev: Dict[str, Any], acc: Dict[str, Any]) -> None:
    u = ev.get("usage") if ev.get("type") == "result" else None
    if isinstance(u, dict):
        acc["input"] = _n(u.get("input_tokens")) + _n(u.get("cache_creation_input_tokens"))
        acc["cached"] = _n(u.get("cache_read_input_tokens"))
        acc["output"] = _n(u.get("output_tokens"))
        if isinstance(ev.get("num_turns"), int):
            acc["calls"] = ev["num_turns"]
        it = (u.get("iterations") or [None])[-1]
        if isinstance(it, dict):
            acc["context"] = sum(_n(it.get(k)) for k in ("input_tokens", "cache_read_input_tokens",
                                                          "cache_creation_input_tokens", "output_tokens"))
        if isinstance(ev.get("total_cost_usd"), (int, float)):
            acc["cost_usd"] = round(ev["total_cost_usd"], 4)


USAGE = {"dsh": usage_dsh, "codex": usage_codex, "claude": usage_claude}


def finish_tokens(acc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """去掉内部字段、补总量；没有任何用量时返回 None（记为未知）。"""
    out = {k: v for k, v in acc.items() if not k.startswith("_")}
    if not any(k in out for k in ("input", "cached", "output")):
        return None
    out["total"] = out.get("input", 0) + out.get("cached", 0) + out.get("output", 0)
    return out


def fmt_tokens(t: Optional[Dict[str, Any]]) -> str:
    """「49万 · 缓存89%」；没有数据写「—」。"""
    if not t or not t.get("total"):
        return "—"
    n = t["total"]
    s = f"{n / 10000:.1f}万".replace(".0万", "万") if n >= 10000 else str(n)
    return f"{s} · 缓存{round(100 * t.get('cached', 0) / n)}%" if t.get("cached") else s


# ---------- 运行一次派活 ----------

class Runner:
    def __init__(self, project: Path, rec: Dict[str, Any], argv: List[str], parser: str,
                 token_base: Optional[Dict[str, Any]] = None):
        self.project = project
        self.rdir = runs_dir(project)
        self.rec = rec
        self.argv = argv
        self.parse = PARSERS[parser]
        self.usage = USAGE.get(parser)
        self.tokens: Dict[str, Any] = {"_base": token_base} if token_base else {}
        self.token_error = False
        self.meta = self.rdir / f"{rec['run']}.json"
        self.events = self.rdir / f"{rec['run']}.jsonl"
        self.errfile = self.rdir / f"{rec['run']}.err"
        self.child: Optional[subprocess.Popen] = None
        self.stopping = False
        self.dirty = False
        self.lock = threading.Lock()
        self.done = threading.Event()

    def save(self, force: bool = False) -> None:
        """force 立即写；否则只标记，由 flusher 每隔 HEARTBEAT_SECONDS 写一次。"""
        with self.lock:
            if force:
                write_json(self.meta, self.rec)
                self.dirty = False
            else:
                self.dirty = True

    def flusher(self) -> None:
        while not self.done.wait(HEARTBEAT_SECONDS):
            with self.lock:
                if self.dirty:
                    write_json(self.meta, self.rec)
                    self.dirty = False

    def on_signal(self, signum, _frame) -> None:
        self.stopping = True
        if self.child and self.child.poll() is None:
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.on_signal)
        signal.signal(signal.SIGINT, self.on_signal)
        signal.signal(signal.SIGHUP, self.on_signal)
        self.rec.update(status=RUNNING, pid=os.getpid(), started=now_iso(), last_at=now_iso())
        self.save(force=True)
        print(f"派活 {self.rec['run']}：{self.rec['member']} 坐「{self.rec['seat']}」，任务 {self.rec['task']}"
              f"{'，续接 ' + self.rec['session'] if self.rec.get('session') else ''}", flush=True)
        try:
            err = self.errfile.open("w", encoding="utf-8")
            self.child = subprocess.Popen(self.argv, cwd=self.project, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=err,
                                          start_new_session=True, text=True, encoding="utf-8",
                                          errors="replace", bufsize=1)
        except OSError as e:
            self.rec.update(status=FAILED, ended=now_iso(), error=f"无法启动：{e}")
            self.save(force=True)
            print(self.rec["error"], file=sys.stderr)
            return 127
        self.rec["child_pid"] = self.child.pid
        self.save(force=True)
        threading.Thread(target=self.flusher, daemon=True).start()

        with self.events.open("a", encoding="utf-8") as log:
            for line in self.child.stdout:
                log.write(line)
                log.flush()
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                out: Dict[str, Any] = {}
                self.parse(ev, out)
                self.rec["last_at"] = now_iso()
                if out.get("session") and not self.rec.get("session"):
                    self.rec["session"] = out["session"]
                    self.save(force=True)
                    print(f"会话 {out['session']}", flush=True)
                if out.get("action"):
                    self.rec["last_action"] = clip(out["action"])
                if out.get("step"):
                    self.rec["steps"] = self.rec.get("steps", 0) + 1
                if "final" in out:
                    self.rec["final"] = out["final"]
                if out.get("error"):
                    self.rec["error"] = out["error"]
                if self.usage and not self.token_error:
                    try:
                        self.usage(ev, self.tokens)
                        t = finish_tokens(self.tokens)
                        if t:
                            self.rec["tokens"] = t
                    except Exception:  # 统计出错只记未知，派活照常
                        self.token_error = True
                        self.rec.pop("tokens", None)
                self.save()

        code = self.child.wait()
        self.done.set()
        err.close()
        stderr = self.errfile.read_text(encoding="utf-8", errors="replace")
        if not stderr.strip():
            self.errfile.unlink()
        last = self.rdir / f"{self.rec['run']}.last.txt"
        if last.exists():
            text = last.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                self.rec["final"] = text
            last.unlink()
        if self.stopping:
            status = STOPPED
        elif code == 0 and not (self.rec.get("error") and not self.rec.get("final")):
            status = DONE
        else:
            status = FAILED
            if not self.rec.get("error"):
                self.rec["error"] = clip(stderr.strip().splitlines()[-1] if stderr.strip() else f"退出码 {code}", 300)
        self.rec.update(status=status, ended=now_iso(), exit_code=code, last_at=now_iso())
        self.rec.pop("child_pid", None)
        self.save(force=True)

        print(f"\n{self.rec['run']} {status}（退出码 {code}，{self.rec.get('steps', 0)} 步）")
        if self.rec.get("session"):
            print(f"会话 {self.rec['session']}")
        if self.rec.get("error") and status != DONE:
            print(f"错误：{self.rec['error']}")
        if self.rec.get("final"):
            print("\n--- 最终回复 ---\n" + self.rec["final"])
        return 0 if status == DONE else (130 if status == STOPPED else 1)


def build_argv(member_cfg: Dict[str, Any], mode: str, prompt: str, session: str, last: Path) -> List[str]:
    vals = {"prompt": prompt, "session": session, "last": str(last)}
    return [re.sub(r"\{(prompt|session|last)\}", lambda m: vals[m.group(1)], a) for a in member_cfg[mode]]


def read_prompt(p: str) -> str:
    text = sys.stdin.read() if p == "-" else p
    if not text.strip():
        sys.exit("启动语为空")
    return text


def check_gate(project: Path, skip: str) -> Optional[str]:
    """第一关：collab/ 校验没有错误。返回记入运行记录的偏离说明；不通过时退出。"""
    errs = collab_check.errors(collab_check.check(project))
    if not errs:
        return None
    if not skip:
        print("未派活：collab/ 校验没通过，先按协议改好：", file=sys.stderr)
        print(collab_check.fmt(errs), file=sys.stderr)
        print('确定是校验器误报才加 --skip-check "理由"（记入运行记录，看板标为偏离）。', file=sys.stderr)
        sys.exit(2)
    print(f"按 --skip-check 跳过校验（{len(errs)} 个错误）：{skip}")
    return f"跳过校验（{len(errs)} 个错误）"


def start_run(project: Path, task: str, seat: str, member: str, prompt: str,
              session: Optional[str] = None, parent: Optional[str] = None, override: str = "",
              adhoc: str = "", skip_check: str = "") -> int:
    members = load_members()
    if member not in members:
        sys.exit(f"未知成员 {member}，可用：{'、'.join(members)}（在 {members_source()} 配置）")
    cfg = members[member]
    if not cfg.get("available", "✓").startswith("✓"):
        print(f"提醒：班子配置里 {member} 的状态是「{cfg['available']}」，可能会失败", file=sys.stderr)
    if cfg.get("parser") not in PARSERS:
        sys.exit(f"{member} 的事件解析「{cfg.get('parser')}」不认识，可用：{'、'.join(PARSERS)}")
    skipped = check_gate(project, skip_check)
    collab = project / "collab"
    has_card = any(t["id"] == task for t in collab_parse.load_tasks(collab))
    if not has_card and not adhoc:
        print(f"未派活：{task} 没有任务卡。先建卡（协议「记录规范 · 编号」），总裁批准后再派；"
              '临时派活（不属于任何任务）加 --adhoc "理由"。', file=sys.stderr)
        return 2
    blocks = collab_parse.dispatch_blocks(collab, task)
    if blocks:
        print(f"未派活：{task}", file=sys.stderr)
        for b in blocks:
            print(f"  - {b}", file=sys.stderr)
        print("这一关不能用 --override 跳过。", file=sys.stderr)
        return 2
    deviations = collab_parse.check_dispatch(collab, task, seat, member, cfg.get("aliases", [member]))
    if deviations and not override:
        print(f"未派活：{task}「{seat}」{member} 与任务卡或目标流程不符：", file=sys.stderr)
        for d in deviations:
            print(f"  - {d}", file=sys.stderr)
        print('确需偏离就加 --override "理由"，理由会记入运行记录并在看板标为偏离；'
              "改流程本身要总裁批准。", file=sys.stderr)
        return 2
    if deviations:
        print(f"按 --override 偏离流程：{override}")
    reasons = [r for r in (override if deviations else "", f"临时派活：{adhoc}" if adhoc and not has_card else "",
                           f"跳过校验：{skip_check}" if skipped else "") if r]
    if adhoc and not has_card:
        deviations = deviations + ["没有任务卡（临时派活）"]
    if skipped:
        deviations = deviations + [skipped]
    rdir = runs_dir(project)
    rid = new_run_id(rdir, task)
    mode = "resume" if session else "start"
    argv = build_argv(cfg, mode, prompt, session or "", rdir / f"{rid}.last.txt")
    rec = {"run": rid, "task": task, "seat": seat, "member": member, "mode": "续接" if session else "新会话",
           "session": session or "", "continues": parent or "", "prompt": prompt, "steps": 0,
           "last_action": "", "final": "", "error": "",
           "deviations": deviations, "override": "；".join(reasons),
           "adhoc": adhoc if not has_card else ""}
    base = None
    if parent:  # Codex 的用量是会话累计，续接时要减去上一次的累计
        prev = next((r for r in load_runs(project) if r.get("run") == parent), None)
        base = ((prev or {}).get("tokens") or {}).get("session_cum")
    return Runner(project, rec, argv, cfg["parser"], token_base=base).run()


# ---------- 子命令 ----------

def cmd_start(a, project: Path) -> int:
    return start_run(project, a.task, a.seat, a.member, read_prompt(a.prompt), override=a.override or "",
                     adhoc=a.adhoc or "", skip_check=a.skip_check or "")


def cmd_continue(a, project: Path) -> int:
    runs = load_runs(project)
    if a.run:
        base = next((r for r in runs if r.get("run") == a.run), None)
        if not base:
            sys.exit(f"找不到派活 {a.run}")
    else:
        cands = [r for r in runs if r.get("task") == a.task and r.get("seat") == a.seat and r.get("session")]
        if not cands:
            sys.exit(f"{a.task} 的「{a.seat}」席位还没有可续接的会话，先用 start 派活")
        base = cands[-1]
    if base.get("status") == RUNNING:
        sys.exit(f"{base['run']} 还在运行，先等它结束或 stop 它")
    if not base.get("session"):
        sys.exit(f"{base['run']} 没有记录会话 ID，无法续接")
    return start_run(project, base["task"], base["seat"], base["member"], read_prompt(a.prompt),
                     session=base["session"], parent=base["run"], override=a.override or "",
                     adhoc=base.get("adhoc") or "", skip_check=a.skip_check or "")


def _stop_one(project: Path, r: Dict[str, Any]) -> str:
    meta = project / "collab" / RUNS_DIR / f"{r['run']}.json"
    pid, child = r.get("pid"), r.get("child_pid")
    if pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            time.sleep(0.1)
            if not pid_alive(pid):
                break
    if child and pid_alive(child):
        try:
            os.killpg(child, signal.SIGTERM)
        except ProcessLookupError:
            pass
    cur = json.loads(meta.read_text(encoding="utf-8"))
    if cur.get("status") == RUNNING:  # 派活进程没能自己收尾，替它记上
        cur.update(status=STOPPED, ended=now_iso())
        cur.pop("child_pid", None)
        write_json(meta, cur)
    return f"{r['run']} 已停止（{r['member']}，会话 {cur.get('session') or '无'}，可用 continue 续接）"


def cmd_stop(a, project: Path) -> int:
    runs = load_runs(project)
    if a.all:
        targets = [r for r in runs if r.get("status") == RUNNING]
        if not targets:
            print("没有正在运行的派活")
    else:
        if not a.run:
            sys.exit("指定要停的派活编号，或用 --all")
        targets = [r for r in runs if r.get("run") == a.run]
        if not targets:
            sys.exit(f"找不到派活 {a.run}")
        if targets[0].get("status") != RUNNING:
            print(f"{a.run} 当前状态是 {targets[0].get('status')}，不需要停")
            return 0
    for r in targets:
        print(_stop_one(project, r))
    return 0


def _ago(iso: str) -> str:
    try:
        s = int((dt.datetime.now().astimezone() - dt.datetime.fromisoformat(iso)).total_seconds())
    except (TypeError, ValueError):
        return ""
    return f"{s}秒前" if s < 120 else f"{s // 60}分钟前" if s < 7200 else f"{s // 3600}小时前"


def cmd_list(a, project: Path) -> int:
    runs = load_runs(project)
    if not a.all:
        running = [r for r in runs if r.get("status") == RUNNING]
        rest = [r for r in runs if r.get("status") != RUNNING][-10:]
        runs = rest + running
    if not runs:
        print("还没有派活记录")
        return 0
    for r in runs:
        tail = f"最近：{r.get('last_action') or '—'}（{_ago(r.get('last_at'))}）" if r.get("status") == RUNNING \
            else (f"错误：{clip(r.get('error'), 60)}" if r.get("status") in (FAILED, LOST) and r.get("error") else "")
        print(f"{r['run']:<10} {r.get('status', ''):<5} {r.get('seat', ''):<3} {r.get('member', ''):<8} "
              f"{(r.get('started') or '')[5:16].replace('T', ' ')}  {r.get('steps', 0)}步  "
              f"{fmt_tokens(r.get('tokens')):<14} {tail}")
    return 0


def cmd_show(a, project: Path) -> int:
    r = next((x for x in load_runs(project) if x.get("run") == a.run), None)
    if not r:
        sys.exit(f"找不到派活 {a.run}")
    for k in ("run", "task", "seat", "member", "mode", "session", "continues", "status", "started", "ended",
              "steps", "last_action", "error", "override", "deviations"):
        if r.get(k) not in (None, ""):
            print(f"{k}: {r[k]}")
    tk = r.get("tokens")
    if tk:
        print(f"tokens: {fmt_tokens(tk)}（输入 {tk.get('input', 0)}，缓存 {tk.get('cached', 0)}，输出 {tk.get('output', 0)}"
              + (f"，调用 {tk['calls']} 次" if tk.get("calls") else "")
              + (f"，最后上下文 {tk['context']}" if tk.get("context") else "")
              + (f"，约 {tk['cost_usd']} 美元" if tk.get("cost_usd") else "") + "）")
    print("\n--- 启动语 ---\n" + r.get("prompt", ""))
    if r.get("final"):
        print("\n--- 最终回复 ---\n" + r["final"])
    return 0


def cmd_tokens(a, project: Path) -> int:
    """从各次派活的原始事件（.jsonl）补算 token；默认只补还没有的，--force 全部重算。按开始时间顺序算，续接能接上前一次的累计。"""
    members = load_members()
    rdir = runs_dir(project)
    runs = [r for r in load_runs(project) if r.get("status") != RUNNING]
    by_id = {r["run"]: r for r in runs}
    n = 0
    for r in sorted(runs, key=lambda x: (x.get("started") or "", x.get("run") or "")):
        if r.get("tokens") and not a.force:
            continue
        parser = (members.get(r.get("member")) or {}).get("parser")
        f = rdir / f"{r['run']}.jsonl"
        if parser not in USAGE or not f.exists():
            continue
        base = ((by_id.get(r.get("continues")) or {}).get("tokens") or {}).get("session_cum")
        acc: Dict[str, Any] = {"_base": base} if base else {}
        try:
            for line in f.open(encoding="utf-8", errors="replace"):
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    USAGE[parser](ev, acc)
            t = finish_tokens(acc)
        except Exception as e:
            print(f"{r['run']}：算不出（{e}），跳过", file=sys.stderr)
            continue
        if not t:
            continue
        meta = rdir / f"{r['run']}.json"
        rec = json.loads(meta.read_text(encoding="utf-8"))
        rec["tokens"] = r["tokens"] = t
        write_json(meta, rec)
        n += 1
        print(f"{r['run']:<10} {r.get('member', ''):<8} {fmt_tokens(t)}")
    print(f"补算 {n} 条")
    return 0


# ---------- 朝会 ----------

MEET_LIMIT = 400


def resolve_member(name: str, members: Dict[str, Any]) -> Optional[str]:
    n = name.strip().lower()
    if n in members:
        return n
    return next((k for k, v in members.items() if any(a.lower() in n for a in v.get("aliases", [k]))), None)


def _section_text(path: Path, prefix: str) -> str:
    secs = collab_parse.h2_sections(collab_parse.strip_comments(path.read_text(encoding="utf-8")))
    s = next((x for x in secs if x["title"].startswith(prefix)), None)
    return "\n".join(l for l in s["lines"] if not l.startswith(">")).strip() if s else ""


def _meet_prompt(issue: Dict[str, Any], path: Path, rnd: int, me: int) -> str:
    att = issue["attendees"]
    mine = att[me]
    others = "；".join(f"{a['member']}（{a['view'] or '未指定视角'}）" for i, a in enumerate(att) if i != me) or "无"
    if rnd == 1:
        return f"""你受邀参加朝会，议题 {issue['id']}「{issue['title']}」。当前工作目录是项目根目录，协作文件在 collab/。
你的视角：{mine['view'] or '未指定，按你的专业判断'}。其他与会者：{others}。第 1 轮各自独立发言，你看不到他们的观点。
可以打开下面列出的参考资料；不要修改任何文件。

## 问题
{_section_text(path, '问题')}

请用中文回答，不超过 {MEET_LIMIT} 字，按以下结构：
- 结论：你主张的做法（一句话）
- 理由：最重要的 2–3 条依据
- 风险与代价：
- 还需要的信息：（没有就写「无」）"""
    prev = next((r for r in issue["rounds"] if r["no"] == rnd - 1), {"speeches": []})
    speeches = "\n\n".join(f"### {s['who']}\n{s['text']}" for s in prev["speeches"])
    return f"""朝会 {issue['id']}「{issue['title']}」第 {rnd} 轮。你的视角仍是：{mine['view'] or '未指定'}。
下面是第 {rnd - 1} 轮全部发言（含你自己的）：

{speeches}

请回应分歧：同意哪些、不同意哪些及理由，是否修改你的结论。不要修改任何文件。中文，不超过 {MEET_LIMIT} 字，按以下结构：
- 结论（是否修改）：
- 回应：
- 仍存在的分歧：（没有就写「无」）"""


def _write_round(path: Path, rnd: int, blocks: List[str], state: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = next((i for i, l in enumerate(lines) if l.startswith("## ") and collab_parse.ROUND_RE.match(l[3:].strip())
                and int(collab_parse.ROUND_RE.match(l[3:].strip()).group(1)) == rnd), None)
    if idx is None:  # 没有这一轮的标题：插在「总结」前（或文末）
        at = next((i for i, l in enumerate(lines) if l.startswith("## 总结")), len(lines))
        lines[at:at] = [f"## 第{rnd}轮", ""]
        idx = at
    end = next((i for i in range(idx + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > idx + 1 and not lines[end - 1].strip():
        end -= 1
    body = []
    for b in blocks:
        body += ["", *b.splitlines()]
    lines[end:end] = body + [""]
    text = "\n".join(lines) + "\n"
    text = re.sub(r"(?m)^(状态：)[^｜\n]*", lambda m: m.group(1) + state + " ", text, count=1)
    path.write_text(text, encoding="utf-8")


def cmd_meet(a, project: Path) -> int:
    check_gate(project, a.skip_check or "")
    collab = project / "collab"
    path = collab_parse.find_issue(collab, a.issue)
    if not path:
        sys.exit(f"找不到议题 {a.issue}（collab/议题/{a.issue}-*.md）")
    issue = collab_parse.parse_issue(path)
    if issue["stage"] in ("待裁决", "已裁决"):
        sys.exit(f"{a.issue} 已{issue['stage']}，不再开会")
    if not issue["attendees"]:
        sys.exit(f"{a.issue} 的「与会」表还没有成员")
    rnd = a.round or issue["roundsDone"] + 1
    if rnd > issue["maxRounds"]:
        sys.exit(f"{a.issue} 已开 {issue['roundsDone']} 轮，到了上限 {issue['maxRounds']} 轮；请写总结")
    if rnd != issue["roundsDone"] + 1:
        sys.exit(f"{a.issue} 已完成 {issue['roundsDone']} 轮，下一轮应是第 {issue['roundsDone'] + 1} 轮")
    members = load_members()
    resolved = []
    for at in issue["attendees"]:
        m = resolve_member(at["member"], members)
        if not m:
            sys.exit(f"与会成员「{at['member']}」不在 {members_source()} 里")
        resolved.append(m)

    started = now_iso()
    procs = []
    print(f"朝会 {a.issue} 第 {rnd} 轮：{'、'.join(resolved)}，并行进行", flush=True)
    for i, m in enumerate(resolved):
        seat = f"朝会{i + 1}"
        base = [sys.executable, str(Path(__file__).resolve()), "--project", str(project)]
        argv = base + (["start", a.issue, seat, m, "-"] if rnd == 1 else ["continue", a.issue, seat, "-"])
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        p.stdin.write(_meet_prompt(issue, path, rnd, i))
        p.stdin.close()
        procs.append((i, m, seat, p))

    stopping = {"v": False}

    def forward(signum, _frame):
        stopping["v"] = True
        for *_, p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
    for sg in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sg, forward)

    outputs = {}
    for i, m, seat, p in procs:
        outputs[i] = p.stdout.read()
        p.wait()
    if stopping["v"]:
        print("朝会被停止，本轮发言未写入议题文件；用 meet 重新开这一轮前先确认各会话状态", file=sys.stderr)
        return 130

    runs = load_runs(project)
    blocks, report = [], []
    for i, m, seat, p in procs:
        r = next((x for x in reversed(runs) if x.get("task") == a.issue and x.get("seat") == seat
                  and (x.get("started") or "") >= started), None)
        view = issue["attendees"][i]["view"]
        head = f"### {m}{' · ' + view if view else ''} · {r['run'] if r else '无记录'}"
        if r and r.get("status") == DONE and r.get("final"):
            text = re.sub(r"(?m)^(#+)", lambda mm: "\\" + mm.group(1), r["final"].strip())
            blocks.append(head + "\n\n" + text)
            report.append(f"{m}：已发言（{r['run']}）")
        else:
            why = (r or {}).get("error") or (r or {}).get("status") or outputs.get(i, "").strip()[-200:]
            blocks.append(head + "\n\n（未发言：" + clip(why, 200) + "）")
            report.append(f"{m}：未发言（{clip(why, 80)}）")
    _write_round(path, rnd, blocks, f"第{rnd}轮完成")
    print("\n".join(report))
    print(f"\n第 {rnd} 轮发言已写入 {path.relative_to(project)}")
    if rnd < issue["maxRounds"]:
        print("下一步：总管读发言，仍有实质分歧就 meet 开下一轮，否则写「总结」。")
    else:
        print("下一步：已到轮数上限，总管写「总结」（共识、争议、建议），并在「待你决定」加一行。")
    for b in blocks:
        print("\n" + b)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="dispatch.py", description="向命令行成员派活并记录")
    ap.add_argument("--project", help="项目目录（默认从当前目录向上找 collab/PROGRESS.md）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start", help="新会话派活")
    p.add_argument("task"); p.add_argument("seat"); p.add_argument("member"); p.add_argument("prompt")
    p.add_argument("--override", help="与任务卡或目标流程不符时仍要派，写明理由")
    p.add_argument("--adhoc", help="没有任务卡的临时派活，写明理由")
    p.add_argument("--skip-check", help="collab/ 校验有错仍要派（只用于校验器误报），写明理由")
    p = sub.add_parser("continue", help="续接最近的会话")
    p.add_argument("task"); p.add_argument("seat"); p.add_argument("prompt")
    p.add_argument("--run", help="续接指定派活的会话，而不是该席位最近的一次")
    p.add_argument("--override", help="与任务卡或目标流程不符时仍要续接，写明理由")
    p.add_argument("--skip-check", help="collab/ 校验有错仍要续接（只用于校验器误报），写明理由")
    p = sub.add_parser("stop", help="停止派活")
    p.add_argument("run", nargs="?"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("list", help="列出派活")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("show", help="显示一次派活")
    p.add_argument("run")
    p = sub.add_parser("tokens", help="从原始事件补算各次派活的 token")
    p.add_argument("--force", action="store_true", help="已有的也重算")
    p = sub.add_parser("meet", help="朝会：召集议题与会者开下一轮")
    p.add_argument("issue"); p.add_argument("--round", type=int, help="指定轮次（只能是下一轮）")
    p.add_argument("--skip-check", help="collab/ 校验有错仍要开会（只用于校验器误报），写明理由")
    a = ap.parse_args(argv)
    project = Path(a.project).expanduser().resolve() if a.project else find_project(Path.cwd())
    if not (project / "collab" / "PROGRESS.md").exists():
        sys.exit(f"{project} 下没有 collab/PROGRESS.md")
    return {"start": cmd_start, "continue": cmd_continue, "stop": cmd_stop,
            "list": cmd_list, "show": cmd_show, "meet": cmd_meet, "tokens": cmd_tokens}[a.cmd](a, project)


if __name__ == "__main__":
    sys.exit(main())
