"""Documentation boundaries and local Bash lifecycle checks."""
import os
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

from student_agent import Runtime, run_agent
from student_agent.sandbox import LocalBash


class DocumentationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = Runtime(self.root)
        self.url = "https://docs.python.org/3/library/decimal.html"

    def fetch(self, url=None):
        return self.runtime.execute({"tool": "fetch_url", "args": {"url": url or self.url}})

    def test_live_bounded_text_and_truncation(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = Message()
        response.headers["Content-Type"] = "text/html"
        response.read.return_value = b"x" * 12001
        with mock.patch("student_agent.runtime._open_url", return_value=response):
            result = self.fetch()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["output"]["truncated"])
        self.assertEqual(len(result["output"]["content"]), 12000)
        response.read.assert_called_once_with(12001)

    def test_network_error_becomes_loop_observation(self):
        replies = iter(['{"tool":"fetch_url","args":{"url":"' + self.url + '"}}', '{"final":"network unavailable"}'])
        histories = []
        def model(messages):
            histories.append(list(messages))
            return next(replies)
        with mock.patch("student_agent.runtime._open_url", side_effect=urllib.error.URLError("offline")):
            outcome = run_agent(model, self.runtime, "inspect")
        self.assertEqual(outcome["termination"], "final")
        self.assertIn("network error", histories[1][-1]["content"])

    def test_offline_label_and_url_validation(self):
        fixture = self.root / "fixture.txt"
        fixture.write_text("Decimal rounding documentation")
        with Runtime(self.root, offline=fixture) as runtime:
            self.runtime = runtime
            result = self.fetch()["output"]
            self.assertIn("offline fixture", result["source"])
            self.assertEqual(result["source_url"], self.url)
            self.assertEqual(self.fetch("http://docs.python.org")["status"], "denied")
            self.assertEqual(self.fetch("https://example.com")["status"], "denied")

    def test_redirect_handler_refuses_redirect(self):
        from student_agent.runtime import _NoRedirect, ToolError
        with self.assertRaises(ToolError):
            _NoRedirect().redirect_request(None, None, 302, "redirect", {}, self.url)


@unittest.skipUnless(os.name == "posix", "local Bash execution requires POSIX/WSL")
class LocalBashTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bash = LocalBash(self.root)

    def test_output_exit_cwd_and_environment(self):
        with mock.patch.dict(os.environ, {"API_KEY": "must-not-inherit"}):
            result = self.bash.run("printf 'hello\\n'; test -z \"$API_KEY\"; pwd; exit 3", 2, 12000)
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["output"], "hello\n" + str(self.root) + "\n")

    def test_timeout_and_excess_output(self):
        self.assertIn("timed out", self.bash.run("sleep 5", 0.1, 12000)["stopped"])
        result = self.bash.run("yes", 2, 12000)
        self.assertIn("exceeded", result["stopped"])
        self.assertEqual(len(result["output"]), 12000)

    def test_denial_does_not_create_marker(self):
        with Runtime(self.root, mode="edit", approve=lambda *_: False) as runtime:
            result = runtime.execute({"tool": "bash", "args": {"command": "touch marker.txt"}})
        self.assertEqual(result["status"], "denied")
        self.assertFalse((self.root / "marker.txt").exists())

    def test_cancellation_stops_process_group(self):
        with mock.patch("student_agent.sandbox.selectors.DefaultSelector") as factory:
            factory.return_value.__enter__.return_value.select.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                self.bash.run("sleep 5", 2, 12000)
