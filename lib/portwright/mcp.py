"""Stdlib-only MCP server: newline-delimited JSON-RPC 2.0 over stdio.

Exposes three read-only tools (preflight, get_note, status) so any MCP
client can run preflight without a managed block. No MCP SDK, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .contracts import ContractCatalog, note_location, resource_path
from .jev import JevClient
from .preflight import Preflight, run_preflight
from .profiles import load_tier_rules

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "preflight",
        "description": "Resolve a service call against the Portwright cache: procedure, lessons, freshness, tier.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "intent": {"type": "string", "enum": ["call", "instruct", "recover"], "default": "call"},
                "cwd": {"type": "string"},
                "profile": {"type": "string"},
                "evidence_version": {"type": "string"},
                "evidence_fetched_at": {"type": "string"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_note",
        "description": "Read one .md note under services/, failures/, or profiles/ (including _private and _hub; _drafts are check-only).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "status",
        "description": "Portwright home summary: version, note counts, Jev mode, tier rule count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class _InvalidParams(Exception):
    pass


def _version(root: Path) -> str:
    path = resource_path(root, "VERSION")
    return path.read_text(encoding="utf-8").strip() if path.is_file() else "unknown"


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _InvalidParams(f"{key} must be a string")
    return value


def _note_path(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw:
        raise _InvalidParams("path must be a non-empty string")
    return ContractCatalog(root).note_path(raw)


def _tool_preflight(root: Path, engine: Preflight, args: dict[str, Any]) -> dict[str, Any]:
    service = args.get("service")
    if not isinstance(service, str) or not service:
        raise _InvalidParams("service is required")
    intent = args.get("intent", "call")
    if intent not in ("call", "instruct", "recover"):
        raise _InvalidParams("intent must be call, instruct, or recover")
    cwd = _opt_str(args, "cwd")
    return run_preflight(
        root,
        service,
        intent,
        cwd=Path(cwd) if cwd else None,
        profile_id=_opt_str(args, "profile"),
        evidence_version=_opt_str(args, "evidence_version"),
        evidence_fetched_at=_opt_str(args, "evidence_fetched_at"),
        engine=engine,
    ).to_dict()


def _tool_status(root: Path) -> dict[str, Any]:
    notes = {"services": 0, "failures": 0, "profiles": 0, "private": 0, "hub": 0, "drafts": 0}
    for path in ContractCatalog(root).iter_notes():
        location = note_location(path.relative_to(root))
        if location is None:
            continue
        kind, source = location
        if source == "tracked":
            notes[f"{kind}s"] += 1
        elif source == "profile":
            notes["profiles"] += 1
        elif source == "draft":
            notes["drafts"] += 1
        else:
            notes[source] += 1
    return {
        "version": _version(root),
        "root_name": root.name,
        "notes": notes,
        "jev_mode": JevClient.from_env().mode,
        "tier_rules": len(load_tier_rules(root)),
    }


def _safe_error(root: Path, error: Exception) -> str:
    text = str(error)
    for token in text.split():
        if token.startswith("/"):
            try:
                if not Path(token).resolve().is_relative_to(root):
                    return "internal error"
            except (OSError, ValueError):
                return "internal error"
    return f"{type(error).__name__}: {text}"


def _result(payload: Any, is_error: bool = False) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def serve(root: Path, *, stdin=None, stdout=None) -> None:
    """Serve newline-delimited JSON-RPC 2.0 until stdin EOF."""
    root = Path(root).resolve()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    engine = Preflight(root)

    def handle(request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        try:
            if method == "initialize":
                offered = params.get("protocolVersion")
                result = {
                    "protocolVersion": offered if offered in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "portwright", "version": _version(root)},
                }
            elif method == "notifications/initialized":
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                if not isinstance(name, str) or not isinstance(args, dict):
                    raise _InvalidParams("name and arguments are required")
                try:
                    if name == "preflight":
                        payload = _tool_preflight(root, engine, args)
                    elif name == "get_note":
                        path = _note_path(root, args.get("path"))
                        payload = {"path": path.relative_to(root).as_posix(), "text": path.read_text(encoding="utf-8")}
                    elif name == "status":
                        payload = _tool_status(root)
                    else:
                        raise _InvalidParams(f"unknown tool: {name}")
                    result = _result(payload)
                except _InvalidParams:
                    raise
                except Exception as error:
                    result = _result(_safe_error(root, error), is_error=True)
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}
        except _InvalidParams as error:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(error)}}
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if not isinstance(request, dict):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
            else:
                reply = handle(request)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
