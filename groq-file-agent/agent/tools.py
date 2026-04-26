from __future__ import annotations

import html.parser
import json
import shutil
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config.settings import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB

_working_directory: Path = Path.cwd()


def set_working_directory(path: Path) -> None:
    global _working_directory
    _working_directory = path.resolve()


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (_working_directory / p).resolve()


def _check_extension(path: Path) -> Optional[str]:
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return f"Extension '{path.suffix}' is not allowed. Permitted: {ALLOWED_EXTENSIONS}"
    return None


def _check_size(path: Path) -> Optional[str]:
    size = path.stat().st_size
    if size > MAX_FILE_SIZE_BYTES:
        return f"File is {size / 1024 / 1024:.2f} MB, exceeding the {MAX_FILE_SIZE_MB} MB limit"
    return None


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: Optional[str] = None

    def to_json(self) -> str:
        if self.success:
            return json.dumps({"success": True, "result": self.data})
        return json.dumps({"success": False, "error": self.error})


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def read_file(path: str) -> ToolResult:
    try:
        fp = _resolve(path)
        if not fp.exists():
            return ToolResult(False, error=f"File not found: {fp}")
        if not fp.is_file():
            return ToolResult(False, error=f"Path is not a file: {fp}")
        if (err := _check_extension(fp)):
            return ToolResult(False, error=err)
        if (err := _check_size(fp)):
            return ToolResult(False, error=err)
        content = fp.read_text(encoding="utf-8")
        return ToolResult(True, data={
            "path": str(fp),
            "content": content,
            "size_bytes": fp.stat().st_size,
            "line_count": content.count("\n") + 1,
        })
    except PermissionError:
        return ToolResult(False, error=f"Permission denied: {path}")
    except UnicodeDecodeError:
        return ToolResult(False, error=f"File is not valid UTF-8 text: {path}")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def write_file(path: str, content: str, overwrite: bool = False) -> ToolResult:
    try:
        fp = _resolve(path)
        if (err := _check_extension(fp)):
            return ToolResult(False, error=err)
        if fp.exists() and not overwrite:
            return ToolResult(False, error=f"File already exists: {fp}. Set overwrite=true to replace it.")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return ToolResult(True, data={
            "path": str(fp),
            "bytes_written": len(content.encode("utf-8")),
            "line_count": content.count("\n") + 1,
        })
    except PermissionError:
        return ToolResult(False, error=f"Permission denied: {path}")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def append_to_file(path: str, content: str) -> ToolResult:
    try:
        fp = _resolve(path)
        if not fp.exists():
            return ToolResult(False, error=f"File not found: {fp}. Use write_file to create it first.")
        if not fp.is_file():
            return ToolResult(False, error=f"Path is not a file: {fp}")
        if (err := _check_extension(fp)):
            return ToolResult(False, error=err)
        with fp.open("a", encoding="utf-8") as fh:
            fh.write(content)
        return ToolResult(True, data={
            "path": str(fp),
            "bytes_appended": len(content.encode("utf-8")),
            "new_size_bytes": fp.stat().st_size,
        })
    except PermissionError:
        return ToolResult(False, error=f"Permission denied: {path}")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def list_directory(path: str, pattern: str = "*", recursive: bool = False) -> ToolResult:
    try:
        dp = _resolve(path)
        if not dp.exists():
            return ToolResult(False, error=f"Directory not found: {dp}")
        if not dp.is_dir():
            return ToolResult(False, error=f"Path is not a directory: {dp}")
        globber = dp.rglob if recursive else dp.glob
        entries = []
        for entry in sorted(globber(pattern)):
            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "path": str(entry),
                "type": "directory" if entry.is_dir() else "file",
                "size_bytes": stat.st_size if entry.is_file() else None,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return ToolResult(True, data={
            "directory": str(dp),
            "pattern": pattern,
            "recursive": recursive,
            "count": len(entries),
            "entries": entries,
        })
    except PermissionError:
        return ToolResult(False, error=f"Permission denied: {path}")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def delete_file(path: str) -> ToolResult:
    try:
        fp = _resolve(path)
        if not fp.exists():
            return ToolResult(False, error=f"File not found: {fp}")
        if not fp.is_file():
            return ToolResult(False, error="Path is not a file. Only individual files can be deleted with this tool.")
        size_bytes = fp.stat().st_size
        fp.unlink()
        return ToolResult(True, data={"deleted_path": str(fp), "freed_bytes": size_bytes})
    except PermissionError:
        return ToolResult(False, error=f"Permission denied: {path}")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def copy_file(source: str, destination: str, overwrite: bool = False) -> ToolResult:
    try:
        src = _resolve(source)
        dst = _resolve(destination)
        if not src.exists():
            return ToolResult(False, error=f"Source not found: {src}")
        if not src.is_file():
            return ToolResult(False, error=f"Source is not a file: {src}")
        if dst.exists() and not overwrite:
            return ToolResult(False, error=f"Destination already exists: {dst}. Set overwrite=true to replace it.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return ToolResult(True, data={
            "source": str(src),
            "destination": str(dst),
            "size_bytes": dst.stat().st_size,
        })
    except PermissionError:
        return ToolResult(False, error="Permission denied")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def move_file(source: str, destination: str, overwrite: bool = False) -> ToolResult:
    try:
        src = _resolve(source)
        dst = _resolve(destination)
        if not src.exists():
            return ToolResult(False, error=f"Source not found: {src}")
        if not src.is_file():
            return ToolResult(False, error=f"Source is not a file: {src}")
        if dst.exists() and not overwrite:
            return ToolResult(False, error=f"Destination already exists: {dst}. Set overwrite=true to replace it.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return ToolResult(True, data={"source": str(src), "destination": str(dst)})
    except PermissionError:
        return ToolResult(False, error="Permission denied")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def create_directory(path: str) -> ToolResult:
    try:
        dp = _resolve(path)
        if dp.exists():
            return ToolResult(False, error=f"Path already exists: {dp}")
        dp.mkdir(parents=True)
        return ToolResult(True, data={"created_path": str(dp)})
    except PermissionError:
        return ToolResult(False, error=f"Permission denied: {path}")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def get_file_info(path: str) -> ToolResult:
    try:
        fp = _resolve(path)
        if not fp.exists():
            return ToolResult(False, error=f"Path not found: {fp}")
        stat = fp.stat()
        size_mb = stat.st_size / (1024 * 1024)
        info: dict[str, Any] = {
            "path": str(fp),
            "name": fp.name,
            "extension": fp.suffix,
            "type": "directory" if fp.is_dir() else "file",
            "size_bytes": stat.st_size,
            "size_mb": round(size_mb, 4),
            "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "extension_allowed": fp.suffix.lower() in ALLOWED_EXTENSIONS,
            "within_size_limit": size_mb <= MAX_FILE_SIZE_MB,
        }
        return ToolResult(True, data=info)
    except PermissionError:
        return ToolResult(False, error=f"Permission denied: {path}")
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def search_in_files(
    directory: str,
    query: str,
    file_pattern: str = "*",
    case_sensitive: bool = False,
) -> ToolResult:
    try:
        dp = _resolve(directory)
        if not dp.exists():
            return ToolResult(False, error=f"Directory not found: {dp}")
        if not dp.is_dir():
            return ToolResult(False, error=f"Path is not a directory: {dp}")

        needle = query if case_sensitive else query.lower()
        matches = []

        for fp in sorted(dp.rglob(file_pattern)):
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            if fp.stat().st_size > MAX_FILE_SIZE_BYTES:
                continue
            try:
                content = fp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue

            haystack = content if case_sensitive else content.lower()
            if needle not in haystack:
                continue

            lines = []
            for i, line in enumerate(content.splitlines(), 1):
                check = line if case_sensitive else line.lower()
                if needle in check:
                    lines.append({"line_number": i, "line": line.rstrip()})
                    if len(lines) == 25:
                        break

            matches.append({
                "file": str(fp),
                "match_count": len(lines),
                "lines": lines,
            })

        return ToolResult(True, data={
            "query": query,
            "directory": str(dp),
            "case_sensitive": case_sensitive,
            "files_with_matches": len(matches),
            "matches": matches,
        })
    except Exception as exc:
        return ToolResult(False, error=str(exc))


def batch_read_files(paths: list[str]) -> ToolResult:
    files = []
    errors = []
    for p in paths:
        result = read_file(p)
        if result.success:
            files.append(result.data)
        else:
            errors.append({"path": p, "error": result.error})
    return ToolResult(True, data={
        "read_count": len(files),
        "error_count": len(errors),
        "files": files,
        "errors": errors,
    })


class _DDGParser(html.parser.HTMLParser):
    """Extracts <a class="result__a"> links from DuckDuckGo HTML results."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._capturing: bool = False
        self._href: str = ""
        self._title_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or len(self.results) >= 3:
            return
        attr_map = dict(attrs)
        classes = (attr_map.get("class") or "").split()
        if "result__a" in classes:
            self._capturing = True
            self._href = attr_map.get("href") or ""
            self._title_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capturing:
            self._capturing = False
            title = "".join(self._title_buf).strip()
            url = _ddg_real_url(self._href)
            if title and url:
                self.results.append({"title": title, "url": url})

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._title_buf.append(data)


def _ddg_real_url(href: str) -> str:
    """Unwrap DDG redirect URLs to get the actual destination URL."""
    parsed = urllib.parse.urlparse(href)
    qs = urllib.parse.parse_qs(parsed.query)
    uddg = qs.get("uddg", [])
    return urllib.parse.unquote(uddg[0]) if uddg else href


def search_web(query: str) -> ToolResult:
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 groq-file-agent/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except socket.timeout:
        return ToolResult(False, error="Search timed out after 10 s.")
    except urllib.error.HTTPError as exc:
        return ToolResult(False, error=f"Search API returned HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        return ToolResult(False, error=f"Network error: {exc.reason}")
    except Exception as exc:
        return ToolResult(False, error=f"Search failed: {exc}")

    parser = _DDGParser()
    parser.feed(body)

    return ToolResult(True, data={
        "query": query,
        "result_count": len(parser.results),
        "results": parser.results,
        "note": "No results found." if not parser.results else None,
    })


# ---------------------------------------------------------------------------
# Schema definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the full text content of a single file. "
                "Validates the file extension against the allowed list and checks that the file "
                "is within the size limit before reading. Returns content plus metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file. Absolute or relative to the working directory.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file. DESTRUCTIVE: replaces the entire file when overwrite is true. "
                "By default refuses to overwrite an existing file — the operation will fail and "
                "explain that overwrite=true is required. Parent directories are created automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Destination file path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Set true only when the user has explicitly confirmed they want to replace the existing file. Default false.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_file",
            "description": (
                "Append text to the end of an existing file without replacing its current content. "
                "The file must already exist. Use write_file to create a new file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the existing file."},
                    "content": {"type": "string", "description": "Text to append."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List the contents of a directory. Supports glob patterns and optional recursion. "
                "Returns each entry's name, path, type, size, and last-modified time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list."},
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter results, e.g. '*.py' or '*.md'. Defaults to '*' (all entries).",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, recurse into subdirectories. Default false.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": (
                "Permanently delete a single file. DESTRUCTIVE and irreversible. "
                "Only operates on individual files, not directories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to delete."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": (
                "Copy a file to a new location. By default refuses to overwrite the destination "
                "if it already exists. Parent directories at the destination are created automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Source file path."},
                    "destination": {"type": "string", "description": "Destination file path."},
                    "overwrite": {
                        "type": "boolean",
                        "description": "Set true only with explicit user confirmation to replace existing destination. Default false.",
                    },
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": (
                "Move or rename a file. DESTRUCTIVE: the original file is removed. "
                "By default refuses to overwrite the destination if it already exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Current file path."},
                    "destination": {"type": "string", "description": "Target file path."},
                    "overwrite": {
                        "type": "boolean",
                        "description": "Set true only with explicit user confirmation to replace existing destination. Default false.",
                    },
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a new directory, including any missing parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the directory to create."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": (
                "Retrieve metadata for a file or directory: size, extension, timestamps, "
                "and whether it can be read by this agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to inspect."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": (
                "Search for a text query across all matching files in a directory tree. "
                "Returns each matching file with the specific line numbers and content that contain the query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Root directory to search from."},
                    "query": {"type": "string", "description": "Text string to search for."},
                    "file_pattern": {
                        "type": "string",
                        "description": "Glob pattern to restrict which files are searched, e.g. '*.py'. Defaults to '*' (all allowed files).",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "If true, match is case-sensitive. Default false.",
                    },
                },
                "required": ["directory", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "batch_read_files",
            "description": (
                "Read multiple files in a single call. Returns an array of file contents plus "
                "a list of any files that could not be read with the reason for each failure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to read.",
                    }
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web using DuckDuckGo and return the top 3 results. "
                "Each result includes a title and a URL. "
                "Safe, read-only operation — does not modify any files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string.",
                    }
                },
                "required": ["query"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Registry and dispatch
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, Any] = {
    "read_file": read_file,
    "write_file": write_file,
    "append_to_file": append_to_file,
    "list_directory": list_directory,
    "delete_file": delete_file,
    "copy_file": copy_file,
    "move_file": move_file,
    "create_directory": create_directory,
    "get_file_info": get_file_info,
    "search_in_files": search_in_files,
    "batch_read_files": batch_read_files,
    "search_web": search_web,
}


def get_tool_schemas() -> list[dict]:
    return TOOL_SCHEMAS


def execute_tool(tool_name: str, args: dict) -> ToolResult:
    fn = _TOOL_REGISTRY.get(tool_name)
    if fn is None:
        return ToolResult(False, error=f"Unknown tool: '{tool_name}'")
    return fn(**args)
