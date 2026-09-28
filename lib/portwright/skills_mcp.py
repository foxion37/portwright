"""portwright-skills: one stdio MCP server for local skill folders and the personal Hub.

Five tools. URIs under ``skill://portwright/local/`` go to local folders (read and write);
every other ``skill://portwright/`` URI goes to the personal Hub (read only, verified).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from .mcp import InvalidParams, _version, run_stdio
from .skills_local import LOCAL_PREFIX, LocalSkills, SkillEntry, SkillsConfig, digest, load_config
from .skills_remote import RemoteSkills

LOCAL_TTL = 30.0
MAX_LIMIT = 50

TOOLS = [
    {"name": "search_skills",
     "description": "Search local skill folders and the personal Hub. Returns uri, name, description, source and digest.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                                                      "offset": {"type": "integer", "minimum": 0}}}},
    {"name": "load_skill",
     "description": "Load one SKILL.md by exact URI. Remote skills are digest-verified; commit pins a remote release. Guidance only, no execution rights.",
     "inputSchema": {"type": "object", "properties": {"uri": {"type": "string"}, "commit": {"type": "string"}}, "required": ["uri"]}},
    {"name": "read_skill_file",
     "description": "Read a text supporting file of a loaded skill.",
     "inputSchema": {"type": "object", "properties": {"skill_uri": {"type": "string"}, "uri": {"type": "string"}}, "required": ["skill_uri", "uri"]}},
    {"name": "create_skill",
     "description": "Create a new local skill folder with a complete SKILL.md (frontmatter name must equal the folder name). Never overwrites.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "markdown": {"type": "string"}, "source": {"type": "string"}},
                     "required": ["name", "markdown"]}},
    {"name": "update_skill",
     "description": "Replace a local SKILL.md. expected_digest is the digest from load_skill; a changed file is refused. Supporting files are kept.",
     "inputSchema": {"type": "object", "properties": {"uri": {"type": "string"}, "markdown": {"type": "string"}, "expected_digest": {"type": "string"}},
                     "required": ["uri", "markdown", "expected_digest"]}},
]


def _text(args: dict, key: str, *, required: bool = True) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value):
        raise InvalidParams(f"{key} must be a non-empty string")
    return value


def _int(args: dict, key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise InvalidParams(f"{key} must be an integer between {low} and {high}")
    return value


def _summary(entry: SkillEntry) -> dict:
    return {"uri": entry.uri, "name": entry.name, "description": entry.description, "source": entry.source, "digest": entry.digest}


class SkillsServer:
    def __init__(self, config: SkillsConfig, remote: RemoteSkills | None, remote_error: str | None = None):
        self.local = LocalSkills(config.local)
        self.remote = remote
        self.remote_error = None if remote is not None else (remote_error or "not_configured")
        self._local_cache: tuple[float, list[SkillEntry]] | None = None

    def _local_entries(self) -> list[SkillEntry]:
        # ponytail: 30 s catalog cache; outside editors show up late, writes here refresh it.
        now = time.monotonic()
        if self._local_cache is None or now - self._local_cache[0] > LOCAL_TTL:
            self._local_cache = (now, self.local.entries())
        return self._local_cache[1]

    def search(self, query: str, limit: int, offset: int) -> dict:
        entries = list(self._local_entries())
        remote_error = self.remote_error
        if self.remote is not None:
            try:
                entries += self.remote.entries()
            except ValueError as error:
                remote_error = str(error)
        words = query.lower().split()

        def score(entry: SkillEntry) -> int:
            name, description = entry.name.lower(), entry.description.lower()
            return sum(3 * (word in name) + (word in description) for word in words)

        ranked = sorted(((score(e), e) for e in entries), key=lambda pair: (-pair[0], pair[1].name, pair[1].uri))
        matches = [e for s, e in ranked if s > 0 or not words]
        page = matches[offset:offset + limit]
        return {"skills": [_summary(e) for e in page], "total": len(matches), "offset": offset,
                "next_offset": offset + len(page) if offset + len(page) < len(matches) else None,
                "remote_unavailable": remote_error is not None, "remote_error": remote_error}

    def load(self, uri: str, commit: str | None) -> dict:
        if uri.startswith(LOCAL_PREFIX):
            if commit is not None:
                raise ValueError("commit_not_local")
            entry = self.local.entry_for(uri)
            data = self.local.read(uri)
            return {"uri": uri, "source": entry.source, "markdown": data.decode("utf-8"),
                    "digest": digest(data), "files": sorted(f["path"] for f in entry.files)}
        if self.remote is None:
            raise ValueError(self.remote_error)
        return {**self.remote.load(uri, commit), "source": "remote:personal"}

    def read_file(self, skill_uri: str, uri: str) -> dict:
        if skill_uri.startswith(LOCAL_PREFIX):
            if not skill_uri.endswith("/SKILL.md") or not uri.startswith(skill_uri[: -len("SKILL.md")]):
                raise ValueError("not_in_skill")
            try:
                text = self.local.read(uri).decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("binary_refused") from None
        elif self.remote is None:
            raise ValueError(self.remote_error)
        else:
            text = self.remote.read_file(skill_uri, uri)
        return {"uri": uri, "text": text}

    def write(self, name: str, args: dict) -> dict:
        if name == "create_skill":
            entry = self.local.create(_text(args, "name"), _text(args, "markdown"), _text(args, "source", required=False))
        else:
            uri = _text(args, "uri")
            if not uri.startswith(LOCAL_PREFIX):
                raise ValueError("not_local")
            entry = self.local.update(uri, _text(args, "markdown"), _text(args, "expected_digest"))
        self._local_cache = None
        return _summary(entry)

    def call(self, name: str, args: dict[str, Any]) -> dict:
        if name == "search_skills":
            query = args.get("query", "")
            if not isinstance(query, str):
                raise InvalidParams("query must be a string")
            call = lambda: self.search(query, _int(args, "limit", 20, 1, MAX_LIMIT), _int(args, "offset", 0, 0, 10**6))
        elif name == "load_skill":
            uri, commit = _text(args, "uri"), _text(args, "commit", required=False)
            call = lambda: self.load(uri, commit)
        elif name == "read_skill_file":
            skill_uri, uri = _text(args, "skill_uri"), _text(args, "uri")
            call = lambda: self.read_file(skill_uri, uri)
        elif name in ("create_skill", "update_skill"):
            call = lambda: self.write(name, args)
        else:
            raise InvalidParams(f"unknown tool: {name}")
        try:
            payload, is_error = call(), False
        except ValueError as error:
            code = str(error)
            payload, is_error = {"error": code if code.replace("_", "").isalpha() else "invalid"}, True
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "isError": is_error}


def serve_config(config: SkillsConfig, *, remote: RemoteSkills | None | bool = None, version: str = "unknown",
                 stdin=None, stdout=None) -> None:
    """``remote=None`` builds the client from config, ``False`` disables it, an instance is used as is."""
    remote_error = None
    if remote is None:
        remote = False
        if config.remote_url and config.token_file:
            try:
                remote = RemoteSkills(config.remote_url, config.token_file)
            except ValueError as error:
                remote_error = str(error)
    server = SkillsServer(config, remote or None, remote_error)
    run_stdio(stdin or sys.stdin, stdout or sys.stdout, server_name="portwright-skills", version=version,
              list_tools=lambda: TOOLS, call_tool=server.call)


def serve(root: Path, *, stdin=None, stdout=None) -> None:
    root = Path(root).resolve()
    serve_config(load_config(root), version=_version(root), stdin=stdin, stdout=stdout)
