"""In-memory virtual filesystem and the demo agent's four tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Workspace:
    """A small virtual filesystem the demo agent's tools operate on."""

    files: dict[str, str] = field(default_factory=dict)

    def snapshot(self) -> dict[str, str]:
        """Return a plain-dict copy of current file contents, for final-state assertions."""
        return dict(self.files)


def tool_list_files(ws: Workspace, **_: Any) -> dict[str, Any]:
    """List all file paths currently in the workspace."""
    return {"files": sorted(ws.files.keys())}


def tool_read_file(ws: Workspace, path: str, **_: Any) -> dict[str, Any]:
    """Read a file's contents, or report that it does not exist."""
    if path not in ws.files:
        return {"error": f"not found: {path}"}
    return {"path": path, "content": ws.files[path]}


def tool_write_file(ws: Workspace, path: str, content: str, **_: Any) -> dict[str, Any]:
    """Write (overwrite) a file's contents."""
    ws.files[path] = content
    return {"path": path, "content": content, "bytes_written": len(content)}


def tool_search(ws: Workspace, query: str, **_: Any) -> dict[str, Any]:
    """Return the paths of files whose contents contain ``query``."""
    matches = [path for path, content in ws.files.items() if query in content]
    return {"query": query, "matches": sorted(matches)}


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "search": tool_search,
}


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": "List all file paths currently in the workspace.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read a file's contents by path.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Workspace-relative file path."}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Overwrite a file's full contents by path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "content": {"type": "string", "description": "The complete new contents of the file."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "search",
        "description": "Return the paths of files whose contents contain a query substring.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Substring to search for."}},
            "required": ["query"],
        },
    },
]
