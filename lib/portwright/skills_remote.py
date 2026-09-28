"""Remote personal Hub reads for portwright-skills: catalog, verified loads, supporting files.

The bearer token is read from an owner-only file for each request and only ever placed in
the Authorization header. Errors carry fixed codes; response bodies are never echoed.
"""
from __future__ import annotations

import base64
import json
import os
import re
import stat
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .doc_cache import NoRedirect
from .skills_local import SkillEntry, digest

PIN_KEY = "io.portwright/commit"
USER_AGENT = "portwright-skills/4"
COMMIT_RE = re.compile(r"[a-f0-9]{40}")
_open = urllib.request.build_opener(NoRedirect()).open


def _url_ok(url: str, allow_http: bool) -> bool:
    parts = urlsplit(url or "")
    if parts.username or parts.password or parts.query or parts.fragment or not parts.hostname:
        return False
    return parts.scheme == "https" or (allow_http and parts.scheme == "http" and parts.hostname == "127.0.0.1")


class RemoteSkills:
    def __init__(self, url: str, token_file: Path, *, allow_http: bool = False):
        if not _url_ok(url, allow_http):
            raise ValueError("url_invalid")
        self.url = url
        self.token_file = Path(token_file)
        self._catalog: tuple[list[SkillEntry], dict] | None = None
        self._loaded: dict[str, dict] = {}
        self._request_id = 0

    # -- transport -------------------------------------------------------------

    def _token(self) -> str:
        try:
            info = os.stat(self.token_file)
            token = self.token_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise ValueError("token_file_invalid") from None
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not token or re.search(r"\s", token):
            raise ValueError("token_file_invalid")
        return token

    def _rpc(self, method: str, params: dict) -> dict:
        token = self._token()
        self._request_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}).encode()
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with _open(request, timeout=30) as response:
                if response.status != 200:
                    raise ValueError("remote_unavailable")
                reply = json.loads(response.read(32 * 1024 * 1024))
        except (OSError, ValueError, UnicodeError):
            raise ValueError("remote_unavailable") from None
        if not isinstance(reply, dict) or "error" in reply or not isinstance(reply.get("result"), dict):
            code = str(((reply or {}).get("error") or {}).get("message", "")) if isinstance(reply, dict) else ""
            raise ValueError({"NOT_FOUND": "not_found", "REVISION_RECALLED": "recalled"}.get(code, "remote_unavailable"))
        return reply["result"]

    @staticmethod
    def _meta(commit: str | None) -> dict:
        if commit is None:
            return {}
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
            raise ValueError("commit_invalid")
        return {"_meta": {PIN_KEY: commit}}

    # -- catalog ---------------------------------------------------------------

    def _fetch(self, commit: str | None = None) -> tuple[list[SkillEntry], dict]:
        result = self._rpc("skills/list", self._meta(commit))
        entries = []
        for skill in result.get("skills") or []:
            try:
                resources = {r["uri"]: {"digest": r["digest"], "size": r["size"]} for r in skill["resources"]}
                front = skill["frontmatter"]
                root = skill["uri"][: -len("SKILL.md")]
                entries.append(SkillEntry(
                    uri=skill["uri"], name=front["name"], description=front.get("description", ""),
                    source="remote:personal", digest=resources[skill["uri"]]["digest"],
                    files=[{"path": uri[len(root):], "uri": uri, **meta} for uri, meta in sorted(resources.items())]))
            except (KeyError, TypeError):
                raise ValueError("remote_unavailable") from None
        return entries, result.get("_meta") or {}

    def entries(self) -> list[SkillEntry]:
        if self._catalog is None:
            self._catalog = self._fetch()
        return self._catalog[0]

    # -- verified reads --------------------------------------------------------

    def _read_verified(self, uri: str, expected: dict, commit: str | None) -> bytes:
        contents = self._rpc("resources/read", {"uri": uri, **self._meta(commit)}).get("contents") or []
        if len(contents) != 1 or contents[0].get("uri") != uri:
            raise ValueError("verification_failed")
        item = contents[0]
        data = item["text"].encode("utf-8") if isinstance(item.get("text"), str) else base64.b64decode(item.get("blob") or "")
        if len(data) != expected["size"] or digest(data) != expected["digest"]:
            self._catalog = None
            raise ValueError("verification_failed")
        return data

    def load(self, uri: str, commit: str | None = None) -> dict:
        self._meta(commit)
        entries, meta = self._fetch(commit)
        if commit is None:
            self._catalog = (entries, meta)
        entry = next((e for e in entries if e.uri == uri), None)
        if entry is None:
            raise ValueError("not_found")
        manifest = {f["uri"]: f for f in entry.files}
        markdown = self._read_verified(uri, manifest[uri], commit).decode("utf-8")
        self._loaded[uri] = {"manifest": manifest, "commit": commit}
        return {"uri": uri, "release": meta.get("release"), "commit": meta.get("commit"), "markdown": markdown,
                "digest": entry.digest, "files": sorted(f["path"] for f in entry.files)}

    def read_file(self, skill_uri: str, uri: str) -> str:
        loaded = self._loaded.get(skill_uri)
        if loaded is None:
            raise ValueError("not_loaded")
        expected = loaded["manifest"].get(uri)
        if expected is None:
            raise ValueError("not_in_skill")
        data = self._read_verified(uri, expected, loaded["commit"])
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("binary_refused") from None
