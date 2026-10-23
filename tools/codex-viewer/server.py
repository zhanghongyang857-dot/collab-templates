#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex 会话只读看板。

只用 Python 3.9 标准库。读取 Codex 的 state_5.sqlite 和 rollout JSONL，
在 127.0.0.1 上提供一个本地网页。数据目录始终以只读方式打开。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import traceback
from collections import OrderedDict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

APP_NAME = "Codex 会话看板"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_CODEX_HOME = "~/.codex"
DEFAULT_PORT = 3090
MAX_ROW_TEXT = 200_000
CACHE_SIZE = 8
DECOMPRESS_TIMEOUT = 120

NODE_ZSTD_SCRIPT = (
    'const fs=require("fs"),z=require("zlib");'
    'process.stdout.write(z.zstdDecompressSync(fs.readFileSync(process.argv[1])));'
)

KIND_LABELS = {
    "system": "系统",
    "user": "用户",
    "context": "上下文",
    "assistant": "助手",
    "reasoning": "思考",
    "tool": "工具",
    "other": "其他",
}

SKIP_EVENT_TYPES = {"item_completed", "token_usage_record"}
SKIP_RESPONSE_TYPES = {"item_completed", "token_usage_record"}

STATIC_ROUTES = {
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
}

# threads 表在 Codex 不同版本里列会增减，缺的列用 NULL 代替。
THREAD_FIELDS = (
    "id",
    "rollout_path",
    "created_at",
    "updated_at",
    "source",
    "model_provider",
    "cwd",
    "title",
    "tokens_used",
    "archived",
    "archived_at",
    "git_branch",
    "cli_version",
    "first_user_message",
    "agent_nickname",
    "agent_role",
    "agent_path",
    "model",
    "created_at_ms",
    "updated_at_ms",
    "thread_source",
    "preview",
    "name",
    "originator",
    "recency_at_ms",
    "is_pinned",
)


class ViewerError(RuntimeError):
    """可以直接展示给用户的读取错误。"""


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _as_bool(value: Any) -> bool:
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def _to_ms(value: Any) -> Optional[int]:
    """把秒、毫秒或 ISO 时间统一成毫秒。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number if number >= 1_000_000_000_000 else number * 1000)
    if isinstance(value, str):
        text = value.strip()
        try:
            number = float(text)
            return int(number if number >= 1_000_000_000_000 else number * 1000)
        except ValueError:
            pass
        parsed = _parse_iso(text)
        if parsed is not None:
            return int(parsed.timestamp() * 1000)
    return None


def _parse_iso(value: str) -> Optional[datetime]:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed


def _format_ms(value: Optional[int]) -> str:
    if value is None:
        return "—"
    try:
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "—"


def _format_iso(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    parsed = _parse_iso(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else ""


def _clock(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        parsed = _parse_iso(value)
        if parsed is not None:
            return parsed.strftime("%H:%M:%S")
    ms = _to_ms(value)
    if ms is None:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def _format_duration(ms: Optional[int]) -> str:
    if ms is None:
        return ""
    if ms < 1000:
        return "%d 毫秒" % ms
    seconds = ms / 1000.0
    if seconds < 60:
        return "%.1f 秒" % seconds
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return "%d 分 %02d 秒" % (minutes, secs)
    hours, minutes = divmod(minutes, 60)
    return "%d 小时 %02d 分" % (hours, minutes)


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _clip(text: str, limit: int = MAX_ROW_TEXT) -> Tuple[str, bool, int]:
    text = "" if text is None else str(text)
    original = len(text)
    if original <= limit:
        return text, False, original
    return (
        text[:limit] + "\n…（内容过长，仅显示前 %d 字）" % limit,
        True,
        original,
    )


def _row_get(row: sqlite3.Row, name: str, default: Any = None) -> Any:
    try:
        value = row[name]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


# ---------------------------------------------------------------------------
# 来源和会话列表
# ---------------------------------------------------------------------------


def _source_info(raw: Any) -> Tuple[str, str]:
    """返回 (筛选键, 展示标签)。"""
    text = "" if raw is None else str(raw)
    haystack = text.lower()
    if text.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            pieces = []
            for key in ("type", "kind", "name", "source", "originator"):
                value = parsed.get(key)
                if isinstance(value, str):
                    pieces.append(value)
            if pieces:
                haystack = (text + " " + " ".join(pieces)).lower()

    if "subagent" in haystack or "sub_agent" in haystack:
        return "subagent", "子 agent"
    if "vscode" in haystack or "desktop" in haystack or "codex_app" in haystack:
        return "vscode", "桌面版"
    if (
        "exec" in haystack
        or "cli" in haystack
        or "command" in haystack
        or "terminal" in haystack
        or haystack in ("app", "gui")
    ):
        return "exec", "命令行"
    if haystack in ("", "unknown", "none"):
        return "unknown", "未知"
    return haystack, text or "未知"


def _is_subagent_row(row: sqlite3.Row, source_key: str) -> bool:
    if source_key == "subagent":
        return True
    if str(_row_get(row, "thread_source", "") or "").lower() == "subagent":
        return True
    if str(_row_get(row, "agent_path", "") or "").strip():
        return True
    if str(_row_get(row, "agent_nickname", "") or "").strip():
        return True
    role = str(_row_get(row, "agent_role", "") or "").strip().lower()
    if role and role not in ("default", "primary", "main", "user"):
        return True
    return False


def _thread_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    source_key, source_label = _source_info(_row_get(row, "source"))

    updated_ms = _as_int(_row_get(row, "updated_at_ms"))
    if updated_ms is None:
        updated_ms = (_as_int(_row_get(row, "updated_at")) or 0) * 1000
    created_ms = _as_int(_row_get(row, "created_at_ms"))
    if created_ms is None:
        created_ms = (_as_int(_row_get(row, "created_at")) or 0) * 1000

    title = (
        _row_get(row, "title")
        or _row_get(row, "name")
        or _row_get(row, "first_user_message")
        or _row_get(row, "preview")
        or "（无标题）"
    )
    title = " ".join(str(title).split())[:400] or "（无标题）"

    return {
        "id": str(_row_get(row, "id", "")),
        "title": title,
        "source": str(_row_get(row, "source", "") or ""),
        "source_key": source_key,
        "source_label": source_label,
        "cwd": str(_row_get(row, "cwd", "") or ""),
        "created_at": created_ms,
        "updated_at": updated_ms,
        "created_at_display": _format_ms(created_ms),
        "updated_at_display": _format_ms(updated_ms),
        "tokens_used": _as_int(_row_get(row, "tokens_used")) or 0,
        "model": str(_row_get(row, "model", "") or ""),
        "model_provider": str(_row_get(row, "model_provider", "") or ""),
        "archived": _as_bool(_row_get(row, "archived", 0)),
        "is_subagent": _is_subagent_row(row, source_key),
        "cli_version": str(_row_get(row, "cli_version", "") or ""),
        "git_branch": str(_row_get(row, "git_branch", "") or ""),
        "originator": str(_row_get(row, "originator", "") or ""),
        "thread_source": str(_row_get(row, "thread_source", "") or ""),
        "agent_role": str(_row_get(row, "agent_role", "") or ""),
        "agent_nickname": str(_row_get(row, "agent_nickname", "") or ""),
        "is_pinned": _as_bool(_row_get(row, "is_pinned", 0)),
    }


class CodexStore:
    """只读访问一个 Codex 数据目录。"""

    def __init__(self, codex_home: str) -> None:
        self.home = Path(codex_home).expanduser()
        self.db_path = self.home / "state_5.sqlite"
        self._cache: "OrderedDict[Tuple[str, int, int], Dict[str, Any]]" = OrderedDict()
        self._cache_lock = threading.Lock()

    # -- SQLite ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise ViewerError("找不到数据库：%s" % self.db_path)
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        except sqlite3.Error as exc:
            raise ViewerError("无法只读打开数据库：%s" % exc) from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
        except sqlite3.Error:
            pass
        return conn

    def _select_sql(self, conn: sqlite3.Connection, where: str = "") -> str:
        info = conn.execute("PRAGMA table_info(threads)").fetchall()
        if not info:
            raise ViewerError("数据库缺少 threads 表：%s" % self.db_path)
        columns = {row["name"] for row in info}
        expressions = []
        for name in THREAD_FIELDS:
            if name in columns:
                expressions.append(name)
            else:
                expressions.append("NULL AS %s" % name)
        sql = "SELECT %s FROM threads" % ", ".join(expressions)
        if where:
            sql += " " + where
        # 精确排序在 Python 里按毫秒值做，这里先按秒级更新时间粗排。
        sql += " ORDER BY updated_at DESC, id DESC"
        return sql

    def list_sessions(
        self,
        source: str = "all",
        query: str = "",
        include_archived: bool = False,
        include_subagents: bool = False,
    ) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(self._select_sql(conn)).fetchall()
        except sqlite3.Error as exc:
            raise ViewerError("读取 threads 表失败：%s" % exc) from exc
        finally:
            conn.close()

        wanted_source = (source or "all").strip().lower()
        needle = (query or "").strip().casefold()
        sessions: List[Dict[str, Any]] = []

        for row in rows:
            session = _thread_to_dict(row)
            if not include_archived and session["archived"]:
                continue
            if not include_subagents and session["is_subagent"]:
                continue
            if wanted_source not in ("", "all"):
                if wanted_source == "other":
                    if session["source_key"] in ("exec", "vscode"):
                        continue
                elif session["source_key"] != wanted_source:
                    continue
            if needle:
                haystack = "\n".join(
                    [
                        session["title"],
                        session["cwd"],
                        str(_row_get(row, "first_user_message", "") or ""),
                        str(_row_get(row, "preview", "") or ""),
                    ]
                ).casefold()
                if needle not in haystack:
                    continue
            sessions.append(session)

        sessions.sort(key=lambda item: (item["updated_at"] or 0, item["id"]), reverse=True)
        return sessions

    def _fetch_thread(self, thread_id: str) -> Optional[sqlite3.Row]:
        conn = self._connect()
        try:
            sql = self._select_sql(conn, "WHERE id = ?")
            return conn.execute(sql, (thread_id,)).fetchone()
        except sqlite3.Error as exc:
            raise ViewerError("读取会话失败：%s" % exc) from exc
        finally:
            conn.close()

    # -- rollout --------------------------------------------------------
    def _path_from_raw(self, raw: Any) -> Optional[Path]:
        if raw is None or not str(raw).strip():
            return None
        text = str(raw)
        marker = "__CODEX_HOME__"
        if text.startswith(marker):
            rest = text[len(marker):].lstrip("/\\")
            return (self.home / rest).resolve()
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = self.home / path
        return path.resolve()

    def _find_by_id(self, root: Path, thread_id: str, suffix: str) -> List[Path]:
        """在 sessions/ 或 archived_sessions/ 下按会话 id 找文件。"""
        if not thread_id or not root.is_dir():
            return []
        matches: List[Path] = []
        for dirpath, _dirnames, filenames in os.walk(str(root)):
            for filename in filenames:
                if filename.endswith(suffix) and thread_id in filename:
                    matches.append(Path(dirpath) / filename)
        matches.sort()
        return matches

    def _locate_rollout(
        self, raw: Any, thread_id: str
    ) -> Tuple[Optional[Path], List[Path]]:
        """按“原路径 → 原路径.zst → 按 id 查找”的顺序定位记录文件。"""
        tried: List[Path] = []

        def remember(path: Optional[Path]) -> None:
            if path is not None and path not in tried:
                tried.append(path)

        original = self._path_from_raw(raw)
        if original is not None:
            remember(original)
            if original.is_file():
                return original, tried
            if not original.name.lower().endswith(".zst"):
                compressed = Path(str(original) + ".zst")
                remember(compressed)
                if compressed.is_file():
                    return compressed, tried

        for root_name in ("sessions", "archived_sessions"):
            root = self.home / root_name
            for suffix in (".jsonl", ".jsonl.zst"):
                matches = self._find_by_id(root, thread_id, suffix)
                for match in matches:
                    remember(match)
                if matches:
                    return matches[0], tried

        return None, tried

    def _parse_cached(self, path: Path) -> Dict[str, Any]:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        parsed = parse_rollout(path)
        with self._cache_lock:
            self._cache[key] = parsed
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return parsed

    def get_session(self, thread_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetch_thread(thread_id)
        if row is None:
            return None

        session = _thread_to_dict(row)
        rollout, tried = self._locate_rollout(
            _row_get(row, "rollout_path"), session["id"]
        )
        warnings: List[str] = []
        source_file: Dict[str, Any] = {
            "path": str(rollout) if rollout else "",
            "exists": False,
            "size": 0,
            "compressed": bool(
                rollout is not None and rollout.name.lower().endswith(".zst")
            ),
            "tried": [str(path) for path in tried],
        }
        parsed: Dict[str, Any] = {
            "meta": {},
            "turns": [],
            "warnings": [],
            "stats": {
                "turn_count": 0,
                "group_count": 0,
                "row_count": 0,
                "label_counts": {},
                "total_tokens": 0,
                "last_total_tokens": None,
            },
        }

        if rollout is None:
            warnings.append(
                "找不到会话记录文件（原路径、.zst 和按 id 查找均未命中）。"
                "已尝试：%s" % _format_tried(tried)
            )
        else:
            try:
                stat = rollout.stat()
                source_file.update({"exists": True, "size": stat.st_size})
                parsed = self._parse_cached(rollout)
            except ViewerError as exc:
                warnings.append(str(exc))
            except FileNotFoundError:
                warnings.append("会话记录文件在读取前被移动或删除：%s" % rollout)
            except PermissionError:
                warnings.append("没有权限读取会话记录文件：%s" % rollout)
            except OSError as exc:
                warnings.append("无法读取会话记录文件：%s（%s）" % (rollout, exc))

        meta = {
            "id": session["id"],
            "session_id": session["id"],
            "cwd": session["cwd"],
            "source": session["source"],
            "source_label": session["source_label"],
            "originator": session["originator"],
            "cli_version": session["cli_version"],
            "model_provider": session["model_provider"],
            "thread_source": session["thread_source"],
        }
        meta.update(parsed.get("meta") or {})

        return {
            "session": session,
            "meta": meta,
            "turns": parsed.get("turns") or [],
            "warnings": warnings + list(parsed.get("warnings") or []),
            "stats": parsed.get("stats") or {},
            "source_file": source_file,
        }


# ---------------------------------------------------------------------------
# rollout JSONL 解析
# ---------------------------------------------------------------------------


def _run_decompress(cmd: List[str], label: str) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=DECOMPRESS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "%s 调用失败：%s" % (label, exc)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 500:
            detail = detail[:500] + "…"
        suffix = ("：" + detail) if detail else ""
        return None, "%s 退出码 %d%s" % (label, proc.returncode, suffix)
    return proc.stdout, None


def _decompress_zstd(path: Path) -> bytes:
    """优先 zstd -dc；没有 zstd 时用 Node 22+ 自带 zlib.zstdDecompressSync。"""
    if not path.is_file():
        raise FileNotFoundError(str(path))

    errors: List[str] = []
    zstd = shutil.which("zstd")
    if zstd:
        data, error = _run_decompress([zstd, "-dc", str(path)], "zstd")
        if data is not None:
            return data
        if error:
            errors.append(error)

    node = shutil.which("node")
    if node:
        data, error = _run_decompress(
            [node, "-e", NODE_ZSTD_SCRIPT, str(path)], "node"
        )
        if data is not None:
            return data
        if error:
            errors.append(error)

    if not zstd and not node:
        raise ViewerError(
            "无法解压会话文件 %s：未找到 zstd 或 node。请安装 zstd，"
            "或安装 Node.js 22+（自带 zstd 解压）。" % path
        )
    raise ViewerError("无法解压会话文件 %s：%s" % (path, "；".join(errors)))


def _iter_rollout_lines(path: Path):
    if path.name.lower().endswith(".zst"):
        data = _decompress_zstd(path)
        text = data.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.split("\n"), 1):
            yield lineno, line
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for lineno, raw_line in enumerate(handle, 1):
            yield lineno, raw_line


def _format_tried(paths: List[Path], limit: int = 6) -> str:
    if not paths:
        return "（没有可尝试的路径）"
    text = "、".join(str(path) for path in paths[:limit])
    if len(paths) > limit:
        text += " 等 %d 个位置" % len(paths)
    return text


def _content_texts(content: Any) -> List[str]:
    if isinstance(content, str):
        return [content]
    texts: List[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                texts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
            elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                texts.append(part["refusal"])
    elif isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            texts.append(text)
    return texts


def _summary_texts(summary: Any) -> List[str]:
    if isinstance(summary, str):
        return [summary]
    texts: List[str] = []
    if isinstance(summary, list):
        for item in summary:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
    return texts


def _extract_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        pieces = []
        for item in output:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    pieces.append(text)
                else:
                    pieces.append(_pretty(item))
            else:
                pieces.append(str(item))
        return "\n".join(pieces)
    if isinstance(output, dict):
        for key in ("output", "text", "content"):
            if key in output:
                return _extract_output(output[key])
    return _pretty(output)


def parse_rollout(path: Path) -> Dict[str, Any]:
    """解析一个 rollout JSONL，返回会话头、按轮分组的行和警告。

    坏行只记一条警告并跳过，任何未知类型都落到“其他”，不会抛错。
    """
    path = Path(path)
    meta: Dict[str, Any] = {}
    turns: List[Dict[str, Any]] = []
    warnings: List[str] = []
    pending_tools: Dict[str, Dict[str, Any]] = {}

    current: Optional[Dict[str, Any]] = None
    pre_turn: Optional[Dict[str, Any]] = None
    row_seq = 0

    def next_row_id() -> int:
        nonlocal row_seq
        row_seq += 1
        return row_seq

    def new_real_turn(payload: Dict[str, Any], timestamp: Any) -> Dict[str, Any]:
        started = payload.get("started_at")
        started_ms = _to_ms(started)
        if started_ms is None:
            started_ms = _to_ms(timestamp)
        turn = {
            "number": sum(1 for item in turns if not item.get("pre")) + 1,
            "turn_id": payload.get("turn_id") or payload.get("root_turn_id"),
            "started_at": timestamp,
            "started_at_ms": started_ms,
            "started_at_display": _format_iso(timestamp) or _format_ms(started_ms),
            "started_time": _clock(timestamp) or _clock(started_ms),
            "completed_at_ms": None,
            "duration_ms": None,
            "duration_display": "",
            "time_to_first_token_ms": None,
            "completed": False,
            "incomplete": False,
            "token_info": None,
            "rows": [],
            "pre": False,
        }
        turns.append(turn)
        return turn

    def target_turn() -> Dict[str, Any]:
        """当前轮；没有当前轮时挂到上一轮，首轮之前则用“会话前置”。"""
        nonlocal current, pre_turn
        if current is not None:
            return current
        real_turns = [item for item in turns if not item.get("pre")]
        if real_turns:
            return real_turns[-1]
        if pre_turn is None:
            pre_turn = {
                "number": 0,
                "label": "会话前置",
                "turn_id": None,
                "started_at": None,
                "started_at_ms": None,
                "started_at_display": "",
                "started_time": "",
                "completed_at_ms": None,
                "duration_ms": None,
                "duration_display": "",
                "time_to_first_token_ms": None,
                "completed": False,
                "incomplete": False,
                "token_info": None,
                "rows": [],
                "pre": True,
            }
            turns.append(pre_turn)
        return pre_turn

    def add_row(
        turn: Dict[str, Any],
        kind: str,
        timestamp: Any,
        text: Any,
        **extra: Any
    ) -> Dict[str, Any]:
        clipped, truncated, original = _clip("" if text is None else str(text))
        row: Dict[str, Any] = {
            "id": next_row_id(),
            "kind": kind,
            "label": KIND_LABELS.get(kind, "其他"),
            "timestamp": timestamp if isinstance(timestamp, str) else "",
            "time": _clock(timestamp),
            "text": clipped,
        }
        if truncated:
            row["text_truncated"] = True
            row["original_length"] = original
        row.update(extra)
        turn["rows"].append(row)
        return row

    def add_context(turn: Dict[str, Any], timestamp: Any, value: Any, subtype: str) -> None:
        add_row(turn, "context", timestamp, _pretty(value), subtype=subtype, collapsed=True)

    def add_other(turn: Dict[str, Any], timestamp: Any, subtype: str, value: Any) -> None:
        add_row(
            turn,
            "other",
            timestamp,
            _pretty({"type": subtype, "payload": value}),
            subtype=subtype,
            collapsed=True,
        )

    def handle_message(payload: Dict[str, Any], timestamp: Any) -> None:
        role = str(payload.get("role") or "user").lower()
        texts = _content_texts(payload.get("content"))
        phase = payload.get("phase") if isinstance(payload.get("phase"), str) else None
        turn = target_turn()

        if role in ("developer", "system"):
            text = "\n\n".join(texts) if texts else "（空消息）"
            add_row(turn, "system", timestamp, text, role=role)
            return

        if role == "user":
            # environment_context 等通常以 < 开头；脱敏样例里正文被替换，
            # 但 content_item_kinds 仍保留环境上下文的标记，作为第二判据。
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            kinds = []
            if isinstance(metadata, dict) and isinstance(metadata.get("content_item_kinds"), list):
                kinds = [str(item).lower() for item in metadata["content_item_kinds"]]
            context_hint = any(
                "environment" in item or "context" in item for item in kinds
            )
            segments: List[Tuple[str, str]] = []
            for text in texts:
                is_context = text.lstrip().startswith("<") or context_hint
                kind = "context" if is_context else "user"
                if segments and segments[-1][0] == kind:
                    segments[-1] = (kind, segments[-1][1] + "\n\n" + text)
                else:
                    segments.append((kind, text))
            if not segments:
                add_row(turn, "user", timestamp, "（空消息）", role=role)
                return
            for kind, text in segments:
                add_row(
                    turn,
                    kind,
                    timestamp,
                    text,
                    role=role,
                    collapsed=(kind == "context"),
                )
            return

        if role == "assistant":
            text = "\n\n".join(texts) if texts else "（空消息）"
            add_row(turn, "assistant", timestamp, text, role=role, phase=phase)
            return

        add_other(turn, timestamp, "message/%s" % role, payload)

    def handle_reasoning(payload: Dict[str, Any], timestamp: Any) -> None:
        texts = _summary_texts(payload.get("summary"))
        if texts:
            text = "\n\n".join(texts)
        elif payload.get("encrypted_content"):
            text = "（思考已加密，不显示内容）"
        else:
            text = "（无摘要）"
        add_row(target_turn(), "reasoning", timestamp, text, phase=payload.get("phase"))

    def handle_tool_call(payload: Dict[str, Any], timestamp: Any) -> None:
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        name = str(payload.get("name") or payload.get("tool_name") or "tool")
        arguments = payload.get("arguments")
        if arguments is None:
            arguments = payload.get("input")
        arg_text, arg_truncated, arg_len = _clip(_pretty(arguments) if arguments is not None else "")
        tool = {
            "call_id": call_id,
            "name": name,
            "arguments": arg_text,
            "output": None,
            "output_missing": True,
            "status": payload.get("status") if isinstance(payload.get("status"), str) else "",
            "orphan": False,
        }
        if arg_truncated:
            tool["arguments_truncated"] = True
            tool["arguments_length"] = arg_len
        row = add_row(target_turn(), "tool", timestamp, arg_text, tool=tool)
        if call_id:
            pending_tools[call_id] = row

    def handle_tool_output(payload: Dict[str, Any], timestamp: Any) -> None:
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        raw_output = _extract_output(payload.get("output"))
        output, output_truncated, output_len = _clip(raw_output)
        row = pending_tools.pop(call_id, None) if call_id else None
        if row is not None:
            tool = row.get("tool") or {}
            tool["output"] = output
            tool["output_missing"] = False
            if output_truncated:
                tool["output_truncated"] = True
                tool["output_length"] = output_len
            row["tool"] = tool
            return
        # 找不到调用时输出也单独成行，保证信息不丢。
        tool = {
            "call_id": call_id,
            "name": "输出",
            "arguments": "",
            "output": output,
            "output_missing": False,
            "status": "",
            "orphan": True,
        }
        if output_truncated:
            tool["output_truncated"] = True
            tool["output_length"] = output_len
        add_row(target_turn(), "tool", timestamp, output, tool=tool)

    def handle_event(payload: Dict[str, Any], timestamp: Any) -> None:
        nonlocal current
        subtype = str(payload.get("type") or "")
        if subtype == "task_started":
            if current is not None and not current.get("pre") and not current.get("completed"):
                current["incomplete"] = True
            current = new_real_turn(payload, timestamp)
            return
        if subtype == "task_complete":
            turn_id = payload.get("turn_id") or payload.get("root_turn_id")
            turn: Optional[Dict[str, Any]] = None
            if turn_id:
                for item in reversed(turns):
                    if item.get("turn_id") == turn_id:
                        turn = item
                        break
            if turn is None:
                turn = current
            if turn is None:
                turn = new_real_turn(payload, timestamp)
            duration = _as_int(payload.get("duration_ms"))
            completed_ms = _to_ms(payload.get("completed_at"))
            if duration is None and completed_ms and turn.get("started_at_ms"):
                duration = max(0, completed_ms - int(turn["started_at_ms"]))
            turn["duration_ms"] = duration
            turn["duration_display"] = _format_duration(duration)
            turn["completed_at_ms"] = completed_ms
            turn["completed"] = True
            turn["time_to_first_token_ms"] = _as_int(payload.get("time_to_first_token_ms"))
            if current is turn:
                current = None
            return
        if subtype == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                target_turn()["token_info"] = info
            return
        if subtype == "thread_settings_applied":
            value = payload.get("thread_settings", payload)
            add_context(target_turn(), timestamp, value, "event_msg/thread_settings_applied")
            return
        if subtype in SKIP_EVENT_TYPES:
            return
        add_other(target_turn(), timestamp, "event_msg/%s" % (subtype or "unknown"), payload)

    def process_record(record: Dict[str, Any]) -> None:
        nonlocal current
        record_type = str(record.get("type") or "")
        timestamp = record.get("timestamp")
        payload = record.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"value": payload}

        if record_type == "session_meta":
            for key in (
                "id",
                "session_id",
                "timestamp",
                "cwd",
                "originator",
                "cli_version",
                "source",
                "thread_source",
                "model_provider",
                "context_window",
            ):
                if key in payload and payload[key] not in (None, ""):
                    meta[key] = payload[key]
            return

        if record_type == "response_item":
            subtype = str(payload.get("type") or "")
            if subtype == "message":
                handle_message(payload, timestamp)
            elif subtype == "reasoning":
                handle_reasoning(payload, timestamp)
            elif subtype in ("function_call", "custom_tool_call"):
                handle_tool_call(payload, timestamp)
            elif subtype in ("function_call_output", "custom_tool_call_output"):
                handle_tool_output(payload, timestamp)
            elif subtype in SKIP_RESPONSE_TYPES:
                return
            else:
                add_other(target_turn(), timestamp, "response_item/%s" % (subtype or "unknown"), payload)
            return

        if record_type == "event_msg":
            handle_event(payload, timestamp)
            return

        if record_type == "turn_context":
            add_context(target_turn(), timestamp, payload, "turn_context")
            return

        if record_type == "world_state":
            state = payload.get("state")
            value = state if isinstance(state, str) else payload
            add_context(target_turn(), timestamp, value, "world_state")
            return

        if record_type in SKIP_EVENT_TYPES:
            return

        add_other(target_turn(), timestamp, record_type or "unknown", payload)

    try:
        for lineno, raw_line in _iter_rollout_lines(path):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (TypeError, ValueError) as exc:
                warnings.append("第 %d 行不是有效 JSON，已跳过：%s" % (lineno, exc))
                continue
            if not isinstance(record, dict):
                warnings.append("第 %d 行不是 JSON 对象，已跳过" % lineno)
                continue
            try:
                process_record(record)
            except Exception as exc:  # 单行异常不能中断整个文件
                warnings.append("第 %d 行解析失败，已跳过：%s" % (lineno, exc))
    except OSError:
        raise

    # 每轮 token：用该轮最后一条 token_count 的 info。累计值做差得到本轮用量。
    token_keys = (
        "total_tokens",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    previous_usage: Optional[Dict[str, int]] = None
    previous_total: Optional[int] = None
    last_cumulative: Optional[int] = None
    for turn in turns:
        info = turn.pop("token_info", None) or {}
        total = info.get("total_token_usage") if isinstance(info, dict) else None
        last = info.get("last_token_usage") if isinstance(info, dict) else None
        if not isinstance(total, dict):
            total = {}
        if not isinstance(last, dict):
            last = {}

        tokens: Dict[str, int] = {}
        source = ""
        current_total = _as_int(total.get("total_tokens"))
        current_usage = {}
        for key in token_keys:
            value = _as_int(total.get(key))
            if value is not None:
                current_usage[key] = value

        if current_total is not None:
            last_cumulative = current_total
            if previous_usage is None or previous_total is None:
                tokens = dict(current_usage)
                source = "cumulative"
            elif current_total >= previous_total:
                for key in token_keys:
                    now = current_usage.get(key)
                    if now is not None:
                        tokens[key] = max(0, now - previous_usage.get(key, 0))
                source = "delta"
            else:
                for key in token_keys:
                    value = _as_int(last.get(key))
                    if value is not None:
                        tokens[key] = value
                source = "last_call"
            previous_usage = current_usage
            previous_total = current_total
        elif last:
            for key in token_keys:
                value = _as_int(last.get(key))
                if value is not None:
                    tokens[key] = value
            source = "last_call"

        turn["tokens"] = tokens or None
        turn["tokens_source"] = source or None
        if not turn.get("started_at_display") and turn.get("started_at_ms"):
            turn["started_at_display"] = _format_ms(turn["started_at_ms"])
        if not turn.get("started_time") and turn.get("started_at_ms"):
            turn["started_time"] = _clock(turn["started_at_ms"])

    label_counts: Dict[str, int] = {}
    row_count = 0
    for turn in turns:
        for row in turn.get("rows", []):
            row_count += 1
            kind = row.get("kind", "other")
            label_counts[kind] = label_counts.get(kind, 0) + 1

    stats = {
        "turn_count": sum(1 for item in turns if not item.get("pre")),
        "group_count": len(turns),
        "row_count": row_count,
        "label_counts": label_counts,
        "total_tokens": last_cumulative if last_cumulative is not None else sum(
            (turn.get("tokens") or {}).get("total_tokens", 0) for turn in turns
        ),
        "last_total_tokens": last_cumulative,
    }

    return {"meta": meta, "turns": turns, "warnings": warnings, "stats": stats}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _query_value(query: Dict[str, List[str]], name: str, default: str = "") -> str:
    values = query.get(name)
    return values[-1] if values else default


def _query_flag(query: Dict[str, List[str]], name: str) -> bool:
    values = query.get(name)
    if not values:
        return False
    return values[-1].strip().lower() in ("1", "true", "yes", "on")


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "CodexViewer/1.0"
    sys_version = ""

    @property
    def store(self) -> CodexStore:
        return self.server.store  # type: ignore[attr-defined]

    # -- 响应 -----------------------------------------------------------
    def _common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: int = 200,
        csp: Optional[str] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._common_headers()
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status=status)

    def _send_index(self) -> None:
        path = STATIC_DIR / "index.html"
        try:
            data = path.read_bytes()
        except OSError as exc:
            self._send_json({"error": "读取页面失败：%s" % exc}, status=500)
            return
        csp = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'"
        )
        self._send_bytes(data, "text/html; charset=utf-8", csp=csp)

    # -- 路由 -----------------------------------------------------------
    def do_GET(self) -> None:
        try:
            self._dispatch()
        except ViewerError as exc:
            self._send_json({"error": str(exc)}, status=500)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._send_json({"error": "服务器内部错误"}, status=500)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_HEAD(self) -> None:
        # 复用 GET 路由，_send_bytes 在 HEAD 时只发响应头。
        self.do_GET()

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_index()
            return

        if path == "/favicon.ico":
            self._send_bytes(b"", "image/x-icon", status=204)
            return

        if path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "app": APP_NAME,
                    "codex_home": str(self.store.home),
                    "database": str(self.store.db_path),
                    "database_exists": self.store.db_path.is_file(),
                }
            )
            return

        if path == "/api/sessions":
            query = parse_qs(parsed.query, keep_blank_values=True)
            source = _query_value(query, "source", "all") or "all"
            search = _query_value(query, "q", "")
            archived = _query_flag(query, "archived") or _query_flag(query, "show_archived")
            subagents = _query_flag(query, "subagents") or _query_flag(query, "show_subagents")
            sessions = self.store.list_sessions(
                source=source,
                query=search,
                include_archived=archived,
                include_subagents=subagents,
            )
            self._send_json(
                {
                    "sessions": sessions,
                    "count": len(sessions),
                    "filters": {
                        "source": source,
                        "q": search,
                        "archived": archived,
                        "subagents": subagents,
                    },
                }
            )
            return

        match = re.fullmatch(r"/api/sessions/([^/]+)", path)
        if match:
            thread_id = unquote(match.group(1))
            detail = self.store.get_session(thread_id)
            if detail is None:
                self._send_json({"error": "会话不存在：%s" % thread_id}, status=404)
                return
            self._send_json(detail)
            return

        if path in STATIC_ROUTES:
            filename, content_type = STATIC_ROUTES[path]
            try:
                data = (STATIC_DIR / filename).read_bytes()
            except OSError as exc:
                self._send_json({"error": "读取静态文件失败：%s" % exc}, status=500)
                return
            self._send_bytes(data, content_type)
            return

        self._send_json({"error": "未找到：%s" % path}, status=404)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], store: CodexStore) -> None:
        self.store = store
        super().__init__(address, ViewerHandler)


def make_server(store: CodexStore, port: int = 0) -> ViewerServer:
    return ViewerServer(("127.0.0.1", port), store)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Codex 会话只读看板（Python 3.9 标准库，只监听 127.0.0.1）"
    )
    parser.add_argument(
        "--codex-home",
        default=DEFAULT_CODEX_HOME,
        help="Codex 数据目录，默认 ~/.codex",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="监听端口，默认 3090",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 到 65535 之间")

    store = CodexStore(args.codex_home)
    try:
        httpd = make_server(store, args.port)
    except OSError as exc:
        print("无法监听 127.0.0.1:%d：%s" % (args.port, exc), file=sys.stderr)
        return 2

    actual_port = httpd.server_address[1]
    print("%s：http://127.0.0.1:%d" % (APP_NAME, actual_port), flush=True)
    print("数据目录：%s（只读）" % store.home, flush=True)
    if not store.db_path.is_file():
        print("警告：找不到 %s，页面会提示数据库缺失。" % store.db_path, file=sys.stderr)

    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
