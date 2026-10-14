"""Permission policy checks using the public Runtime interface."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from student_agent import Runtime

PROTECTED = {"orders/validation.py", "tests/test_acceptance.py", "docs/business-rules.md"}


class ReadOnlyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def call(self, runtime, tool, **args):
        return runtime.execute({"tool": tool, "args": args})

    def test_protected_files_and_normalized_paths(self):
        with Runtime(self.root, mode="edit") as runtime:
            for path in PROTECTED:
                destination = self.root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("original\n", encoding="utf-8")
                for spelling in (path, "./" + path, path.replace("/", "//")):
                    for tool, extra in (
                        ("write_file", {"content": "replacement"}),
                        ("edit_file", {"old": "original", "new": "replacement"}),
                    ):
                        with self.subTest(tool=tool, path=spelling):
                            self.assertEqual(self.call(runtime, tool, path=spelling, **extra)["status"], "denied")
                            self.assertEqual(destination.read_text(), "original\n")
                self.assertEqual(self.call(runtime, "read_file", path=path)["status"], "ok")
                destination.unlink()
                self.assertEqual(self.call(runtime, "write_file", path=path, content="new")["status"], "denied")

    def test_direct_root_and_exact_edit(self):
        with Runtime(str(self.root), mode="edit") as runtime:
            self.assertEqual(self.call(runtime, "write_file", path="notes.txt", content="  one\n")["status"], "ok")
            self.assertEqual(self.call(runtime, "edit_file", path="notes.txt", old="one\nmissing", new="two")["status"], "error")
            self.assertEqual(self.call(runtime, "edit_file", path="notes.txt", old="one\n", new="two\n")["status"], "ok")
            self.assertEqual((self.root / "notes.txt").read_text(), "  two\n")
        self.assertTrue(self.root.exists())

    def test_indentation_is_not_recovered(self):
        path = self.root / "example.py"
        source = "if True:\n    value = 1\n"
        path.write_text(source)
        with Runtime(self.root, mode="edit") as runtime:
            result = self.call(runtime, "edit_file", path="example.py",
                               old="if True:\nvalue = 1\n", new="value = 2\n")
            self.assertEqual(result["status"], "error")
            self.assertEqual(path.read_text(), source)

    def test_mode_and_enabled_filter_prompt_and_dispatch(self):
        approve = mock.Mock(return_value=True)
        with Runtime(self.root, enabled={"read_file", "bash", "edit_file"}, approve=approve) as runtime:
            self.assertEqual([t["name"] for t in runtime.describe_tools()], ["read_file"])
            self.assertEqual(self.call(runtime, "bash", command="true")["status"], "denied")
            self.assertEqual(self.call(runtime, "list_files")["status"], "denied")
            self.assertEqual(self.call(runtime, "unknown")["status"], "error")
            approve.assert_not_called()

    def test_file_listing_limit(self):
        for index in range(201):
            (self.root / f"file-{index:03}.txt").write_text("text")
        with Runtime(self.root) as runtime:
            result = self.call(runtime, "list_files")["output"]
            self.assertEqual(len(result["paths"]), 200)
            self.assertTrue(result["truncated"])


class ApprovalTest(unittest.TestCase):
    def test_only_exact_yes_approves(self):
        from student_agent.cli import ask_approval
        for answer in ("y", "", "YES", "yes ", "no", "yes"):
            with self.subTest(answer=answer), mock.patch("builtins.input", return_value=answer) as prompt, mock.patch("builtins.print"):
                self.assertEqual(ask_approval("printf hello", "/workspace"), answer == "yes")
                prompt.assert_called_once_with("Approve this command only? Type yes: ")
        with mock.patch("builtins.input", side_effect=EOFError), mock.patch("builtins.print"):
            self.assertFalse(ask_approval("true", "/workspace"))
