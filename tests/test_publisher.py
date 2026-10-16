"""Offline regression tests: only fictional content and mocked transports."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from yuque_note_publisher import publish_checkpoint as checkpoint
from yuque_note_publisher import update_history as history
from yuque_note_publisher import update_homepage as homepage
from yuque_note_publisher import yuque_note as note
from yuque_note_publisher.runtime import ledger_lock


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class BudgetTests(TemporaryTest):
    def ledger(self, budget):
        return note.UsageLedger(self.root / "usage.json", budget=budget)

    def test_zero_blocks_before_reservation(self):
        ledger = self.ledger(0)
        with self.assertRaisesRegex(RuntimeError, "LOCAL_DAILY_BUDGET_REACHED"):
            ledger.reserve_api_call()
        self.assertEqual(ledger.status()["tracked_calls"], 0)
        self.assertEqual(ledger.status()["tracked_remaining"], 0)

    def test_budget_boundary(self):
        ledger = self.ledger(2)
        ledger.reserve_api_call()
        ledger.reserve_api_call()
        with self.assertRaisesRegex(RuntimeError, "LOCAL_DAILY_BUDGET_REACHED"):
            ledger.reserve_api_call()
        self.assertEqual(ledger.load()["tracked_calls"], 2)

    def test_day_rollover(self):
        ledger = self.ledger(3)
        ledger.save({"date": "2000-01-01", "tracked_calls": 99})
        self.assertEqual(ledger.reserve_api_call()["tracked_calls"], 1)

    def test_corrupt_ledger_fails_closed(self):
        ledger = self.ledger(3)
        ledger.path.write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Cannot read"):
            ledger.reserve_api_call()

    def test_lock_prevents_concurrent_reservation(self):
        ledger = self.ledger(3)
        with ledger_lock(ledger.path):
            with self.assertRaisesRegex(RuntimeError, "USAGE_LEDGER_BUSY"):
                ledger.reserve_api_call()
        self.assertEqual(ledger.reserve_api_call()["tracked_calls"], 1)


class ClientTests(unittest.TestCase):
    def client(self):
        return note.YuqueClient("fictional-test-secret", min_interval=0, usage_ledger=Mock())

    def response(self, data=b'{"data": {"id": 1}}', content_type="application/json"):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = data
        response.headers = {"Content-Type": content_type}
        return response

    def test_auth_only_on_api(self):
        client = self.client()
        client.opener.open = Mock(return_value=self.response())
        client.request("/repos/demo/book/docs/one")
        req = client.opener.open.call_args.args[0]
        self.assertEqual(req.get_header("X-auth-token"), "fictional-test-secret")
        client.opener.open.return_value = self.response(b"image", "image/png")
        client.request("https://images.example.org/note.png", binary=True)
        req = client.opener.open.call_args.args[0]
        self.assertIsNone(req.get_header("X-auth-token"))
        self.assertEqual(client.usage_ledger.reserve_api_call.call_count, 1)

    def test_api_prefix_confusion(self):
        self.assertFalse(note.is_api_url("https://www.yuque.com/api/v2.evil/image.png"))
        self.assertFalse(note.is_api_url("https://www.yuque.com.evil/api/v2/docs"))

    def test_authenticated_cross_origin_redirect_blocked(self):
        req = urllib.request.Request(note.API_BASE + "/repos/demo/book/docs/one")
        req.add_header("X-Auth-Token", "fictional-test-secret")
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            note.SafeRedirectHandler().redirect_request(
                req, None, 302, "Found", {}, "https://images.example.org/redirect"
            )

    def test_network_error_no_retry_and_counted(self):
        client = self.client()
        client.opener.open = Mock(side_effect=urllib.error.URLError("offline"))
        with self.assertRaisesRegex(RuntimeError, "YUQUE_NETWORK_ERROR"):
            client.request("/repos/demo/book/docs/one")
        self.assertEqual(client.opener.open.call_count, 1)
        client.usage_ledger.reserve_api_call.assert_called_once()

    def test_server_error_no_retry(self):
        client = self.client()
        client.opener.open = Mock(side_effect=urllib.error.HTTPError("url", 503, "offline", {}, None))
        with self.assertRaisesRegex(RuntimeError, "YUQUE_SERVER_ERROR"):
            client.request("/repos/demo/book/docs/one")
        self.assertEqual(client.opener.open.call_count, 1)

    def test_short_429_retries_once_and_counts_both(self):
        client = self.client()
        error = urllib.error.HTTPError("url", 429, "rate limit", {"Retry-After": "0"}, None)
        client.opener.open = Mock(side_effect=[error, self.response()])
        with patch.object(note.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(client.request("/repos/demo/book/docs/one")["id"], 1)
        self.assertEqual(client.usage_ledger.reserve_api_call.call_count, 2)

    def test_long_429_stops(self):
        client = self.client()
        client.opener.open = Mock(side_effect=urllib.error.HTTPError(
            "url", 429, "rate limit", {"Retry-After": "61"}, None
        ))
        with self.assertRaisesRegex(RuntimeError, "YUQUE_RATE_LIMITED"):
            client.request("/repos/demo/book/docs/one")
        self.assertEqual(client.opener.open.call_count, 1)

    def test_image_download_may_retry(self):
        client = self.client()
        client.opener.open = Mock(side_effect=[
            urllib.error.URLError("offline"), self.response(b"image", "image/png")
        ])
        with patch.object(note.time, "sleep"):
            self.assertEqual(client.request("https://images.example.org/a.png", True)[0], b"image")
        client.usage_ledger.reserve_api_call.assert_not_called()

    def test_precise_url_and_namespace_validation(self):
        self.assertEqual(note.parse_yuque_url("https://www.yuque.com/demo/book/one?x=1"), ("demo/book", "one"))
        for url in ["http://www.yuque.com/a/b/c", "https://evil.example/a/b/c", "https://www.yuque.com/a/b", "https://www.yuque.com/a/b/c/d"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                note.parse_yuque_url(url)
        with self.assertRaises(ValueError):
            note.validate_namespace("../all")

    def test_pagination_is_explicitly_scoped(self):
        client = self.client()
        client.request = Mock(side_effect=[[{"id": i} for i in range(100)], [{"id": 101}]])
        self.assertEqual(len(client.docs("demo/book")), 101)
        self.assertIn("offset=100", client.request.call_args.args[0])


class MediaTests(TemporaryTest):
    def test_md_and_html_localized_and_deduplicated(self):
        client = Mock()
        client.request.return_value = (b"fictional-image", "image/png")
        text = '![](https://images.example.org/a.png) <img src="https://images.example.org/a.png">'
        result, count = note.localize_images(client, text, self.root / "note.md", self.root / "assets")
        self.assertEqual(count, 1)
        self.assertNotIn("https://", result)
        self.assertEqual(client.request.call_count, 1)
        self.assertEqual(len(list((self.root / "assets").glob("*.png"))), 1)

    def test_non_image_response_rejected(self):
        client = Mock()
        client.request.return_value = (b"login page", "text/html")
        with self.assertRaisesRegex(RuntimeError, "non-image"):
            note.localize_images(client, '![](https://images.example.org/a)', self.root / "note.md", self.root / "assets")

    def test_resume_after_media_failure_without_second_api_call(self):
        client = Mock()
        client.doc.return_value = {
            "id": 1, "slug": "one", "body": '# Demo\n![](https://images.example.org/a.png)',
            "created_at": "2026-01-01T00:00:00Z",
        }
        client.request.side_effect = RuntimeError("media offline")
        output = self.root / "note.md"
        with self.assertRaisesRegex(RuntimeError, "media offline"):
            note.fetch(client, "demo/book", "one", str(output), str(self.root / "assets"))
        self.assertTrue(output.with_suffix(".md.source-cache.json").exists())
        client.request.side_effect = None
        client.request.return_value = (b"image", "image/png")
        result = note.fetch(client, "demo/book", "one", str(output), str(self.root / "assets"))
        self.assertTrue(result["resumed_from_cache"])
        client.doc.assert_called_once()
        self.assertFalse(output.with_suffix(".md.source-cache.json").exists())
        self.assertTrue(output.with_suffix(".meta.json").exists())

    def test_cache_cannot_be_used_for_other_document(self):
        client = Mock()
        client.doc.return_value = {"body": ""}
        output = self.root / "note.md"
        with self.assertRaisesRegex(RuntimeError, "empty"):
            note.fetch(client, "demo/book", "one", str(output), str(self.root / "assets"))
        with self.assertRaisesRegex(RuntimeError, "different document"):
            note.fetch(client, "demo/book", "two", str(output), str(self.root / "assets"))

    def test_manifest_cannot_escape_assets(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"https://images.example.org/a": "../private.txt"}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "escapes"):
            note.localize_images(Mock(), '![](https://images.example.org/a)', self.root / "note.md", self.root / "assets", manifest)


class HistoryTests(unittest.TestCase):
    def test_append_is_idempotent(self):
        first, changed = history.update_history("# Demo", "2026-01-02", "首次发布", "从语雀整理并发布到学习记录仓库")
        self.assertTrue(changed)
        again, changed = history.update_history(first, "2026-01-02", "首次发布", "从语雀整理并发布到学习记录仓库")
        self.assertFalse(changed)
        self.assertEqual(first, again)

    def test_malformed_markers_and_unmanaged_heading_rejected(self):
        for text in [history.START, history.END + history.START, "## 更新记录"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                history.update_history(text, "2026-01-02", "内容更新", "补充示例")

    def test_author_voice_enforced(self):
        with self.assertRaisesRegex(ValueError, "author"):
            history.update_history("# Demo", "2026-01-02", "内容更新", "按用户要求修复")

    def test_maintenance_requires_explicit_exception(self):
        with self.assertRaisesRegex(ValueError, "Maintenance"):
            history.update_history("# Demo", "2026-01-02", "格式维护", "目录调整")

    def test_table_cells_are_escaped(self):
        result, _ = history.update_history("# Demo", "2026-01-02", "内容更新", "A|B\nC")
        self.assertIn(r"A\|B C", result)


class NavigationTests(TemporaryTest):
    def test_external_readme_links_do_not_enter_navigation(self):
        directory = self.root / "topic"
        directory.mkdir()
        (self.root / "outside.md").write_text("# External", encoding="utf-8")
        (directory / "README.md").write_text("[External](../outside.md)", encoding="utf-8")
        self.assertEqual(homepage.parse_readme_links(directory), [])

    def test_unknown_domain_discovered_and_readme_labels_reused(self):
        domain = self.root / "自定义领域"
        domain.mkdir()
        (domain / "2026-01-02-long-name.md").write_text("# Demo", encoding="utf-8")
        (domain / "README.md").write_text("[自然展示名](2026-01-02-long-name.md)", encoding="utf-8")
        (domain / "scratch.md").write_text("not a note", encoding="utf-8")
        result, count = homepage.build_navigation(self.root)
        self.assertEqual(count, 1)
        self.assertIn("自然展示名", result)
        self.assertIn("自定义领域", result)

    def test_readme_outside_marker_preserved(self):
        readme = self.root / "README.md"
        readme.write_text("intro\n" + homepage.START + "\nold\n" + homepage.END + "\nfooter", encoding="utf-8")
        self.assertEqual(homepage.render(readme, "new"), "intro\n" + homepage.START + "\nnew\n" + homepage.END + "\nfooter")

    def test_reversed_markers_rejected(self):
        readme = self.root / "README.md"
        readme.write_text(homepage.END + homepage.START, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "markers"):
            homepage.render(readme, "new")


class CheckpointTests(TemporaryTest):
    def invoke(self, *args):
        argv = ["yuque-checkpoint", "--state-file", str(self.root / "checkpoint.json"), *args]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return checkpoint.main()

    def test_three_note_rounds_and_safe_clear(self):
        self.assertEqual(self.invoke("start", "--github-repo", "demo/notes", "--selection", "A", "--selection", "B", "--selection", "C", "--selection", "D"), 0)
        self.assertEqual(self.invoke("clear"), 1)
        self.assertEqual(self.invoke("advance", "--completed-selection", "A"), 1)
        self.assertEqual(self.invoke("advance", "--completed-selection", "A", "--completed-selection", "B", "--completed-selection", "C"), 0)
        state = checkpoint.load(self.root / "checkpoint.json")
        self.assertEqual(state["pending"]["current_batch"], ["D"])
        self.assertEqual(self.invoke("advance", "--completed-selection", "D"), 0)
        self.assertEqual(self.invoke("clear"), 0)

    def test_duplicate_selections_rejected(self):
        self.assertEqual(self.invoke("start", "--github-repo", "demo/notes", "--selection", "A", "--selection", "A"), 1)

    def test_existing_checkpoint_not_overwritten(self):
        args = ("start", "--github-repo", "demo/notes", "--selection", "A")
        self.assertEqual(self.invoke(*args), 0)
        self.assertEqual(self.invoke(*args), 1)


class EntrypointTests(TemporaryTest):
    def test_package_help_entrypoints(self):
        for module in ["yuque_note", "publish_checkpoint", "update_history", "update_homepage"]:
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, "-m", "yuque_note_publisher." + module, "--help"], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_self_contained_script_without_install_imports(self):
        script = Path(__file__).resolve().parents[1] / "skills/yuque-note-publisher/scripts/yuque_note.py"
        env = os.environ.copy()
        env["YNP_STATE_DIR"] = str(self.root / "private-state")
        result = subprocess.run([sys.executable, str(script), "usage"], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["tracked_calls"], 0)


if __name__ == "__main__":
    unittest.main()
