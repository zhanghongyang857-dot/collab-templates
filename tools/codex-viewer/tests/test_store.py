# -*- coding: utf-8 -*-
"""CodexStore 的列表、筛选和只读测试。"""

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402


FIXTURE = ROOT / "fixtures" / "codex-home"
FIXTURE_ID = "01a14de0-1bbb-7790-b839-372fc9363740"
ARCHIVED_ID = "01a14de0-0000-7000-8000-00000000a2c1"
ROLLOUT = next((FIXTURE / "sessions").rglob("*.jsonl"))
ARCHIVED_ZST = next((FIXTURE / "archived_sessions").glob("*.jsonl.zst"))

MINIMAL_SCHEMA = """
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    rollout_path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    cwd TEXT NOT NULL,
    title TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);
"""

FILTER_SCHEMA = """
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    rollout_path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    cwd TEXT NOT NULL,
    title TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    agent_role TEXT,
    agent_nickname TEXT,
    agent_path TEXT,
    thread_source TEXT,
    model TEXT,
    cli_version TEXT,
    first_user_message TEXT DEFAULT '',
    preview TEXT DEFAULT '',
    originator TEXT,
    model_provider TEXT,
    git_branch TEXT,
    archived_at INTEGER,
    created_at_ms INTEGER,
    updated_at_ms INTEGER,
    recency_at_ms INTEGER,
    is_pinned INTEGER DEFAULT 0
);
"""


def make_home(directory, schema, rows, optional_columns=False):
    home = Path(directory) / "codex-home"
    home.mkdir(parents=True)
    db = home / "state_5.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(schema)
        conn.executemany(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, cwd, title,
                tokens_used, archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row[:9] for row in rows],
        )
        if optional_columns:
            conn.executemany(
                """
                UPDATE threads
                SET agent_role = ?, thread_source = ?, model = ?,
                    cli_version = ?, created_at_ms = ?, updated_at_ms = ?
                WHERE id = ?
                """,
                [
                    (row[9], row[10], row[11], row[12], row[13], row[14], row[0])
                    for row in rows
                ],
            )
        conn.commit()
    finally:
        conn.close()
    return home


class FixtureStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = server.CodexStore(str(FIXTURE))

    def test_list_returns_fixture_session(self):
        sessions = self.store.list_sessions()
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session["id"], FIXTURE_ID)
        self.assertIn("fruits.txt", session["title"])
        self.assertEqual(session["source_key"], "exec")
        self.assertEqual(session["source_label"], "命令行")
        self.assertEqual(session["tokens_used"], 97600)
        self.assertFalse(session["archived"])
        self.assertFalse(session["is_subagent"])
        self.assertGreater(session["updated_at"], 0)

    def test_source_and_query_filters(self):
        self.assertEqual(len(self.store.list_sessions(source="exec")), 1)
        self.assertEqual(len(self.store.list_sessions(source="vscode")), 0)
        self.assertEqual(len(self.store.list_sessions(query="fruits")), 1)
        self.assertEqual(len(self.store.list_sessions(query="水果总数")), 1)
        self.assertEqual(len(self.store.list_sessions(query="不存在的关键词")), 0)

    def test_detail_parses_two_turns(self):
        detail = self.store.get_session(FIXTURE_ID)
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["turns"]), 2)
        self.assertEqual(detail["warnings"], [])
        self.assertTrue(detail["source_file"]["exists"])
        self.assertEqual(detail["meta"]["cli_version"], "0.163.0")

    def test_archived_compressed_session_is_listed_and_opened(self):
        sessions = self.store.list_sessions(include_archived=True)
        self.assertEqual(len(sessions), 2)
        archived = next(item for item in sessions if item["id"] == ARCHIVED_ID)
        self.assertTrue(archived["archived"])

        detail = self.store.get_session(ARCHIVED_ID)
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["turns"]), 2)
        self.assertEqual(detail["warnings"], [])
        self.assertTrue(detail["source_file"]["exists"])
        self.assertTrue(detail["source_file"]["compressed"])
        self.assertTrue(detail["source_file"]["path"].endswith(".jsonl.zst"))
        self.assertEqual(detail["meta"]["id"], ARCHIVED_ID)

    def test_moved_rollout_is_found_by_session_id(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        moved_id = "01a14de0-dead-8000-9000-000000000001"
        home = make_home(
            tmp.name,
            MINIMAL_SCHEMA,
            [
                (
                    moved_id,
                    "/no/such/placeholder.jsonl",
                    1000,
                    2000,
                    "exec",
                    "/work/moved",
                    "Moved rollout",
                    7,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            ],
        )
        target = (
            home
            / "archived_sessions"
            / ("rollout-2026-10-18T15-18-15-%s.jsonl" % moved_id)
        )
        target.parent.mkdir()
        shutil.copyfile(str(ROLLOUT), str(target))

        detail = server.CodexStore(str(home)).get_session(moved_id)
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["turns"]), 2)
        self.assertEqual(detail["warnings"], [])
        self.assertTrue(detail["source_file"]["exists"])
        self.assertFalse(detail["source_file"]["compressed"])
        self.assertTrue(detail["source_file"]["path"].endswith(target.name))
        self.assertIn(str(target), detail["source_file"]["tried"])

    def test_moved_compressed_rollout_is_found_by_session_id(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        moved_id = "01a14de0-dead-8000-9000-00000000beef"
        home = make_home(
            tmp.name,
            MINIMAL_SCHEMA,
            [
                (
                    moved_id,
                    "/no/such/placeholder.jsonl",
                    1000,
                    2000,
                    "exec",
                    "/work/moved-zst",
                    "Moved zst rollout",
                    7,
                    1,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            ],
        )
        target = (
            home
            / "archived_sessions"
            / ("rollout-2026-10-18T15-18-15-%s.jsonl.zst" % moved_id)
        )
        target.parent.mkdir()
        shutil.copyfile(str(ARCHIVED_ZST), str(target))

        detail = server.CodexStore(str(home)).get_session(moved_id)
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["turns"]), 2)
        self.assertEqual(detail["warnings"], [])
        self.assertTrue(detail["source_file"]["exists"])
        self.assertTrue(detail["source_file"]["compressed"])
        self.assertTrue(detail["source_file"]["path"].endswith(".jsonl.zst"))
        self.assertIn(str(target), detail["source_file"]["tried"])

    def test_decompression_unavailable_is_a_page_warning(self):
        fresh = server.CodexStore(str(FIXTURE))
        with mock.patch("server.shutil.which", return_value=None):
            detail = fresh.get_session(ARCHIVED_ID)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["turns"], [])
        self.assertTrue(detail["source_file"]["exists"])
        self.assertTrue(detail["source_file"]["compressed"])
        self.assertTrue(
            any("zstd" in item and "node" in item for item in detail["warnings"])
        )

    def test_unknown_session(self):
        self.assertIsNone(self.store.get_session("does-not-exist"))

    def test_database_is_read_only(self):
        conn = self.store._connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE should_not_exist (id TEXT)")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM threads")
        finally:
            conn.close()

    def test_fixture_files_are_not_modified(self):
        before = sorted(str(path.relative_to(FIXTURE)) for path in FIXTURE.rglob("*"))
        db_mtime = (FIXTURE / "state_5.sqlite").stat().st_mtime_ns

        self.store.list_sessions()
        self.store.get_session(FIXTURE_ID)

        after = sorted(str(path.relative_to(FIXTURE)) for path in FIXTURE.rglob("*"))
        self.assertEqual(before, after)
        self.assertEqual(db_mtime, (FIXTURE / "state_5.sqlite").stat().st_mtime_ns)


class FilterStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = make_home(
            self.tmp.name,
            FILTER_SCHEMA,
            [
                (
                    "vscode-id",
                    "",
                    10000,
                    40,
                    "vscode",
                    "/work/alpha",
                    "VSCode task",
                    10,
                    0,
                    None,
                    "user",
                    "gpt-test",
                    "0.163.0",
                    10000,
                    40000,
                ),
                (
                    "exec-id",
                    "",
                    20000,
                    30,
                    "exec",
                    "/work/alpha",
                    "Exec task",
                    20,
                    0,
                    None,
                    "user",
                    "gpt-test",
                    "0.163.0",
                    20000,
                    30000,
                ),
                (
                    "archived-id",
                    "",
                    30000,
                    50,
                    "exec",
                    "/work/beta",
                    "Archived task",
                    30,
                    1,
                    None,
                    "user",
                    "gpt-test",
                    "0.163.0",
                    30000,
                    50000,
                ),
                (
                    "sub-id",
                    "",
                    40000,
                    60,
                    "subagent",
                    "/work/gamma",
                    "Sub task",
                    40,
                    0,
                    "explorer",
                    "subagent",
                    "gpt-test",
                    "0.163.0",
                    40000,
                    60000,
                ),
            ],
            optional_columns=True,
        )
        self.store = server.CodexStore(str(self.home))

    def ids(self, **kwargs):
        return [session["id"] for session in self.store.list_sessions(**kwargs)]

    def test_default_hides_archived_and_subagents(self):
        self.assertEqual(self.ids(), ["vscode-id", "exec-id"])

    def test_toggles_show_archived_and_subagents(self):
        self.assertEqual(
            self.ids(include_archived=True),
            ["archived-id", "vscode-id", "exec-id"],
        )
        self.assertEqual(
            self.ids(include_subagents=True),
            ["sub-id", "vscode-id", "exec-id"],
        )
        self.assertEqual(
            self.ids(include_archived=True, include_subagents=True),
            ["sub-id", "archived-id", "vscode-id", "exec-id"],
        )

    def test_source_filter(self):
        self.assertEqual(self.ids(source="exec"), ["exec-id"])
        self.assertEqual(self.ids(source="vscode"), ["vscode-id"])
        self.assertEqual(self.ids(source="other"), [])
        self.assertEqual(self.ids(source="other", include_subagents=True), ["sub-id"])
        self.assertEqual(
            self.ids(source="exec", include_archived=True),
            ["archived-id", "exec-id"],
        )

    def test_search_title_and_cwd(self):
        self.assertEqual(self.ids(query="VSCode"), ["vscode-id"])
        self.assertEqual(self.ids(query="alpha"), ["vscode-id", "exec-id"])
        self.assertEqual(self.ids(query="beta"), [])
        self.assertEqual(self.ids(query="beta", include_archived=True), ["archived-id"])
        self.assertEqual(self.ids(query="gamma", include_subagents=True), ["sub-id"])

    def test_labels_and_tokens(self):
        sessions = {item["id"]: item for item in self.store.list_sessions(include_archived=True, include_subagents=True)}
        self.assertEqual(sessions["vscode-id"]["source_label"], "桌面版")
        self.assertEqual(sessions["exec-id"]["source_label"], "命令行")
        self.assertEqual(sessions["sub-id"]["source_label"], "子 agent")
        self.assertTrue(sessions["archived-id"]["archived"])
        self.assertTrue(sessions["sub-id"]["is_subagent"])
        self.assertEqual(sessions["exec-id"]["tokens_used"], 20)

    def test_minimal_schema_and_missing_rollout(self):
        home = make_home(
            Path(self.tmp.name) / "minimal",
            MINIMAL_SCHEMA,
            [
                (
                    "missing-id",
                    "/definitely/missing/rollout.jsonl",
                    1000,
                    2000,
                    "exec",
                    "/work/minimal",
                    "Missing rollout",
                    7,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            ],
        )
        store = server.CodexStore(str(home))
        sessions = store.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["source_label"], "命令行")
        self.assertEqual(sessions[0]["updated_at"], 2000000)

        detail = store.get_session("missing-id")
        self.assertIsNotNone(detail)
        self.assertFalse(detail["source_file"]["exists"])
        self.assertEqual(detail["turns"], [])
        self.assertTrue(any("找不到会话记录文件" in item for item in detail["warnings"]))

    def test_missing_database_raises_viewer_error(self):
        store = server.CodexStore(str(Path(self.tmp.name) / "no-such-home"))
        with self.assertRaises(server.ViewerError):
            store.list_sessions()


if __name__ == "__main__":
    unittest.main()
