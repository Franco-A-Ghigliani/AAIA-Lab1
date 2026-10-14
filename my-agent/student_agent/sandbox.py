"""Bounded local Bash command execution."""

import os
import subprocess
import time
import selectors
import signal
from pathlib import Path


class SandboxError(Exception):
    pass


def is_link(path):
    """Reject symlinks and Windows junctions, including on Python 3.10."""
    return path.is_symlink() or bool(getattr(path.lstat() if path.exists() else None,
                                            "st_reparse_tag", 0))


class LocalBash:
    """Bounded local Bash execution on POSIX or Git Bash on Windows."""

    def __init__(self, root):
        self.root = Path(root)
        self.broken = False

    def run(self, command, timeout, limit):
        if os.name == "nt":
            from .windows_bash import run_git_bash
            try:
                return run_git_bash(self.root, command, timeout, limit)
            except OSError as error:
                self.broken = True
                raise SandboxError(f"Git Bash failed; further actions disabled: {error}") from error
        if os.name != "posix":
            raise SandboxError("local Bash requires POSIX/WSL or Git Bash on Windows")
        child = subprocess.Popen(
            ["/bin/bash", "--noprofile", "--norc", "-c", command],
            cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
                 "HOME": str(self.root), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        output = bytearray()
        reason = None
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        reason = f"timed out after {timeout}s"
                        break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), min(4096, limit + 1 - len(output)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            break
                        output.extend(chunk)
                        if len(output) > limit:
                            reason = f"output exceeded {limit} bytes"
                            break
                    if reason:
                        break
                if reason is None:
                    try:
                        child.wait(timeout=max(0.001, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        reason = f"timed out after {timeout}s"
        finally:
            # Kill the entire group, including children surviving their parent.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            child.stdout.close()
        result = {"exit_code": child.returncode,
                  "output": bytes(output[:limit]).decode("utf-8", errors="replace")}
        if reason:
            result["stopped"] = reason
        return result
