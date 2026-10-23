# -*- coding: utf-8 -*-
"""HTTP 接口、静态页面和只读行为测试。"""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402


FIXTURE = ROOT / "fixtures" / "codex-home"
FIXTURE_ID = "01a14de0-1bbb-7790-b839-372fc9363740"
ARCHIVED_ID = "01a14de0-0000-7000-8000-00000000a2c1"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = server.CodexStore(str(FIXTURE))
        cls.httpd = server.make_server(cls.store, port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)

    def request(self, path, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, headers={"Accept": "application/json"})
            response = conn.getresponse()
            body = response.read()
            headers = {key.lower(): value for key, value in response.getheaders()}
            return response.status, headers, body
        finally:
            conn.close()

    def get_json(self, path):
        status, headers, body = self.request(path)
        return status, headers, json.loads(body.decode("utf-8"))

    def test_index_page(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["content-type"])
        self.assertIn("content-security-policy", headers)
        text = body.decode("utf-8")
        self.assertIn("Codex 会话看板", text)
        self.assertIn("/static/app.js", text)

    def test_head_request(self):
        status, headers, body = self.request("/", method="HEAD")
        self.assertEqual(status, 200)
        self.assertGreater(int(headers["content-length"]), 0)
        self.assertEqual(body, b"")

    def test_static_files(self):
        status, headers, body = self.request("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["content-type"])
        self.assertIn("loadSessions", body.decode("utf-8"))

        status, headers, body = self.request("/static/app.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["content-type"])

    def test_health(self):
        status, _, data = self.get_json("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["database_exists"])
        self.assertIn("state_5.sqlite", data["database"])

    def test_session_list(self):
        status, _, data = self.get_json("/api/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        session = data["sessions"][0]
        self.assertEqual(session["id"], FIXTURE_ID)
        self.assertIn("fruits.txt", session["title"])
        self.assertEqual(session["source_label"], "命令行")
        self.assertEqual(session["tokens_used"], 97600)

    def test_session_filters(self):
        _, _, data = self.get_json("/api/sessions?source=exec")
        self.assertEqual(data["count"], 1)
        _, _, data = self.get_json("/api/sessions?source=vscode")
        self.assertEqual(data["count"], 0)
        _, _, data = self.get_json("/api/sessions?q=fruits")
        self.assertEqual(data["count"], 1)
        _, _, data = self.get_json("/api/sessions?q=%E4%B8%8D%E5%AD%98%E5%9C%A8")
        self.assertEqual(data["count"], 0)

    def test_archived_filter(self):
        _, _, data = self.get_json("/api/sessions?archived=1")
        self.assertEqual(data["count"], 2)
        self.assertIn(ARCHIVED_ID, {item["id"] for item in data["sessions"]})
        _, _, data = self.get_json("/api/sessions?archived=0")
        self.assertEqual(data["count"], 1)

    def test_session_detail(self):
        status, _, data = self.get_json("/api/sessions/%s" % FIXTURE_ID)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["turns"]), 2)
        self.assertEqual(data["warnings"], [])
        self.assertTrue(data["source_file"]["exists"])
        tool_rows = [
            row
            for turn in data["turns"]
            for row in turn["rows"]
            if row["kind"] == "tool"
        ]
        self.assertEqual(len(tool_rows), 2)
        self.assertTrue(all(not row["tool"]["output_missing"] for row in tool_rows))

    def test_archived_compressed_detail(self):
        status, _, data = self.get_json("/api/sessions/%s" % ARCHIVED_ID)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["turns"]), 2)
        self.assertEqual(data["warnings"], [])
        self.assertTrue(data["source_file"]["exists"])
        self.assertTrue(data["source_file"]["compressed"])
        self.assertTrue(data["source_file"]["path"].endswith(".jsonl.zst"))

    def test_unknown_session_returns_404_json(self):
        status, headers, data = self.get_json("/api/sessions/not-a-session")
        self.assertEqual(status, 404)
        self.assertIn("application/json", headers["content-type"])
        self.assertIn("会话不存在", data["error"])

    def test_path_traversal_is_blocked(self):
        status, _, body = self.request("/static/../server.py")
        self.assertEqual(status, 404)
        self.assertNotIn(b"ViewerHandler", body)

        status, _, data = self.get_json("/api/sessions/..%2F..%2Fserver.py")
        self.assertEqual(status, 404)
        self.assertIn("会话不存在", data["error"])

    def test_read_only_during_requests(self):
        before = sorted(str(path.relative_to(FIXTURE)) for path in FIXTURE.rglob("*"))
        db_mtime = (FIXTURE / "state_5.sqlite").stat().st_mtime_ns

        self.get_json("/api/health")
        self.get_json("/api/sessions")
        self.get_json("/api/sessions/%s" % FIXTURE_ID)

        after = sorted(str(path.relative_to(FIXTURE)) for path in FIXTURE.rglob("*"))
        self.assertEqual(before, after)
        self.assertEqual(db_mtime, (FIXTURE / "state_5.sqlite").stat().st_mtime_ns)

    def test_missing_database_is_reported(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = server.CodexStore(str(Path(tmp.name) / "empty-home"))
        httpd = server.make_server(store, port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", "/api/health")
                health = json.loads(conn.getresponse().read().decode("utf-8"))
                self.assertFalse(health["database_exists"])

                conn.request("GET", "/api/sessions")
                response = conn.getresponse()
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 500)
                self.assertIn("找不到数据库", body["error"])
            finally:
                conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
