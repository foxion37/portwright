"""Pure wire contracts shared by the Hub writer, gate, builder and CLI.

Everything here is stdlib-only and side-effect free: no network, no credential
lookup, no git and no filesystem writes. The functions define the canonical
revision, the submission/schema validation and the safe generated paths of the
public/company commons (design §4.2, §6.1, §7.1).

``contained_path``/``unsafe_path_reason`` read the filesystem to reject symlinks,
escapes, submodules and executable bits, but never modify anything.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .contracts import DATE_FIELDS, SERVICE_ID_RE, parse_frontmatter


GATE_VERSION = "hub-gates-v1"

KINDS = ("procedure", "lesson")
GRADES = ("trial", "stable")
AUDIENCES = ("public", "company")

# git-visible reference layout (design §4.1, §A-1 sibling path)
TRIAL_DIR = "trial"
SERVICE_DIR = "services"
FAILURE_DIR = "failures"
COMMONS_SOURCE = "commons"
URI_PREFIX = "skill://gisul/"

# Server-owned scalars: rewritten by the server, never part of the revision.
SERVER_FIELDS = (
    "grade",
    "revision",
    "intake_id",
    "doc_digest",
    "gate_version",
    "gate_digest",
    "operator_review",
)
# Canonical fields excluded from the semantic metadata as well.
CANONICAL_FIELDS = ("doc_url", "distributable")
EXCLUDED_FIELDS = frozenset(SERVER_FIELDS + CANONICAL_FIELDS)

REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NOTE_ID_RE = re.compile(r"^(service|failure)/[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Generated path components: lexical allowlist, nothing dot-led, no spaces.
COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STEM_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

MAX_TEXT_BYTES = 2048
MAX_SERVICE_ID = 64
MAX_STEM = 120
MAX_NOTE_ID = 160
MAX_DOC_URL_BYTES = 2048

KIND_NOTE_PREFIX = {"procedure": "service", "lesson": "failure"}
NOTE_PREFIX_KIND = {value: key for key, value in KIND_NOTE_PREFIX.items()}

SUBMISSION_FIELDS = (
    "request_id",
    "service_id",
    "kind",
    "body",
    "doc_url",
    "success_evidence",
    "note_id",
    "expected_revision",
)
SUBMISSION_REQUIRED = ("request_id", "service_id", "kind", "body", "doc_url", "success_evidence")

NOTE_INDEX_FIELDS = ("note_id", "kind", "service_id", "revision", "grade", "path", "uri", "file_digest", "size")


# --------------------------------------------------------------------------- #
# canonical JSON and digests
# --------------------------------------------------------------------------- #
def canonical_json(value: Any) -> bytes:
    """Canonical JSON bytes: sorted keys, no ASCII escaping, no NaN/Infinity."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_digest(data: bytes | str) -> str:
    """``sha256:<64 lowercase hex>`` over raw bytes (or UTF-8 text)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_digest(value: Any) -> str:
    return sha256_digest(canonical_json(value))


def gate_digest(*, revision: str, doc_digest: str, answers: dict, policy: dict) -> str:
    """Bind one revision's judgment to the exact source and screening policies."""
    return canonical_digest({
        "revision": revision,
        "doc_digest": doc_digest,
        "answers": answers,
        **{key: policy[key] for key in (
            "gate_version", "pricing_version", "secret_policy_digest",
            "identifier_policy_digest", "official_domains_digest", "questions_digest",
        )},
    })


# --------------------------------------------------------------------------- #
# note text, frontmatter and the revision object
# --------------------------------------------------------------------------- #
def normalize_text(text: str) -> str:
    """Normalize CRLF/CR to LF and nothing else (markdown whitespace is content)."""
    if not isinstance(text, str):
        raise ValueError("text_not_string")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _terminal_lf(body: str) -> str:
    """Exactly one trailing LF, or '' for an empty body."""
    body = body.rstrip("\n")
    return body + "\n" if body else ""


def split_note(text: str) -> tuple[list[str] | None, list[str]]:
    """LF-only frontmatter split; mirrors contracts.split_frontmatter without
    letting str.splitlines() eat other Unicode line separators."""
    lines = normalize_text(text).split("\n")
    if not lines or lines[0].strip() != "---":
        return None, lines
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], lines[index + 1 :]
    return None, lines


def _check_duplicate_keys(meta_lines: list[str]) -> None:
    """A repeated top-level key would let parse_frontmatter's last value diverge
    from render_note's first-occurrence rewrite; refuse it outright (§4.2)."""
    keys: list[str] = []
    for raw in meta_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 and ":" in stripped:
            keys.append(stripped.partition(":")[0].strip())
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate_field")


def parse_note(text: str) -> dict[str, Any]:
    """``{"has_frontmatter", "metadata", "body"}``; body ends with one LF."""
    meta_lines, body_lines = split_note(text)
    if meta_lines is not None:
        _check_duplicate_keys(meta_lines)
    metadata = parse_frontmatter(meta_lines) if meta_lines is not None else {}
    return {
        "has_frontmatter": meta_lines is not None,
        "metadata": metadata,
        "body": _terminal_lf("\n".join(body_lines)),
    }


def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if not value or value != value.strip() or ":" in value or value[0] in "-?[]{}#&*!|>'\"%@`":
            return json.dumps(value, ensure_ascii=False)
        return value
    raise ValueError("frontmatter scalars must be strings, booleans or integers")


def render_note(text: str, updates: dict[str, Any] | None = None, drop: Iterable[str] = ()) -> str:
    """Rewrite frontmatter scalars, preserving every untouched line and the body.

    A grade-only rewrite must not disturb revision: ``note_revision`` excludes the
    server-owned fields, so callers may safely set ``grade``/``revision``/... here.
    """
    updates = {key: value for key, value in dict(updates or {}).items()}
    dropped = set(drop)
    meta_lines, body_lines = split_note(text)
    if meta_lines is None:
        raise ValueError("note has no frontmatter")
    _check_duplicate_keys(meta_lines)
    out: list[str] = []
    seen: set[str] = set()
    for raw in meta_lines:
        stripped = raw.strip()
        top_level = len(raw) - len(raw.lstrip(" ")) == 0
        if top_level and stripped and not stripped.startswith("#") and ":" in stripped:
            key = stripped.partition(":")[0].strip()
            if key in dropped:
                seen.add(key)
                continue
            if key in updates:
                out.append(f"{key}: {_render_scalar(updates.pop(key))}")
                seen.add(key)
                continue
        out.append(raw)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}: {_render_scalar(value)}")
    head = "---\n" + "\n".join(out) + "\n---\n"
    return head + _terminal_lf("\n".join(body_lines))


def _metadata_issues(metadata: Any, *, prefix: str = "") -> list[str]:
    # Semantic metadata is JSON strings, booleans, integers, arrays and objects (§4.2).
    if metadata is None or isinstance(metadata, float):
        return [f"{prefix}metadata_type_invalid"]
    if isinstance(metadata, (bool, int, str)):
        return []
    if isinstance(metadata, list):
        issues: list[str] = []
        for item in metadata:
            issues.extend(_metadata_issues(item, prefix=prefix))
        return issues
    if isinstance(metadata, dict):
        issues = []
        for key, value in metadata.items():
            if not isinstance(key, str):
                issues.append(f"{prefix}metadata_type_invalid")
                continue
            if isinstance(value, float) or value is None:
                issues.append(f"{prefix}metadata_type_invalid")
            else:
                issues.extend(_metadata_issues(value, prefix=prefix))
        return issues
    return [f"{prefix}metadata_type_invalid"]


def canonical_doc_url_issue(url: Any) -> str | None:
    """Fixed code for a non-canonical official-document URL, else None.

    HTTPS on the default port (explicit ``:443`` allowed) with no userinfo,
    query, fragment or percent escape anywhere, no IP literal, no internal or
    single-label host, and an already-canonical IDNA hostname.
    """
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return "doc_url_invalid"
    if len(url.encode("utf-8")) > MAX_DOC_URL_BYTES or not url.startswith("https://"):
        return "doc_url_invalid"
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return "doc_url_invalid"
    if parsed.scheme != "https" or parsed.query or parsed.fragment or port not in (None, 443):
        return "doc_url_invalid"
    if parsed.username or parsed.password or "%" in url:
        return "doc_url_invalid"
    host = parsed.hostname
    if not host or host.endswith("."):
        return "doc_url_invalid"
    if parsed.netloc != (host if port is None else f"{host}:{port}"):
        return "doc_url_invalid"
    if "." not in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return "doc_url_invalid"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return "doc_url_invalid"
    try:
        if host.encode("idna").decode("ascii") != host:
            return "doc_url_invalid"
    except (UnicodeError, UnicodeDecodeError):
        return "doc_url_invalid"
    return None


def identifier_patterns(entries: Iterable[Any]) -> tuple[re.Pattern[str], ...]:
    """Compile install/identifier-policy.json ``patterns`` for rejection use.

    Entries flagged ``scope: classification`` are guidance for classify/export,
    not rejection rules (same rule as scripts/build_skill_bundles.identifier_hits).
    A malformed entry or regex fails closed with ValueError("identifier_rule_invalid")
    rather than silently dropping an identifier guard.
    """
    compiled: list[re.Pattern[str]] = []
    for entry in entries or ():
        if isinstance(entry, re.Pattern):
            compiled.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError("identifier_rule_invalid")
        if entry.get("scope") == "classification":
            continue
        regex = entry.get("regex")
        if not isinstance(regex, str):
            raise ValueError("identifier_rule_invalid")
        flags = re.IGNORECASE if "i" in str(entry.get("flags", "")) else 0
        try:
            compiled.append(re.compile(regex, flags))
        except re.error as error:
            raise ValueError("identifier_rule_invalid") from error
    return tuple(compiled)


_SCREEN_NORMALIZATION = json.loads(
    (Path(__file__).resolve().parents[2] / "install" / "secret-patterns.json").read_text(encoding="utf-8")
)["normalization"]
if _SCREEN_NORMALIZATION != ["raw", "percent-decode-once"]:
    raise ValueError("screen_normalization_invalid")


def screening_issues(text: Any, *, secrets: Iterable[Any] = (), identifiers: Iterable[Any] = ()) -> list[str]:
    """Fixed codes ``secret``/``identifier`` for one free-text field.

    Callers pass memory.SECRET_PATTERNS and identifier_patterns(policy["patterns"]).
    The matched text is never returned, logged or echoed.
    """
    if not isinstance(text, str):
        return ["not_text"]
    forms = (text, urllib.parse.unquote(text)) if "%" in text else (text,)
    issues: list[str] = []
    if any(pattern.search(form) for pattern in secrets or () for form in forms):
        issues.append("secret")
    if any(pattern.search(form) for pattern in identifiers or () for form in forms):
        issues.append("identifier")
    return issues


def validate_note_metadata(metadata: dict[str, Any]) -> list[str]:
    """Fixed issue codes for server-owned scalars and semantic metadata."""
    if not isinstance(metadata, dict):
        return ["metadata_type_invalid"]
    issues: list[str] = []
    grade = metadata.get("grade")
    if grade is not None and grade not in GRADES:
        issues.append("grade_invalid")
    for field in ("revision", "doc_digest", "gate_digest"):
        value = metadata.get(field)
        if value is not None and not (isinstance(value, str) and REVISION_RE.fullmatch(value)):
            issues.append(f"{field}_invalid")
    intake_id = metadata.get("intake_id")
    if intake_id is not None and not (isinstance(intake_id, str) and UUID_RE.fullmatch(intake_id)):
        issues.append("intake_id_invalid")
    gate_version = metadata.get("gate_version")
    if gate_version is not None and gate_version != GATE_VERSION:
        issues.append("gate_version_invalid")
    if metadata.get("profile_id") is not None or metadata.get("profile_ids") is not None:
        issues.append("profile_forbidden")
    distributable = metadata.get("distributable")
    if distributable is not None and distributable is not True:
        issues.append("distributable_false")
    semantic = {key: value for key, value in metadata.items() if key not in EXCLUDED_FIELDS}
    issues.extend(_metadata_issues(semantic))
    for field in sorted(DATE_FIELDS & set(semantic)):
        value = semantic[field]
        if isinstance(value, str) and value and not DATE_RE.fullmatch(value):
            issues.append("date_invalid")
    return issues


def note_semantics(kind: str, service_id: str, text: str, doc_url: str) -> dict[str, Any]:
    """The exact ``{kind,service_id,metadata,body,doc_url}`` revision object.

    Raises ``ValueError`` with a fixed code when the note cannot be canonicalized.
    """
    if kind not in KINDS:
        raise ValueError("kind_invalid")
    if not isinstance(service_id, str) or not SERVICE_ID_RE.fullmatch(service_id) or len(service_id) > MAX_SERVICE_ID:
        raise ValueError("service_id_invalid")
    doc_issue = canonical_doc_url_issue(doc_url)
    if doc_issue:
        raise ValueError(doc_issue)
    parsed = parse_note(text)
    if not parsed["has_frontmatter"]:
        raise ValueError("frontmatter_required")
    metadata = parsed["metadata"]
    issues = validate_note_metadata(metadata)
    if issues:
        raise ValueError(issues[0])
    meta_doc_url = metadata.get("doc_url")
    if isinstance(meta_doc_url, str) and meta_doc_url != doc_url:
        raise ValueError("doc_url_mismatch")
    semantic = {key: value for key, value in metadata.items() if key not in EXCLUDED_FIELDS}
    return {
        "kind": kind,
        "service_id": service_id,
        "metadata": semantic,
        "body": parsed["body"],
        "doc_url": doc_url,
    }


def note_revision(kind: str, service_id: str, text: str, doc_url: str) -> str:
    """Content identity: ``sha256:<hex>`` of the canonical semantic object.

    Grade promotion, recall markers and review receipts keep the revision; any
    semantic change (body, commands, endpoints, versions, doc_url) makes a new one.
    """
    return canonical_digest(note_semantics(kind, service_id, text, doc_url))


# --------------------------------------------------------------------------- #
# note identity and safe generated paths
# --------------------------------------------------------------------------- #
def note_id_from_path(relative: str) -> str:
    """``services/<id>.md`` -> ``service/<id>``, ``failures/<stem>.md`` -> ``failure/<stem>``."""
    path = PurePosixPath(relative)
    parts = path.parts
    if len(parts) == 3 and parts[0] == TRIAL_DIR:
        parts = parts[1:]
    if len(parts) != 2 or parts[0] not in (SERVICE_DIR, FAILURE_DIR) or not parts[1].endswith(".md"):
        raise ValueError("path_invalid")
    slug = parts[1][: -len(".md")]
    if parts[0] == SERVICE_DIR:
        if not SERVICE_ID_RE.fullmatch(slug) or len(slug) > MAX_SERVICE_ID:
            raise ValueError("path_invalid")
        return f"service/{slug}"
    if not STEM_RE.fullmatch(slug) or len(slug) > MAX_STEM:
        raise ValueError("path_invalid")
    return f"failure/{slug}"


def note_id_for(kind: str, service_id: str, stem: str | None = None) -> str:
    if kind == "procedure":
        if not isinstance(service_id, str) or not SERVICE_ID_RE.fullmatch(service_id):
            raise ValueError("service_id_invalid")
        return f"service/{service_id}"
    if kind != "lesson":
        raise ValueError("kind_invalid")
    if stem is None:
        raise ValueError("stem_required")
    stem = _safe_stem(stem)
    return f"failure/{stem}"


def split_note_id(note_id: str) -> tuple[str, str]:
    """``note_id`` -> ``(kind, slug)``; raises ValueError for anything else."""
    if not isinstance(note_id, str) or len(note_id) > MAX_NOTE_ID or not NOTE_ID_RE.fullmatch(note_id):
        raise ValueError("note_id_invalid")
    prefix, _, slug = note_id.partition("/")
    return NOTE_PREFIX_KIND[prefix], slug


def _safe_stem(stem: str) -> str:
    if not isinstance(stem, str):
        raise ValueError("stem_invalid")
    stem = stem[: -len(".md")] if stem.endswith(".md") else stem
    if not STEM_RE.fullmatch(stem) or len(stem) > MAX_STEM:
        raise ValueError("stem_invalid")
    return stem


def safe_relative_path(relative: str) -> str:
    """Lexical allowlist for every generated commons path."""
    if not isinstance(relative, str) or not relative or "\x00" in relative or "\\" in relative:
        raise ValueError("path_invalid")
    if relative.startswith("/") or "%" in relative or str(PurePosixPath(relative)) != relative:
        raise ValueError("path_invalid")
    parts = PurePosixPath(relative).parts
    if not parts or any(part in (".", "..") or not COMPONENT_RE.fullmatch(part) for part in parts):
        raise ValueError("path_invalid")
    return relative


def note_relative_path(
    kind: str,
    service_id: str,
    intake_id: str,
    created: int,
    *,
    stem: str | None = None,
    stable_sibling: bool = False,
) -> str:
    """Server-generated commons path.

    Procedure: ``services/<service_id>.md``. Lesson:
    ``failures/<YYYY-MM-DD>-<service_id>-<intake_id>.md``. ``stem`` preserves an
    existing (migration) lesson filename; ``stable_sibling`` returns the §A-1
    ``trial/`` sibling used when a stable note's new revision arrives.
    """
    if kind not in KINDS:
        raise ValueError("kind_invalid")
    if not isinstance(service_id, str) or not SERVICE_ID_RE.fullmatch(service_id) or len(service_id) > MAX_SERVICE_ID:
        raise ValueError("service_id_invalid")
    if not isinstance(intake_id, str) or not UUID_RE.fullmatch(intake_id):
        raise ValueError("intake_id_invalid")
    if not isinstance(created, int) or isinstance(created, bool) or created <= 0:
        raise ValueError("created_invalid")
    if kind == "procedure":
        if stem is not None:
            raise ValueError("stem_not_allowed")
        relative = f"{SERVICE_DIR}/{service_id}.md"
    else:
        if stem is None:
            try:
                day = datetime.fromtimestamp(created, timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError) as error:
                raise ValueError("created_invalid") from error
            stem = f"{day}-{service_id}-{intake_id}"
        relative = f"{FAILURE_DIR}/{_safe_stem(stem)}.md"
    if stable_sibling:
        relative = f"{TRIAL_DIR}/{relative}"
    return safe_relative_path(relative)


def unsafe_path_reason(root: Path, relative: str) -> str | None:
    """None when the generated path is safe to write, else a fixed code."""
    try:
        safe_relative_path(relative)
    except ValueError:
        return "path-invalid"
    root = Path(root).resolve()
    target = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        target = target / part
        if target.is_symlink():
            return "path-symlink"
        if index < len(parts) - 1 and (target / ".git").exists():
            return "path-submodule"
    resolved = target.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        return "path-escape"
    if target.is_file() and (target.stat().st_mode & 0o111):
        return "path-executable"
    return None


def contained_path(root: Path, relative: str) -> Path:
    """Absolute target path inside ``root``, or ValueError with the fixed code."""
    reason = unsafe_path_reason(root, relative)
    if reason:
        raise ValueError(reason)
    return Path(root).resolve() / PurePosixPath(relative)


# --------------------------------------------------------------------------- #
# submission validation
# --------------------------------------------------------------------------- #
def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return MAX_TEXT_BYTES + 1


def _text_issue(value: Any) -> str | None:
    if not isinstance(value, str):
        return "not_string"
    if "\x00" in value:
        return "nul_byte"
    if not value.strip():
        return "empty"
    if _utf8_size(value) > MAX_TEXT_BYTES:
        return "too_large"
    return None


def validate_submission(payload: Any) -> dict[str, Any]:
    """Strict format validation of a ``submit_lesson`` payload.

    Returns ``{"ok", "reason_code", "errors", "fields"}``. Errors are fixed
    ``field:code`` tokens from a constant field allowlist; input text is never
    echoed. Secret/identifier matching and note-content agreement are the gate's
    job, not this function's.
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "reason_code": "invalid_params", "errors": ["payload:not_object"], "fields": {}}
    if any(not isinstance(key, str) or key not in SUBMISSION_FIELDS for key in payload):
        errors.append("unknown_field")
    for field in SUBMISSION_REQUIRED:
        if field not in payload:
            errors.append(f"{field}:missing")

    service_id = payload.get("service_id")
    if not isinstance(service_id, str) or not SERVICE_ID_RE.fullmatch(service_id) or len(service_id) > MAX_SERVICE_ID:
        errors.append("service_id:invalid")
        service_id = None
    kind = payload.get("kind")
    if kind not in KINDS:
        errors.append("kind:invalid")
        kind = None
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not UUID_RE.fullmatch(request_id):
        errors.append("request_id:invalid")

    body = payload.get("body")
    issue = _text_issue(body)
    if issue:
        errors.append(f"body:{issue}")

    doc_url = payload.get("doc_url")
    if canonical_doc_url_issue(doc_url) is not None:
        errors.append("doc_url:invalid")

    evidence = payload.get("success_evidence")
    if not isinstance(evidence, dict):
        errors.append("success_evidence:not_object")
    else:
        if set(evidence) != {"action", "outcome"}:
            errors.append("success_evidence:unknown_field")
        for field in ("action", "outcome"):
            if field not in evidence:
                errors.append(f"success_evidence.{field}:missing")
                continue
            issue = _text_issue(evidence[field])
            if issue:
                errors.append(f"success_evidence.{field}:{issue}")

    note_id = payload.get("note_id")
    expected_revision = payload.get("expected_revision")
    if note_id is None and expected_revision is not None:
        errors.append("note_id:missing")
    if expected_revision is None and note_id is not None:
        errors.append("expected_revision:missing")
    if note_id is not None:
        try:
            note_kind, slug = split_note_id(note_id)
        except ValueError:
            errors.append("note_id:invalid")
        else:
            if kind is not None and note_kind != kind:
                errors.append("note_id:invalid")
            elif kind == "procedure" and slug != service_id:
                errors.append("note_id:invalid")
    if expected_revision is not None and not (
        isinstance(expected_revision, str) and REVISION_RE.fullmatch(expected_revision)
    ):
        errors.append("expected_revision:invalid")

    if errors:
        return {"ok": False, "reason_code": "invalid_params", "errors": errors, "fields": {}}
    fields = {
        "request_id": request_id,
        "service_id": service_id,
        "kind": kind,
        "body": body,
        "doc_url": doc_url,
        "success_evidence": {
            "action": evidence["action"].strip(),
            "outcome": evidence["outcome"].strip(),
        },
    }
    if note_id is not None:
        fields["note_id"] = note_id
        fields["expected_revision"] = expected_revision
    return {"ok": True, "reason_code": None, "errors": [], "fields": fields}


# --------------------------------------------------------------------------- #
# note-index validation
# --------------------------------------------------------------------------- #
def _note_index_shape(notes: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(notes, list):
        return ["notes:missing"]
    for index, note in enumerate(notes):
        where = f"notes[{index}]"
        if not isinstance(note, dict):
            errors.append(f"{where}:not_object")
            continue
        if set(note) != set(NOTE_INDEX_FIELDS):
            errors.append(f"{where}:unknown_field")
            continue
        try:
            note_kind, slug = split_note_id(note["note_id"])
        except ValueError:
            errors.append(f"{where}:note_id_invalid")
            continue
        if note["kind"] != note_kind:
            errors.append(f"{where}:kind_mismatch")
        if note["service_id"] != slug and note_kind == "procedure":
            errors.append(f"{where}:service_id_mismatch")
        if not isinstance(note["service_id"], str) or not SERVICE_ID_RE.fullmatch(note["service_id"]):
            errors.append(f"{where}:service_id_invalid")
        if not isinstance(note["revision"], str) or not REVISION_RE.fullmatch(note["revision"]):
            errors.append(f"{where}:revision_invalid")
        if not isinstance(note["file_digest"], str) or not REVISION_RE.fullmatch(note["file_digest"]):
            errors.append(f"{where}:file_digest_invalid")
        if note["grade"] not in GRADES:
            errors.append(f"{where}:grade_invalid")
            continue
        if not isinstance(note["size"], int) or isinstance(note["size"], bool) or note["size"] <= 0:
            errors.append(f"{where}:size_invalid")
        stem = note["service_id"] if note_kind == "procedure" else slug
        expected_path = f"{note['grade']}/{note['service_id']}/notes/{stem}.md"
        if note["path"] != expected_path:
            errors.append(f"{where}:path_invalid")
        if note["uri"] != f"{URI_PREFIX}{COMMONS_SOURCE}/{expected_path}":
            errors.append(f"{where}:uri_invalid")
        else:
            try:
                safe_relative_path(expected_path)
            except ValueError:
                errors.append(f"{where}:path_invalid")
    return errors


def _note_index_duplicates(notes: list[Any]) -> list[str]:
    errors: list[str] = []
    seen_grade: set[tuple[Any, Any]] = set()
    seen_revision: set[tuple[Any, Any]] = set()
    seen_path: set[Any] = set()
    for index, note in enumerate(notes):
        if not isinstance(note, dict) or "note_id" not in note:
            continue
        key_grade = (note.get("note_id"), note.get("grade"))
        key_revision = (note.get("note_id"), note.get("revision"))
        path = note.get("path")
        if key_grade in seen_grade:
            errors.append(f"notes[{index}]:duplicate_note_grade")
        if key_revision in seen_revision:
            errors.append(f"notes[{index}]:duplicate_note_revision")
        if path in seen_path:
            errors.append(f"notes[{index}]:duplicate_path")
        seen_grade.add(key_grade)
        seen_revision.add(key_revision)
        seen_path.add(path)
    return errors


def _note_index_inventory(notes: list[dict[str, Any]], inventory: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(inventory, dict):
        return ["inventory:not_object"]
    files: dict[str, tuple[Any, Any]] = {}
    for entry in inventory.get("files") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            files[entry["path"]] = (entry.get("digest"), entry.get("size"))
    for index, note in enumerate(notes):
        found = files.get(note.get("path"))
        if found is None:
            errors.append("inventory:note_missing")
            continue
        if found[0] != note.get("file_digest"):
            errors.append(f"notes[{index}]:digest_mismatch")
        if found[1] != note.get("size"):
            errors.append(f"notes[{index}]:size_mismatch")
    index_paths = {note.get("path") for note in notes}
    file_notes = {path for path in files if "/notes/" in path and path.endswith(".md")}
    if file_notes != index_paths:
        errors.append("index:notes_incomplete")
    refs: dict[tuple[Any, Any], list[tuple[Any, Any]]] = {}
    for skill in inventory.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        uri = skill.get("uri")
        if not isinstance(uri, str) or not uri.startswith(URI_PREFIX) or not uri.endswith("/SKILL.md"):
            continue
        parts = PurePosixPath(uri[len(URI_PREFIX) :]).parts
        if len(parts) != 4 or parts[0] != COMMONS_SOURCE:
            continue
        grade, service_id = parts[1], parts[2]
        key = (grade, service_id)
        if skill.get("grade") != grade:
            errors.append("skill:grade_mismatch")
        note_refs = skill.get("note_refs")
        if not isinstance(note_refs, list):
            errors.append("skill:note_refs_missing")
            continue
        listed = [(ref.get("note_id"), ref.get("revision")) for ref in note_refs if isinstance(ref, dict)]
        if len(listed) != len(note_refs):
            errors.append("skill:note_refs_mismatch")
        if key in refs:
            errors.append("skill:duplicate")
        refs[key] = listed
    for index, note in enumerate(notes):
        key = (note.get("grade"), note.get("service_id"))
        if key not in refs:
            errors.append("skill:missing")
        elif (note.get("note_id"), note.get("revision")) not in refs[key]:
            errors.append(f"notes[{index}]:unreferenced")
    for key, listed in refs.items():
        expected = {
            (note.get("note_id"), note.get("revision"))
            for note in notes
            if (note.get("grade"), note.get("service_id")) == key
        }
        if set(listed) != expected or len(listed) != len(set(listed)):
            errors.append("skill:note_refs_mismatch")
    return errors


def validate_note_index(payload: Any, *, inventory: Any = None) -> dict[str, Any]:
    """Validate a release-v2 note-index (§7.1) and, optionally, its inventory.

    A note_id may appear once per grade: the §A-1 stable/trial siblings are the
    only allowed duplicate, and only with different revisions. Returns
    ``{"ok", "reason_code", "errors", "notes", "inventory_checked"}``.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "reason_code": "invalid_note_index", "errors": ["payload:not_object"], "notes": [], "inventory_checked": False}
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version:invalid")
    if payload.get("audience") not in AUDIENCES:
        errors.append("audience:invalid")
    commit = payload.get("commit")
    if not isinstance(commit, str) or not HEX40_RE.fullmatch(commit):
        errors.append("commit:invalid")
    notes = payload.get("notes")
    errors.extend(_note_index_shape(notes))
    if isinstance(notes, list):
        errors.extend(_note_index_duplicates(notes))
        if inventory is not None:
            errors.extend(_note_index_inventory([n for n in notes if isinstance(n, dict)], inventory))
            if isinstance(inventory, dict) and inventory.get("audience") != payload.get("audience"):
                errors.append("inventory:audience_mismatch")
    if errors:
        return {
            "ok": False,
            "reason_code": "invalid_note_index",
            "errors": errors,
            "notes": [],
            "inventory_checked": inventory is not None,
        }
    return {
        "ok": True,
        "reason_code": None,
        "errors": [],
        "notes": [dict(note) for note in notes],
        "inventory_checked": inventory is not None,
    }
