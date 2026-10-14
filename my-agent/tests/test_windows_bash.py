"""Real Git Bash lifecycle and harness approval tests on Windows."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from student_agent import Runtime, run_agent
from student_agent.sandbox import LocalBash


@unittest.skipUnless(os.name == "nt", "Git Bash tests require Windows")
class GitBashTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bash = LocalBash(self.root)

    def test_python_environment_stdin_and_exit_code(self):
        with mock.patch.dict(os.environ, {"API_KEY": "must-not-inherit"}):
            result = self.bash.run(
                "python -c 'import os, sys; print(os.getcwd()); "
                "print(os.environ.get(\"API_KEY\", \"absent\")); "
                "print(repr(sys.stdin.read())); sys.exit(3)'", 10, 12000)
        self.assertEqual(result["exit_code"], 3)
        lines = result["output"].splitlines()
        self.assertEqual(Path(lines[0]), self.root)
        self.assertEqual(lines[1:], ["absent", "''"])

    def test_timeout_and_output_limit(self):
        self.assertIn("timed out", self.bash.run("sleep 5", 0.2, 12000)["stopped"])
        result = self.bash.run("yes", 5, 12000)
        self.assertIn("exceeded", result["stopped"])
        self.assertLessEqual(len(result["output"].encode()), 12000)

    def test_timeout_stops_descendants(self):
        marker = self.root / "marker.txt"
        self.bash.run("(sleep 1; printf survived > marker.txt) & wait", 0.2, 12000)
        time.sleep(1.2)
        self.assertFalse(marker.exists())

    def test_cancel_stops_descendants(self):
        with mock.patch("student_agent.windows_bash.queue.Queue.get", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.bash.run("(sleep 1; printf survived > marker.txt) & wait", 5, 12000)
        time.sleep(1.2)
        self.assertFalse((self.root / "marker.txt").exists())

    def test_test_result_is_returned_to_model(self):
        (self.root / "tests").mkdir()
        (self.root / "tests/test_sample.py").write_text(
            "import unittest\nclass Sample(unittest.TestCase):\n"
            "    def test_failure(self):\n        self.fail('visible to model')\n")
        actions = iter(['{"tool":"bash","args":{"command":'
                        '"python -m unittest discover -s tests -v"}}', '{"final":"tests failed"}'])
        histories = []
        approvals = []
        def model(messages):
            histories.append(list(messages))
            return next(actions)
        def approve(command, cwd):
            approvals.append((command, cwd))
            return True
        with Runtime(self.root, mode="edit", approve=approve) as runtime:
            outcome = run_agent(model, runtime, "Run tests")
        self.assertEqual(outcome["termination"], "final")
        self.assertEqual(len(approvals), 1)
        self.assertIn("visible to model", histories[1][-1]["content"])
        self.assertIn('"exit_code": 1', histories[1][-1]["content"])
