"""Local skill folders for portwright-skills: scan, resolve, read, and guarded writes.

Local URIs are ``skill://portwright/local/<folder id>/<path>``. Reads follow symlinks
inside a folder; writes refuse any symlink below the folder root. Every write takes a
per-folder lock directory and replaces ``SKILL.md`` atomically.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

LOCAL_PREFIX = "skill://portwright/local/"
LOCK_NAME = ".portwright-write-lock"
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_NAME = 64
MAX_FILES = 512
MAX_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 3
_ENCODED_SEPARATOR = re.compile(r"%(2f|2e|5c|00)", re.IGNORECASE)
_BLOCK = {">", ">-", ">+", "|", "|-", "|+"}


@dataclass
class SkillsConfig:
    local: dict[str, Path] = field(default_factory=dict)
    remote_url: str | None = None
    token_file: Path | None = None
    work_repo: Path | None = None


@dataclass
class SkillEntry:
    uri: str
    name: str
    description: str
    source: str
    digest: str
    files: list[dict] = field(default_factory=list)
    root_id: str | None = None
    path: Path | None = None


def _home(value: str) -> Path:
    return Path(os.path.expanduser(value))


def load_config(root: Path) -> SkillsConfig:
    """Read ``<home>/_local/skills.json``; a missing file means nothing is configured."""
    path = Path(root) / "_local" / "skills.json"
    if not path.is_file():
        return SkillsConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    remote = data.get("remote") or {}
    return SkillsConfig(
        local={key: _home(value) for key, value in (data.get("local") or {}).items()},
        remote_url=remote.get("url"),
        token_file=_home(remote["token_file"]) if remote.get("token_file") else None,
        work_repo=_home(data["work_repo"]) if data.get("work_repo") else None,
    )


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def frontmatter(text: str) -> dict[str, str]:
    """Top-level ``key: value`` fields, quoted scalars and ``>``/``|`` block scalars. No YAML dependency."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}
    fields: dict[str, str] = {}
    body = lines[1:end]
    index = 0
    while index < len(body):
        match = re.match(r"([A-Za-z0-9_-]+):\s*(.*)$", body[index])
        index += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in _BLOCK:
            block = []
            while index < len(body) and (not body[index].strip() or body[index][:1] in (" ", "\t")):
                block.append(body[index].strip())
                index += 1
            folded = value.startswith(">")
            value = " ".join(part for part in block if part) if folded else "\n".join(block).strip("\n")
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        fields[key] = value
    return fields


def _valid_package(name: str, markdown: str) -> None:
    meta = frontmatter(markdown)
    if meta.get("name") != name or not meta.get("description", "").strip():
        raise ValueError("frontmatter_invalid")


class LocalSkills:
    def __init__(self, roots: dict[str, Path]):
        self.roots = {root_id: Path(path) for root_id, path in roots.items()}

    # -- reading -------------------------------------------------------------

    def entries(self) -> list[SkillEntry]:
        found: list[SkillEntry] = []
        for root_id, root in self.roots.items():
            if not root.is_dir():
                continue
            for directory in self._skill_dirs(root):
                entry = self._entry(root_id, root, directory)
                if entry is not None:
                    found.append(entry)
        return found

    def _skill_dirs(self, root: Path) -> list[Path]:
        result, seen = [], set()

        def walk(directory: Path, depth: int) -> None:
            real = directory.resolve()
            if real in seen or depth > MAX_DEPTH:
                return
            seen.add(real)
            if depth and (directory / "SKILL.md").is_file():
                result.append(directory)
            try:
                children = sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))
            except OSError:
                return
            for child in children:
                walk(child, depth + 1)

        walk(root, 0)
        return result

    def _entry(self, root_id: str, root: Path, directory: Path) -> SkillEntry | None:
        try:
            text = (directory / "SKILL.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        meta = frontmatter(text)
        if meta.get("name") != directory.name or not meta.get("description", "").strip():
            return None
        files = self._files(directory)
        if files is None:
            return None
        relative = directory.relative_to(root).as_posix()
        return SkillEntry(uri=self._uri(root_id, f"{relative}/SKILL.md"), name=meta["name"],
                          description=meta["description"], source=f"local:{root_id}",
                          digest=next(f["digest"] for f in files if f["path"] == "SKILL.md"),
                          files=files, root_id=root_id, path=directory)

    def _files(self, directory: Path) -> list[dict] | None:
        files, total = [], 0
        for current, dirs, names in os.walk(directory, followlinks=True):
            here = Path(current)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (here / d / "SKILL.md").is_file())
            for name in sorted(names):
                if name.startswith("."):
                    continue
                try:
                    data = (here / name).read_bytes()
                except OSError:
                    return None  # a dangling link or unreadable file hides this skill only
                total += len(data)
                files.append({"path": (here / name).relative_to(directory).as_posix(), "digest": digest(data), "size": len(data)})
                if len(files) > MAX_FILES or total > MAX_BYTES:
                    return None
        return files

    @staticmethod
    def _uri(root_id: str, relative: str) -> str:
        return LOCAL_PREFIX + "/".join(quote(part, safe="") for part in [root_id, *relative.split("/")])

    def resolve(self, uri: str) -> tuple[str, Path]:
        if not isinstance(uri, str) or not uri.startswith(LOCAL_PREFIX) or "\\" in uri or _ENCODED_SEPARATOR.search(uri):
            raise ValueError("uri_outside_root")
        parts = [unquote(part) for part in uri[len(LOCAL_PREFIX):].split("/")]
        if len(parts) < 2 or parts[0] not in self.roots or any(p in ("", ".", "..") or "/" in p for p in parts):
            raise ValueError("uri_outside_root")
        return parts[0], self.roots[parts[0]].joinpath(*parts[1:])

    def read(self, uri: str) -> bytes:
        _, path = self.resolve(uri)
        if not path.is_file():
            raise ValueError("not_found")
        return path.read_bytes()

    def entry_for(self, uri: str) -> SkillEntry:
        root_id, path = self.resolve(uri)
        if path.name != "SKILL.md":
            raise ValueError("not_a_skill")
        entry = self._entry(root_id, self.roots[root_id], path.parent)
        if entry is None:
            raise ValueError("not_found")
        return entry

    # -- writing -------------------------------------------------------------

    def create(self, name: str, markdown: str, source: str | None = None) -> SkillEntry:
        if not isinstance(name, str) or len(name) > MAX_NAME or not NAME_RE.fullmatch(name):
            raise ValueError("name_invalid")
        root_id = source or next(iter(self.roots), None)
        if root_id not in self.roots:
            raise ValueError("not_local")
        _valid_package(name, markdown)
        data = markdown.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ValueError("too_large")
        root = self.roots[root_id]
        with _Lock(root):
            target = root / name
            if os.path.lexists(target):
                raise ValueError("exists")
            target.mkdir()
            try:
                _atomic_write(target / "SKILL.md", data)
            except BaseException:
                target.rmdir()
                raise
        return self.entry_for(self._uri(root_id, f"{name}/SKILL.md"))

    def update(self, uri: str, markdown: str, expected_digest: str) -> SkillEntry:
        root_id, path = self.resolve(uri)
        if path.name != "SKILL.md":
            raise ValueError("not_a_skill")
        root = self.roots[root_id]
        with _Lock(root):
            current = root
            for part in path.relative_to(root).parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("symlink_refused")
            if not path.is_file():
                raise ValueError("not_found")
            old = path.read_bytes()
            if digest(old) != expected_digest:
                raise ValueError("digest_conflict")
            _valid_package(path.parent.name, markdown)
            data = markdown.encode("utf-8")
            others = sum(f["size"] for f in (self._files(path.parent) or []) if f["path"] != "SKILL.md")
            if others + len(data) > MAX_BYTES:
                raise ValueError("too_large")
            _atomic_write(path, data)
        return self.entry_for(uri)


class _Lock:
    """Per-folder write lock (a directory). A stale lock is reported, never broken automatically."""

    def __init__(self, root: Path):
        self.path = root / LOCK_NAME

    def __enter__(self):
        try:
            os.mkdir(self.path)
        except FileExistsError:
            raise ValueError("locked") from None
        return self

    def __exit__(self, *exc):
        os.rmdir(self.path)
        return False


def _atomic_write(path: Path, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(prefix=".portwright-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temp, path.stat().st_mode & 0o777)
        else:
            os.chmod(temp, 0o644)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
