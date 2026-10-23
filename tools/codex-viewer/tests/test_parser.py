# -*- coding: utf-8 -*-
"""rollout JSONL 解析测试。"""

import json
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
ROLLOUT = next((FIXTURE / "sessions").rglob("*.jsonl"))
ARCHIVED_ROLLOUT = next((FIXTURE / "archived_sessions").rglob("*.jsonl.zst"))
ARCHIVED_ID = "01a14de0-0000-7000-8000-00000000a2c1"


def _write_jsonl(directory, records):
    path = Path(directory) / "rollout-test.jsonl"
    lines = []
    for record in records:
        if isinstance(record, str):
            lines.append(record)
        else:
            lines.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _record(type_, payload, timestamp="2026-10-18T07:18:15.737Z"):
    return {"timestamp": timestamp, "type": type_, "payload": payload}


def _message(role, text, kinds=None):
    payload = {
        "type": "message",
        "role": role,
        "content": [{"type": "input_text", "text": text}],
    }
    if kinds is not None:
        payload["internal_chat_message_metadata_passthrough"] = {
            "content_item_kinds": kinds
        }
    return _record("response_item", payload)


class FixtureParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = server.parse_rollout(ROLLOUT)

    def test_fixture_has_two_turns(self):
        self.assertEqual(len(self.parsed["turns"]), 2)
        self.assertEqual(self.parsed["stats"]["turn_count"], 2)
        self.assertEqual([turn["number"] for turn in self.parsed["turns"]], [1, 2])
        self.assertEqual(self.parsed["warnings"], [])

    def test_turn_duration_and_tokens(self):
        first, second = self.parsed["turns"]
        self.assertEqual(first["duration_ms"], 7115)
        self.assertEqual(second["duration_ms"], 7336)
        self.assertIn("7.1", first["duration_display"])
        self.assertEqual(first["tokens"]["total_tokens"], 48599)
        self.assertEqual(second["tokens"]["total_tokens"], 49001)
        self.assertEqual(self.parsed["stats"]["total_tokens"], 97600)

    def test_label_classification(self):
        counts = self.parsed["stats"]["label_counts"]
        self.assertEqual(counts.get("system"), 3)
        self.assertEqual(counts.get("user"), 2)
        self.assertEqual(counts.get("context"), 7)
        self.assertEqual(counts.get("assistant"), 4)
        self.assertEqual(counts.get("tool"), 2)

        first_labels = [row["label"] for row in self.parsed["turns"][0]["rows"]]
        self.assertEqual(first_labels.count("系统"), 3)
        self.assertIn("上下文", first_labels)
        self.assertIn("用户", first_labels)
        self.assertIn("助手", first_labels)
        self.assertIn("工具", first_labels)

    def test_tool_calls_are_paired_with_outputs(self):
        tool_rows = [
            row
            for turn in self.parsed["turns"]
            for row in turn["rows"]
            if row["kind"] == "tool"
        ]
        self.assertEqual(len(tool_rows), 2)
        for row in tool_rows:
            self.assertFalse(row["tool"]["output_missing"])
            self.assertIsNotNone(row["tool"]["output"])
            self.assertFalse(row["tool"]["orphan"])
        self.assertEqual(tool_rows[0]["tool"]["name"], "exec")
        self.assertIn("cat fruits.txt", tool_rows[0]["tool"]["arguments"])
        self.assertIn("苹果 3", tool_rows[0]["tool"]["output"])
        self.assertIn("葡萄 4", tool_rows[1]["tool"]["output"])

    def test_session_meta(self):
        meta = self.parsed["meta"]
        self.assertEqual(meta["id"], "01a14de0-1bbb-7790-b839-372fc9363740")
        self.assertEqual(meta["cwd"], "/tmp/demo/codex-test")
        self.assertEqual(meta["cli_version"], "0.163.0")
        self.assertEqual(meta["source"], "exec")


class ArchivedFixtureParserTests(unittest.TestCase):
    """返工标准 8：压缩归档会话要能直接解析。"""

    @classmethod
    def setUpClass(cls):
        cls.parsed = server.parse_rollout(ARCHIVED_ROLLOUT)

    def test_archived_zst_has_two_turns(self):
        self.assertEqual(len(self.parsed["turns"]), 2)
        self.assertEqual(self.parsed["stats"]["turn_count"], 2)
        self.assertEqual(self.parsed["warnings"], [])

    def test_archived_zst_meta_tokens_and_tools(self):
        self.assertEqual(self.parsed["meta"]["id"], ARCHIVED_ID)
        self.assertEqual(self.parsed["turns"][0]["tokens"]["total_tokens"], 48599)
        self.assertEqual(self.parsed["turns"][1]["tokens"]["total_tokens"], 49001)
        tool_rows = [
            row
            for turn in self.parsed["turns"]
            for row in turn["rows"]
            if row["kind"] == "tool"
        ]
        self.assertEqual(len(tool_rows), 2)
        self.assertTrue(all(not row["tool"]["output_missing"] for row in tool_rows))

    def test_decompression_unavailable_message(self):
        with mock.patch("server.shutil.which", return_value=None):
            with self.assertRaises(server.ViewerError) as context:
                server.parse_rollout(ARCHIVED_ROLLOUT)
        message = str(context.exception)
        self.assertIn("zstd", message)
        self.assertIn("node", message)


class SyntheticParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_unknown_types_and_bad_lines_do_not_crash(self):
        path = _write_jsonl(
            self.tmp.name,
            [
                _record("session_meta", {"id": "x", "cwd": "/tmp/x"}),
                _record(
                    "event_msg",
                    {"type": "task_started", "turn_id": "t1", "started_at": 1},
                ),
                _message("developer", "系统提示"),
                "这不是 JSON",
                _record("mystery_type", {"foo": "bar"}),
                _record("response_item", {"type": "mystery_item", "foo": 1}),
                _record(
                    "event_msg",
                    {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "total_tokens": 5,
                                "input_tokens": 4,
                                "output_tokens": 1,
                            }
                        },
                    },
                ),
                _record(
                    "event_msg",
                    {"type": "task_complete", "turn_id": "t1", "duration_ms": 1234},
                ),
            ],
        )

        parsed = server.parse_rollout(path)
        self.assertEqual(len(parsed["warnings"]), 1)
        self.assertIn("第 4 行", parsed["warnings"][0])
        self.assertEqual(len(parsed["turns"]), 1)
        turn = parsed["turns"][0]
        self.assertEqual(turn["duration_ms"], 1234)
        self.assertEqual(turn["tokens"]["total_tokens"], 5)
        kinds = [row["kind"] for row in turn["rows"]]
        self.assertEqual(kinds.count("other"), 2)
        for row in turn["rows"]:
            if row["kind"] == "other":
                self.assertTrue(row["collapsed"])

    def test_label_rules_and_reasoning_encryption(self):
        path = _write_jsonl(
            self.tmp.name,
            [
                _record("session_meta", {"id": "labels", "cwd": "/tmp/labels"}),
                _record(
                    "event_msg",
                    {"type": "task_started", "turn_id": "t1", "started_at": 1},
                ),
                _message("system", "系统消息"),
                _message("developer", "开发者消息"),
                _message("user", "普通问题"),
                _message(
                    "user",
                    "<environment_context>\n  <cwd>/tmp/labels</cwd>\n</environment_context>",
                    ["environments.environment_context"],
                ),
                _message("assistant", "回答"),
                _record(
                    "response_item",
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "先想一下"}],
                        "encrypted_content": "SECRET-ENCRYPTED-BLOB",
                    },
                ),
                _record(
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": '{"cmd": "ls"}',
                        "call_id": "call-1",
                    },
                ),
                _record(
                    "response_item",
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": "a.txt\nb.txt",
                    },
                ),
                _record("turn_context", {"turn_id": "t1", "model": "gpt-test"}),
                _record("world_state", {"state": "工作区状态"}),
                _record(
                    "event_msg",
                    {"type": "thread_settings_applied", "thread_settings": "设置"},
                ),
                _record(
                    "event_msg",
                    {"type": "item_completed", "item": {"type": "UserMessage"}},
                ),
                _record("token_usage_record", {"usage": {"total_tokens": 99}}),
                _record(
                    "event_msg",
                    {
                        "type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 100}},
                    },
                ),
                _record("event_msg", {"type": "some_unknown_event"}),
                _record(
                    "event_msg",
                    {"type": "task_complete", "turn_id": "t1", "duration_ms": 2500},
                ),
            ],
        )

        parsed = server.parse_rollout(path)
        self.assertEqual(parsed["warnings"], [])
        rows = parsed["turns"][0]["rows"]
        kinds = [row["kind"] for row in rows]

        self.assertEqual(
            kinds[:8],
            [
                "system",
                "system",
                "user",
                "context",
                "assistant",
                "reasoning",
                "tool",
                "context",
            ],
        )
        self.assertEqual(kinds.count("context"), 4)  # environment + turn/world/settings
        self.assertEqual(kinds.count("other"), 1)  # 未知 event_msg

        reasoning = next(row for row in rows if row["kind"] == "reasoning")
        self.assertEqual(reasoning["text"], "先想一下")
        self.assertNotIn("SECRET", json.dumps(rows, ensure_ascii=False))

        tool = next(row for row in rows if row["kind"] == "tool")
        self.assertEqual(tool["tool"]["call_id"], "call-1")
        self.assertFalse(tool["tool"]["output_missing"])
        self.assertIn("a.txt", tool["tool"]["output"])

        for row in rows:
            if row["kind"] == "context":
                self.assertTrue(row["collapsed"])
            if row["kind"] == "other":
                self.assertTrue(row["collapsed"])
        self.assertEqual(parsed["turns"][0]["tokens"]["total_tokens"], 100)

    def test_custom_tool_call_with_list_output(self):
        path = _write_jsonl(
            self.tmp.name,
            [
                _record("session_meta", {"id": "custom", "cwd": "/tmp/custom"}),
                _record(
                    "event_msg",
                    {"type": "task_started", "turn_id": "t1", "started_at": 1},
                ),
                _record(
                    "response_item",
                    {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "input": "text(await tools.exec_command({cmd:'pwd'}));",
                        "call_id": "call-2",
                    },
                ),
                _record(
                    "response_item",
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call-2",
                        "output": [
                            {"type": "input_text", "text": "Script completed"},
                            {"type": "input_text", "text": "/tmp/custom"},
                        ],
                    },
                ),
                _record(
                    "event_msg",
                    {"type": "task_complete", "turn_id": "t1", "duration_ms": 100},
                ),
            ],
        )
        parsed = server.parse_rollout(path)
        tool_rows = [
            row for row in parsed["turns"][0]["rows"] if row["kind"] == "tool"
        ]
        self.assertEqual(len(tool_rows), 1)
        self.assertIn("Script completed", tool_rows[0]["tool"]["output"])
        self.assertIn("/tmp/custom", tool_rows[0]["tool"]["output"])

    def test_missing_file_raises_oserror(self):
        with self.assertRaises(FileNotFoundError):
            server.parse_rollout(Path(self.tmp.name) / "missing.jsonl")


if __name__ == "__main__":
    unittest.main()
