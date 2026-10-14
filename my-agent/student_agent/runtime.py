import os
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

if __package__:
    from . import tool_repository
    from .sandbox import LocalBash, SandboxError, is_link
else:
    import tool_repository
    from sandbox import LocalBash, SandboxError, is_link

LIMIT = 12_000
MAX_LIST = 200
MAX_MATCHES = 30
BASH_TIMEOUT = 20
FETCH_TIMEOUT = 10
MUTATING = {"write_file", "edit_file", "bash"}

class Denied(Exception):
    """Policy refused the request."""

class ToolError(Exception):
    """The request was allowed but could not be completed."""

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ToolError("redirects are not allowed")

def _open_url(url):
    """Open an HTTPS URL without following redirects (patched in tests)."""
    opener = urllib.request.build_opener(_NoRedirect)
    return opener.open(urllib.request.Request(url, method="GET"), timeout=FETCH_TIMEOUT)


class Runtime:
    """Validate and execute tools."""

    def __init__(self, root, mode="read-only", enabled=None, approve=None, *, offline=None):
        if mode not in {"read-only", "edit"}:
            raise ValueError("mode must be read-only or edit")
        source = Path(root).resolve(strict=True)
        if not source.is_dir():
            raise ValueError("root must be a directory")
        protected = {
            "orders/validation.py", "tests/test_acceptance.py", "docs/business-rules.md"}
        self.root = source
        self.read_only_files = frozenset(self._resolve_path(path) for path in protected)
        self.read_only = mode == "read-only"
        self.sandbox = LocalBash(self.root)
        self.approve = approve
        self.bash_timeout = BASH_TIMEOUT
        self.registry = tool_repository.bind_tools(self)
        self.tools = [tool for tool in tool_repository.describe_tools()
                      if (enabled is None or tool["name"] in enabled)
                      and not (self.read_only and tool["name"] in MUTATING)]
        self.offline = Path(offline) if offline else None

    def close(self):
        """Each bash call cleans up its own processes."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    #POLICIES
    def tool_allowed(self, name):
        """True when the tool is enabled."""
        return any(tool["name"] == name for tool in self.tools)

    def execute(self, action):
        """Execute the given action."""
        try:
            if self.sandbox.broken:
                raise Denied("sandbox cleanup failed; further actions disabled")
            if not isinstance(action, dict):
                raise ToolError('action must be {"tool": str, "args": object}')

            if set(action) != {"tool", "args"}:
                raise ToolError('action must be {"tool": str, "args": object}')

            name = action["tool"]
            args = action["args"]

            if not isinstance(name, str):
                raise ToolError("tool name must be a string")

            if name not in self.registry:
                raise ToolError(f"unknown tool: {name!r}")

            if self.read_only and name in MUTATING:
                raise Denied(f"cannot access tool in read-only mode: {name!r}")
            if not self.tool_allowed(name):
                raise Denied(f"tool is disabled: {name!r}")

            expected_args = self.registry[name]["args"]
            argument_error = f"{name} takes exactly these arguments: {expected_args}"

            if not isinstance(args, dict):
                raise ToolError(argument_error)

            if set(args) != set(expected_args):
                raise ToolError(argument_error)

            for key, value in args.items():
                error_message = (
                    f"argument {key!r} must be a string "
                    f"of at most {LIMIT} chars"
                )

                if not isinstance(value, str) or len(value) > LIMIT:
                    raise ToolError(error_message)

            # Bash is checked separately by the explicit human approval gate.
            if name in MUTATING and name != "bash" and self.read_only_files:
                destination = self._resolve_path(args["path"])
                if destination in self.read_only_files:
                    raise Denied(f"read-only file: {args['path']}")

            handler = self.registry[name]["function"]
            result = handler(**args)
            return {"status": "ok", "output": result}

        except Denied as error:
            return {
                "status": "denied",
                "output": str(error),
            }

        except (ToolError, SandboxError, subprocess.SubprocessError) as error:
            return {
                "status": "error",
                "output": error.args[0] if error.args else str(error),
            }

        except (OSError, UnicodeError) as error:
            return {
                "status": "error",
                "output": f"{error.__class__.__name__}: {error}",
            }

    def describe_tools(self):
        return self.tools

    def _resolve_path(self, path):
        """Map a relative path to a real path inside root, or raise Denied."""
        pure_path = PurePosixPath(path)

        if (not path or "\\" in path or ":" in path or "\0" in path
                or pure_path.is_absolute() or Path(path).is_absolute()):
            raise Denied("path must be relative to root using forward slashes")
        if any(part == ".." or part.startswith(".") for part in pure_path.parts):
            raise Denied("'..' and hidden directories are not allowed")
        real_path = (self.root / pure_path).resolve()
        for entry in [(self.root / pure_path), *(self.root / pure_path).parents]:
            if entry == self.root:
                break
            if is_link(entry):
                raise Denied("links are not allowed")
        try:
            real_path.relative_to(self.root).as_posix()
        except ValueError:
            raise Denied("path is outside the workspace") from None

        return real_path

    def _walk_workspace(self):
        """Return relative paths of visible files, without following links."""
        for path, dir_names, file_names in os.walk(self.root, followlinks=False):
            visible_dirs = []
            for dirname in dir_names:
                directory = Path(path) / dirname
                if (not dirname.startswith(".") and not is_link(directory)):
                    visible_dirs.append(dirname)
            dir_names[:] = sorted(visible_dirs)

            for filename in sorted(file_names):
                if filename.startswith(".") or (Path(path) / filename).is_symlink():
                    continue
                file_path = Path(path) / filename
                if not stat.S_ISREG(file_path.lstat().st_mode):
                    continue
                yield file_path.relative_to(self.root).as_posix()

    #TOOL HANDLERS
    def _list_files(self):
        paths = []
        for index, path in enumerate(self._walk_workspace()):
            if index < MAX_LIST:
                paths.append(path)
                continue
            return {"paths": paths, "truncated": True}
        return paths

    def _read_file(self, path):
        location = self._resolve_path(path)
        if not location.is_file():
            raise ToolError(f"not a file: {path}")
        try:
            with location.open("r", encoding="utf-8") as stream:
                contents = stream.read(LIMIT + 1)
        except UnicodeDecodeError:
            raise ToolError(f"not UTF-8 text: {path}") from None
        if len(contents) <= LIMIT:
            return contents
        return f"{contents[:LIMIT]}\n[truncated: showing first {LIMIT} characters]"

    def _search_files(self, query):
        if query == "":
            raise ToolError("query must not be empty")
        found = []
        for index, path in enumerate(self._walk_workspace()):
            if index >= MAX_LIST or len(found) >= MAX_MATCHES:
                break
            try:
                location = self._resolve_path(path)
                if not location.is_file():
                    continue
                with location.open("rb") as stream:
                    contents = stream.read(1_000_000)
                if b"\x00" in contents:
                    continue
                lines = contents.decode("utf-8").splitlines()
            except (Denied, OSError, UnicodeDecodeError):
                continue
            for index, line in enumerate(lines, start=1):
                if query not in line:
                    continue
                found.append(dict(path=path, line=index, excerpt=line.strip()[:200]))
                if len(found) >= MAX_MATCHES:
                    break
        return found

    def _write_file(self, path, content):
        destination = self._resolve_path(path)
        self._validate_change(path, content, f"content exceeds {LIMIT} bytes")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open(mode="x", encoding="utf-8") as stream:
                stream.write(content)
        except FileExistsError:
            raise ToolError(f"{path} already exists; use edit_file") from None
        return f"created {path}"

    def _edit_file(self, path, old, new):
        destination = self._resolve_path(path)
        if not destination.is_file():
            raise ToolError(f"not a file: {path}")
        if destination.stat().st_size > LIMIT:
            raise ToolError(f"{path} is larger than {LIMIT} bytes")
        if old == "":
            raise ToolError("old text must not be empty")
        contents = destination.read_text(encoding="utf-8")
        occurrences = contents.count(old)
        if occurrences != 1:
            raise ToolError(f"old text occurs {occurrences} times; it must occur exactly once")
        contents = contents.replace(old, new, 1)
        self._validate_change(path, contents, f"edited file would exceed {LIMIT} bytes")
        destination.write_text(contents, encoding="utf-8")
        return f"edited {path}"
    @staticmethod
    def _validate_change(path, contents, size_error):
        if len(contents.encode("utf-8")) > LIMIT:
            raise ToolError(size_error)

    def _fetch_url(self, url):
        try:
            address = urlsplit(url)
            port = address.port
        except ValueError:
            raise Denied("invalid URL or port") from None
        permitted = (
            address.scheme == "https"
            and address.hostname == "docs.python.org"
            and address.username is None and address.password is None
            and port in (None, 443)
            and "?" not in url and "#" not in url
        )
        if not permitted:
            raise Denied("only https://docs.python.org URLs without credentials, "
                         "query, or fragment are allowed")
        if self.offline:
            with self.offline.open("rb") as stream:
                contents = stream.read(LIMIT + 1)
            origin = f"offline fixture ({self.offline.name}), not a live response"
        else:
            origin = "live docs.python.org"
            try:
                with _open_url(url) as response:
                    mime_type = response.headers.get_content_type()
                    if mime_type != "application/xhtml+xml" and not mime_type.startswith("text/"):
                        raise ToolError(f"unsupported content type: {mime_type}")
                    contents = response.read(LIMIT + 1)
            except urllib.error.HTTPError as error:
                raise ToolError(f"HTTP {error.code} from {url}") from None
            except (urllib.error.URLError, TimeoutError) as error:
                raise ToolError(f"network error: {error}") from None
        return dict(source_url=url, source=origin, truncated=len(contents) > LIMIT,
                    content=contents[:LIMIT].decode("utf-8", errors="replace"))

    def _bash(self, command):
        if self.approve is None or self.approve(command, str(self.root)) is not True:
            raise Denied("bash requires explicit command approval")
        result = self.sandbox.run(command, self.bash_timeout, LIMIT)
        if "stopped" in result:
            raise ToolError(result)
        return result


