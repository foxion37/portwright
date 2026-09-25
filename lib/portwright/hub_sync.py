"""Verified, atomic local snapshots of one selected shared Hub (stdlib only)."""
from __future__ import annotations

import errno
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import hub_contracts as hc
from .contracts import PACKAGE_ROOT
from .memory import SECRET_PATTERNS

HUBS = ("public", "company")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")
MAX_RESPONSE = 32 * 1024 * 1024 + 8 * 1024 * 1024
IDENTIFIER_PATTERNS = hc.identifier_patterns(json.loads(
    (PACKAGE_ROOT / "install/identifier-policy.json").read_text(encoding="utf-8"))["patterns"])

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def urlopen(request, *, timeout=20):
    return build_opener(_NoRedirect).open(request, timeout=timeout)



def _parts(relative: str) -> list[str]:
    if not relative or relative.startswith("/") or "\\" in relative or any(p in ("", ".", "..") for p in relative.split("/")):
        raise ValueError("unsafe Hub path")
    return relative.split("/")


@contextmanager
def _parent(root: Path, relative: str, *, create: bool = False):
    """Pin every ancestor; no pathname is followed after it has been checked."""
    parts = _parts(relative)
    opened = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append(fd)
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError("symlink or invalid Hub directory") from None
                raise
            opened.append(fd)
        yield fd, parts[-1]
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _mkdir(root: Path, relative: str) -> Path:
    with _parent(root, relative + "/child", create=True):
        pass
    return root / relative


def _read_bytes(root: Path, relative: str) -> bytes | None:
    try:
        with _parent(root, relative) as (parent, name):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise ValueError("symlink Hub file") from None
                raise
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("invalid Hub file")
                return stream.read()
    except FileNotFoundError:
        return None


def _write_new(root: Path, relative: str, data: bytes) -> None:
    _mkdir(root, str(Path(relative).parent))
    with _parent(root, relative) as (parent, name):
        with os.fdopen(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)


def _unlink(root: Path, relative: str) -> None:
    with _parent(root, relative) as (parent, name):
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)


def _replace(root: Path, source: str, destination: str) -> None:
    with _parent(root, source) as (from_fd, from_name):
        with _parent(root, destination) as (to_fd, to_name):
            os.replace(from_name, to_name, src_dir_fd=from_fd, dst_dir_fd=to_fd)
            os.fsync(to_fd)


def _read_json(root: Path, relative: str):
    raw = _read_bytes(root, relative)
    return json.loads(raw.decode("utf-8")) if raw is not None else None


def _rmdir(root: Path, relative: str) -> None:
    with _parent(root, relative) as (parent, name):
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)


def _cleanup(root: Path, hub: str, protected: set[str]) -> None:
    """Reclaim only UUID generations with our manifest or prewrite marker."""
    directory = f"_local/hub/{hub}"
    with _parent(root, directory + "/child") as (fd, _):
        names = set(os.listdir(fd))
    for name in sorted(names):
        generation = name.split(".", 1)[0]
        if not re.fullmatch(r"[a-f0-9]{32}", generation) or generation in protected:
            continue
        marker = f"{generation}.pending.json"
        manifest = f"{generation}.json"
        source = marker if marker in names else manifest
        if source not in names:
            continue
        try:
            notes = _read_json(root, f"{directory}/{source}")
            if not isinstance(notes, list) or len(notes) > 1000:
                continue
            paths = [_local_path(hub, generation, note) for note in notes]
            if len(set(paths)) != len(paths):
                continue
            for path in paths:
                try:
                    with _parent(root, path) as (parent, leaf):
                        mode = os.stat(leaf, dir_fd=parent, follow_symlinks=False).st_mode
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(mode):
                    raise ValueError("unsafe orphan")
            for path in paths:
                try:
                    _unlink(root, path)
                except FileNotFoundError:
                    pass
            for folder in ("services", "failures"):
                base = f"{folder}/_hub/{hub}/{generation}"
                for path in (base + "/trial", base):
                    try:
                        _rmdir(root, path)
                    except FileNotFoundError:
                        pass
            temporary = f"_local/hub/state.{generation}.json"
            if _read_bytes(root, temporary) is not None:
                _unlink(root, temporary)
            for file in (manifest, marker):
                if file in names:
                    _unlink(root, f"{directory}/{file}")
        except (OSError, ValueError, KeyError, TypeError):
            continue


def config(root: Path, hub: str | None = None) -> tuple[str | None, dict | None]:
    data = _read_json(root, "_local/hub/config.json")
    if data is None:
        if hub:
            raise ValueError("Hub config missing")
        return None, None
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("hubs"), dict):
        raise ValueError("invalid Hub config")
    if hub is None and "active" not in data:
        return None, None
    selected = hub if hub is not None else data["active"]
    if selected not in HUBS:
        raise ValueError("select public or company Hub")
    item = data["hubs"].get(selected)
    if not isinstance(item, dict) or type(item.get("include_trial", False)) is not bool:
        raise ValueError("invalid Hub config")
    url, name = item.get("url"), item.get("token_env")
    if not isinstance(url, str) or not isinstance(name, str) or not ENV_NAME.fullmatch(name):
        raise ValueError("invalid Hub config")
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ValueError("Hub URL must be a bare HTTPS origin") from None
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment or parsed.netloc != host + (f":{port}" if port else "")):
        raise ValueError("Hub URL must be a bare HTTPS origin")
    return selected, item


def _state(root: Path) -> dict:
    data = _read_json(root, "_local/hub/state.json")
    if data is None:
        return {"schema_version": 1, "active_hub": None, "snapshots": {}}
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("snapshots"), dict) or data.get("active_hub") not in (*HUBS, None):
        raise ValueError("invalid Hub state")
    return data


def _refs(projection: dict, hub: str) -> set[tuple[str, str]]:
    if (not isinstance(projection, dict) or projection.get("schema_version") != 1 or projection.get("audience") != hub
            or not isinstance(projection.get("sequence"), int) or isinstance(projection["sequence"], bool) or projection["sequence"] < 1
            or not isinstance(projection.get("commit"), str) or not COMMIT.fullmatch(projection["commit"])
            or not isinstance(projection.get("entries"), list)):
        raise ValueError("invalid recall projection")
    refs = set()
    for item in projection["entries"]:
        if not isinstance(item, dict) or set(item) != {"note_id", "revision"}:
            raise ValueError("invalid recall entry")
        hc.split_note_id(item["note_id"])
        if not isinstance(item["revision"], str) or not hc.REVISION_RE.fullmatch(item["revision"]):
            raise ValueError("invalid recall revision")
        ref = item["note_id"], item["revision"]
        if ref in refs:
            raise ValueError("duplicate recall entry")
        refs.add(ref)
    return refs

def _known_recalls(item: dict, hub: str) -> set[tuple[str, str]]:
    if (not isinstance(item, dict) or hc.canonical_digest(item.get("recalls")) != item.get("recalls_digest")
            or not isinstance(item.get("generation"), str) or not re.fullmatch(r"[a-f0-9]{32}", item["generation"])):
        raise ValueError("invalid cached Hub recall")
    return _refs(item["recalls"], hub)


def _monotone(previous: dict | None, current: dict, hub: str) -> set[tuple[str, str]]:
    refs = _refs(current, hub)
    if previous is not None:
        old = _refs(previous, hub)
        if not old <= refs or current["sequence"] < previous["sequence"] or (current["sequence"] == previous["sequence"] and current != previous):
            raise ValueError("recall projection decreased")
    return refs


def _local_path(hub: str, generation: str, note: dict) -> str:
    kind, slug = hc.split_note_id(note["note_id"])
    if note["kind"] != kind or not hc.SERVICE_ID_RE.fullmatch(note["service_id"]) or (kind == "procedure" and slug != note["service_id"]):
        raise ValueError("Hub note identity mismatch")
    stem = note["service_id"] if kind == "procedure" else slug
    folder = "services" if kind == "procedure" else "failures"
    grade = "trial/" if note["grade"] == "trial" else ""
    return f"{folder}/_hub/{hub}/{generation}/{grade}{stem}.md"


def _identity_ok(note: dict, metadata: dict) -> bool:
    kind, stem = hc.split_note_id(note["note_id"])
    if kind == "procedure":
        return metadata.get("id") == note["service_id"]
    date = metadata.get("date")
    return (metadata.get("service") == note["service_id"] and isinstance(date, str)
            and stem.startswith(f"{date}-{note['service_id']}-"))


def _verified_notes(payload: dict, hub: str, trial: bool) -> list[dict]:
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("audience") != hub
            or not isinstance(payload.get("release_identity"), dict) or not COMMIT.fullmatch(str(payload["release_identity"].get("commit", "")))):
        raise ValueError("invalid sync response")
    notes = payload.get("notes")
    if not isinstance(notes, list) or len(notes) > 1000 or hc.canonical_digest(notes) != payload.get("notes_digest"):
        raise ValueError("invalid notes digest")
    if hc.canonical_digest(payload.get("recalls")) != payload.get("recalls_digest"):
        raise ValueError("invalid recalls digest")
    meta = [{k: v for k, v in n.items() if k != "text"} for n in notes if isinstance(n, dict)]
    if len(hc.canonical_json(meta)) > 8 * 1024 * 1024:
        raise ValueError("Hub manifest too large")
    if len(meta) != len(notes) or not hc.validate_note_index({"schema_version": 1, "audience": hub, "commit": payload["release_identity"]["commit"], "notes": meta})["ok"]:
        raise ValueError("invalid note index")
    size = 0
    for note in notes:
        text = note.get("text")
        if not isinstance(text, str):
            raise ValueError("invalid note text")
        raw = text.encode("utf-8")
        size += len(raw)
        if len(raw) != note["size"] or len(raw) > 1024 * 1024 or size > 32 * 1024 * 1024 or hc.sha256_digest(raw) != note["file_digest"]:
            raise ValueError("invalid file digest")
        parsed = hc.parse_note(text)["metadata"]
        if (parsed.get("grade") != note["grade"] or parsed.get("revision") != note["revision"]
                or parsed.get("distributable") is not True or not _identity_ok(note, parsed)):
            raise ValueError("Hub grade or identity mismatch")
        if hc.note_revision(note["kind"], note["service_id"], text, parsed.get("doc_url")) != note["revision"]:
            raise ValueError("invalid note revision")
        if note["grade"] == "trial" and not trial:
            raise ValueError("unexpected trial note")
    return notes


def _get(url: str, token: str, trial: bool) -> dict:
    request = Request(url + "/sync?include_trial=" + str(trial).lower(), headers={"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "portwright-hub/2.2"})
    with urlopen(request, timeout=20) as stream:
        if stream.geturl() != request.full_url:
            raise ValueError("Hub redirect refused")
        raw = stream.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("Hub sync too large")
    return json.loads(raw.decode("utf-8"))


@contextmanager
def _lock(root: Path):
    _mkdir(root, "_local/hub")
    with _parent(root, "_local/hub/sync.lock") as (parent, name):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.fsync(fd)
            yield
        finally:
            os.close(fd)
            os.unlink(name, dir_fd=parent)


def snapshot(root: Path, hub: str | None = None, *, include_trial: bool = False) -> tuple[dict | None, list[tuple[dict, str]]]:
    """One verified active pointer; no other Hub or old generation is scanned."""
    root = Path(root).resolve()
    state = _state(root)
    selected = hub if hub is not None else config(root)[0]
    if selected is None:
        return None, []
    if selected not in HUBS:
        raise ValueError("invalid Hub")
    item = state["snapshots"].get(selected)
    if item is None:
        return None, []
    generation = item.get("generation")
    if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation):
        raise ValueError("invalid generation")
    manifest = _read_json(root, f"_local/hub/{selected}/{generation}.json")
    if not isinstance(manifest, list):
        raise ValueError("missing Hub manifest")
    recalled = _known_recalls(item, selected)
    notes = []
    for note in manifest:
        path = _local_path(selected, generation, note)
        raw = _read_bytes(root, path)
        if raw is None:
            raise ValueError("missing Hub note")
        text = raw.decode("utf-8")
        if hc.sha256_digest(text) != note["file_digest"] or len(text.encode()) != note["size"]:
            raise ValueError("Hub cache digest mismatch")
        if (note["note_id"], note["revision"]) in recalled:
            raise ValueError("recalled Hub note in active generation")
        parsed = hc.parse_note(text)["metadata"]
        if (parsed.get("revision") != note["revision"] or parsed.get("grade") != note["grade"]
                or not _identity_ok(note, parsed)
                or hc.note_revision(note["kind"], note["service_id"], text, parsed.get("doc_url")) != note["revision"]):
            raise ValueError("Hub cache revision mismatch")
        notes.append((dict(note, text=text), path))
    if hc.canonical_digest([n for n, _ in notes]) != item["notes_digest"]:
        raise ValueError("Hub cache manifest mismatch")
    if not include_trial:
        notes = [(n, p) for n, p in notes if n["grade"] == "stable"]
    return item, notes


def sync(root: Path, hub: str | None = None, *, include_trial: bool = False, offline: bool = False) -> dict:
    root = Path(root).resolve()
    selected, settings = config(root, hub)
    if selected is None:
        raise ValueError("Hub not configured")
    if offline:
        item, _ = snapshot(root, selected, include_trial=True)
        if item is None:
            raise ValueError("no offline Hub snapshot")
        return {"status": "offline", "hub": selected, "commit": item["commit"], "synced_at": item["synced_at"], "recall_freshness": "last-sync"}
    trial = include_trial or settings.get("include_trial", False)
    token = os.environ.get(settings["token_env"])
    if not token:
        raise ValueError("Hub credential unavailable")
    with _lock(root):
        state = _state(root)
        previous = state["snapshots"].get(selected)
        if previous is not None:
            _known_recalls(previous, selected)
        try:
            _cleanup(root, selected, {previous["generation"]} if previous else set())
        except (OSError, ValueError):
            pass
        first = _get(settings["url"], token, trial)
        notes = _verified_notes(first, selected, trial)
        _monotone(previous["recalls"] if previous else None, first["recalls"], selected)
        generation = uuid.uuid4().hex
        staged = []
        _write_new(root, f"_local/hub/{selected}/{generation}.pending.json",
                   hc.canonical_json([{k: v for k, v in note.items() if k != "text"} for note in notes]))
        for note in notes:
            path = _local_path(selected, generation, note)
            _write_new(root, path, note["text"].encode("utf-8"))
            staged.append((note, path))
        latest = _get(settings["url"], token, trial)
        _verified_notes(latest, selected, trial)
        recalled = _monotone(first["recalls"], latest["recalls"], selected)
        kept = []
        for note, path in staged:
            if (note["note_id"], note["revision"]) in recalled:
                _unlink(root, path)
            else:
                kept.append((note, path))
        staged = kept
        manifest = [{k: v for k, v in note.items() if k != "text"} for note, _ in staged]
        _mkdir(root, f"_local/hub/{selected}")
        _write_new(root, f"_local/hub/{selected}/{generation}.json", hc.canonical_json(manifest))
        item = {"generation": generation, "commit": first["release_identity"]["commit"],
                "notes_digest": hc.canonical_digest([note for note, _ in staged]), "recalls": latest["recalls"],
                "recalls_digest": latest["recalls_digest"], "synced_at": datetime.now(timezone.utc).isoformat(), "include_trial": trial}
        state["snapshots"][selected] = item
        temp = f"_local/hub/state.{generation}.json"
        _write_new(root, temp, hc.canonical_json(state))
        _replace(root, temp, "_local/hub/state.json")
        return {"status": "synced", "hub": selected, "commit": item["commit"], "synced_at": item["synced_at"], "notes": len(staged)}


def screen_submission(name: str, payload: dict) -> dict:
    """Pure shape and shared free-text policy; returns codes, never offending text."""
    if name not in ("submit_lesson", "confirm_lesson", "report_failure") or not isinstance(payload, dict):
        return {"ok": False, "reason_code": "invalid_params"}
    if name == "submit_lesson" and not hc.validate_submission(payload)["ok"]:
        return {"ok": False, "reason_code": "invalid_params"}
    fields = {"confirm_lesson": {"note_id", "revision", "success_evidence"}, "report_failure": {"note_id", "revision", "reason"}}
    if name in fields:
        if set(payload) != fields[name]:
            return {"ok": False, "reason_code": "invalid_params"}
        try:
            hc.split_note_id(payload["note_id"])
        except (ValueError, TypeError):
            return {"ok": False, "reason_code": "invalid_params"}
        if not isinstance(payload["revision"], str) or not hc.REVISION_RE.fullmatch(payload["revision"]):
            return {"ok": False, "reason_code": "invalid_params"}
        text = payload.get("reason") if name == "report_failure" else payload["success_evidence"]
        if name == "report_failure" and (not isinstance(text, str) or not text.strip() or "\x00" in text or len(text.encode("utf-8")) > 2048):
            return {"ok": False, "reason_code": "invalid_params"}
        if name == "confirm_lesson" and (not isinstance(text, dict) or set(text) != {"action", "outcome"} or any(not isinstance(v, str) or not v.strip() or "\x00" in v or len(v.encode("utf-8")) > 2048 for v in text.values())):
            return {"ok": False, "reason_code": "invalid_params"}
    identifiers = IDENTIFIER_PATTERNS
    def leaves(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [part for k, v in value.items() for part in (leaves(k) + leaves(v))]
        return []
    for part in leaves(payload):
        issues = hc.screening_issues(part, secrets=SECRET_PATTERNS, identifiers=identifiers)
        if issues:
            return {"ok": False, "reason_code": issues[0]}
    return {"ok": True, "reason_code": None}
