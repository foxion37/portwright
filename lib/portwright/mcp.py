"""Stdlib-only local MCP server: cache reads and screened Hub intake relay."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request

from .contracts import ContractCatalog, note_location, resource_path
from .jev import JevClient
from .preflight import Preflight, run_preflight
from .hub_sync import config, screen_submission, snapshot, urlopen
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
                "hub": {"type": "string", "enum": ["public", "company"]},
                "include_trial": {"type": "boolean"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_note",
        "description": "Read one .md note under services/, failures/, or profiles/ (including _private and _hub; _drafts are check-only).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "hub": {"type": "string", "enum": ["public", "company"]},
                           "include_trial": {"type": "boolean"}},
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


def _catalog(root: Path, args: dict[str, Any]) -> ContractCatalog:
    hub = args.get("hub")
    trial = args.get("include_trial", False)
    if hub is not None and hub not in ("public", "company"):
        raise _InvalidParams("hub must be public or company")
    if type(trial) is not bool:
        raise _InvalidParams("include_trial must be boolean")
    return ContractCatalog(root, hub=hub, include_trial=True if trial else None)


def _note_path(root: Path, raw: Any, args: dict[str, Any]) -> Path:
    if not isinstance(raw, str) or not raw:
        raise _InvalidParams("path must be a non-empty string")
    return _catalog(root, args).note_path(raw)


def _tool_preflight(root: Path, engine: Preflight, args: dict[str, Any]) -> dict[str, Any]:
    service = args.get("service")
    if not isinstance(service, str) or not service:
        raise _InvalidParams("service is required")
    intent = args.get("intent", "call")
    if intent not in ("call", "instruct", "recover"):
        raise _InvalidParams("intent must be call, instruct, or recover")
    cwd = _opt_str(args, "cwd")
    _catalog(root, args)
    return run_preflight(
        root,
        service,
        intent,
        cwd=Path(cwd) if cwd else None,
        profile_id=_opt_str(args, "profile"),
        evidence_version=_opt_str(args, "evidence_version"),
        evidence_fetched_at=_opt_str(args, "evidence_fetched_at"),
        engine=engine,
        hub=args.get("hub"),
        include_trial=args.get("include_trial", False),
    ).to_dict()


def _relay_tools(root: Path) -> list[dict[str, Any]]:
    selected, settings = config(root)
    if selected is None or not os.environ.get(settings["token_env"]):
        return []
    definitions = json.loads(resource_path(root, "hub/contracts/intake.schema.json").read_text(encoding="utf-8"))["$defs"]
    result = []
    for name in ("submit_lesson", "confirm_lesson", "report_failure"):
        schema = definitions[name]
        schema = dict(schema, **{"$defs": definitions})
        result.append({"name": name, "description": f"Screen locally, then submit to the selected {selected} Hub.", "inputSchema": schema})
    return result


def _relay(root: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    selected, settings = config(root)
    if selected is None or not os.environ.get(settings["token_env"]):
        raise _InvalidParams("Hub submission unavailable")
    decision = screen_submission(name, args)
    if not decision["ok"]:
        return {"error": decision["reason_code"]}
    if name in ("confirm_lesson", "report_failure"):
        _, notes = snapshot(root, selected, include_trial=True)
        if not any(note["note_id"] == args["note_id"] and note["revision"] == args["revision"] for note, _ in notes):
            return {"error": "note_not_in_selected_hub"}
    request = Request(settings["url"] + "/mcp", data=json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": args}}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ[settings["token_env"]], "User-Agent": "portwright-hub/2.2"},
        method="POST")
    try:
        with urlopen(request, timeout=20) as stream:
            if stream.geturl() != request.full_url:
                raise ValueError("Hub redirect refused")
            data = json.loads(stream.read(65537).decode("utf-8"))
        if "error" in data or not isinstance(data.get("result"), dict) or data["result"].get("isError"):
            return {"error": "Hub rejected submission"}
        return data["result"]
    except (OSError, ValueError, KeyError, UnicodeError):
        return {"error": "Hub submission unavailable"}


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


def _safe_error(error: Exception) -> str:
    if isinstance(error, ValueError) and str(error) in {
        "Hub URL must be a bare HTTPS origin",
        "invalid Hub config",
        "select public or company Hub",
        "Hub note is not in the active generation",
        "service id must be lowercase kebab-case",
    }:
        return f"ValueError: {error}"
    return "internal error"


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
                result = {"tools": TOOLS + _relay_tools(root)}
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                if not isinstance(name, str) or not isinstance(args, dict):
                    raise _InvalidParams("name and arguments are required")
                try:
                    if name == "preflight":
                        payload = _tool_preflight(root, engine, args)
                    elif name == "get_note":
                        path = _note_path(root, args.get("path"), args)
                        relative = path.relative_to(root).as_posix()
                        location = note_location(relative)
                        catalog = _catalog(root, args)
                        if location and location[1] == "hub" and not catalog._legacy_hub():
                            _, notes = snapshot(root, args.get("hub"), include_trial=catalog._trial())
                            text = next((note["text"] for note, candidate in notes if candidate == relative), None)
                            if text is None:
                                raise ValueError("Hub note is not in the active generation")
                        else:
                            text = path.read_text(encoding="utf-8")
                        payload = {"path": relative, "text": text}
                    elif name == "status":
                        payload = _tool_status(root)
                    elif name in ("submit_lesson", "confirm_lesson", "report_failure"):
                        payload = _relay(root, name, args)
                        result = _result(payload, is_error=True) if "error" in payload else payload
                        return {"jsonrpc": "2.0", "id": request_id, "result": result}
                    else:
                        raise _InvalidParams(f"unknown tool: {name}")
                    result = _result(payload)
                except _InvalidParams:
                    raise
                except Exception as error:
                    result = _result(_safe_error(error), is_error=True)
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}
        except _InvalidParams as error:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(error)}}
        except Exception:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "internal error"}}
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
