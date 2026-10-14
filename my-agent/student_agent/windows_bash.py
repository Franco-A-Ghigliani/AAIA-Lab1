"""Git Bash process lifecycle on Windows; approval is enforced by Runtime."""
import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", w.DWORD), ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t), ("active", w.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class _Limits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("tid", w.DWORD),
                ("pid", w.DWORD), ("base_priority", w.LONG),
                ("delta_priority", w.LONG), ("flags", w.DWORD)]


class _Accounting(ctypes.Structure):
    _fields_ = [("times", ctypes.c_longlong * 4), ("page_faults", w.DWORD),
                ("total", w.DWORD), ("active", w.DWORD), ("terminated", w.DWORD)]


class _ProcessList(ctypes.Structure):
    _fields_ = [("assigned", w.DWORD), ("count", w.DWORD),
                ("ids", ctypes.c_size_t * 256)]


class _Job:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           w.DWORD, ctypes.c_void_p], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, ctypes.POINTER(_ThreadEntry)], w.BOOL),
            "Thread32Next": ([w.HANDLE, ctypes.POINTER(_ThreadEntry)], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, child):
        # Popen starts suspended: the shell cannot spawn children before attachment.
        if not self.api.AssignProcessToJobObject(self.handle, int(child._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.pid == child.pid:
                    thread = self.api.OpenThread(2, False, entry.tid)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.api.ResumeThread(thread) == 0xffffffff:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.api.CloseHandle(thread)
                    return
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("could not find suspended bash thread")
        finally:
            self.api.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            processes = []
            try:
                listed = _ProcessList()
                if not self.api.QueryInformationJobObject(
                        self.handle, 3, ctypes.byref(listed), ctypes.sizeof(listed), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                for pid in listed.ids[:listed.count]:
                    process = self.api.OpenProcess(0x100000, False, pid)
                    if process:
                        processes.append(process)
                if not self.api.TerminateJobObject(self.handle, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
                # Termination is asynchronous; wait until descendants release cwd/pipes.
                deadline = time.monotonic() + 5
                while True:
                    accounting = _Accounting()
                    if not self.api.QueryInformationJobObject(
                            self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if not accounting.active:
                        break
                    if time.monotonic() >= deadline:
                        raise OSError("could not confirm Git Bash descendant cleanup")
                    time.sleep(0.01)
                for process in processes:
                    remaining = max(0, int((deadline - time.monotonic()) * 1000))
                    if self.api.WaitForSingleObject(process, remaining) != 0:
                        raise OSError("Git Bash child did not finish termination")
            finally:
                for process in processes:
                    self.api.CloseHandle(process)
                if not self.api.CloseHandle(self.handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handle = None


def find_git_bash():
    candidates = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/bin/bash.exe"]
    for path in candidates:
        if path.is_file():
            return path
    raise OSError("Git Bash not found; install Git for Windows")


def run_git_bash(root, command, timeout, limit):
    bash = find_git_bash()
    git_root = bash.parent.parent
    env = {"PATH": os.pathsep.join([str(Path(sys.executable).parent),
                                   str(git_root / "bin"), str(git_root / "usr/bin")]),
           "HOME": str(root), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "SystemRoot": os.environ.get("SystemRoot", "C:/Windows")}
    job = _Job()
    child = None
    reader = None
    stopped = threading.Event()
    chunks = queue.Queue(maxsize=4)
    output = bytearray()
    reason = None
    deadline = time.monotonic() + timeout
    try:
        child = subprocess.Popen([str(bash), "--noprofile", "--norc", "-c", command],
                                 cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,
                                 creationflags=0x4 | subprocess.CREATE_NO_WINDOW, env=env)
        job.attach_and_resume(child)

        def read_output():
            while not stopped.is_set():
                try:
                    chunk = os.read(child.stdout.fileno(), 4096)
                except OSError as error:
                    chunk = error
                while not stopped.is_set():
                    try:
                        chunks.put(chunk, timeout=0.05)
                        break
                    except queue.Full:
                        continue
                if not chunk or isinstance(chunk, OSError):
                    break

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = f"timed out after {timeout}s"
                break
            try:
                chunk = chunks.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if isinstance(chunk, OSError):
                raise chunk
            if not chunk:
                try:
                    child.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    reason = f"timed out after {timeout}s"
                break
            output.extend(chunk[:limit + 1 - len(output)])
            if len(output) > limit:
                reason = f"output exceeded {limit} bytes"
                break
    finally:
        stopped.set()
        try:
            job.close()  # Kills bash and its descendants on every exit, including Ctrl+C.
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()  # Also handles attachment/resume failure.
                child.wait(timeout=5)
                if reader is not None:
                    reader.join(timeout=5)
                    if reader.is_alive():
                        raise OSError("could not confirm Git Bash output-reader cleanup")
                child.stdout.close()
                # Release the Windows process object before the caller removes cwd.
                child._handle.Close()
    result = {"exit_code": child.returncode,
              "output": bytes(output[:limit]).decode("utf-8", errors="replace")}
    if reason:
        result["stopped"] = reason
    return result
