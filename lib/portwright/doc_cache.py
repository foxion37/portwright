"""Fetch and cache explicitly configured public documentation evidence.

Each Procedure may name one canonical public HTTPS document URL. The user's
update invocation is the only refresh trigger; there is no scheduler. Fetched
text is stored as JSON evidence under ``services/_private/_evidence/`` so
preflight can compare it against the note's ``last_verified`` date.

The public-address DNS check is best effort, not a security sandbox: it runs
before the connection and cannot stop a hostname that is re-resolved
differently later. A successful download never marks a Procedure verified;
only a human review does.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from .contracts import SERVICE_ID_RE


_MAX_BYTES = 1024 * 1024
# JSON escaping inflates stored text; cap the raw file with headroom.
_MAX_FILE_BYTES = _MAX_BYTES * 8
_TIMEOUT = 20
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
# Only true HTML documents get text-extracted; markdown that quotes tags is kept.
_HTML_DOC_RE = re.compile(r"^\s*(?:<\?xml[^>]*>\s*)?(?:<!doctype\s+html|<html[\s>/])", re.IGNORECASE)
_HTML_SKIP_TAGS = frozenset({"script", "style"})
_HTML_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)


class _TextExtractor(HTMLParser):
    """Visible text of an HTML document, excluding script and style bodies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipped = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _HTML_SKIP_TAGS:
            self._skipped += 1
        elif tag in _HTML_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIP_TAGS:
            self._skipped = max(0, self._skipped - 1)
        elif tag in _HTML_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipped:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Stdlib-only visible text: script/style dropped, entities decoded."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed markup: keep whatever text was collected
        pass
    lines = [line.strip() for line in "".join(parser.parts).split("\n")]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


class _FetchFailed(Exception):
    """Network failure reduced to a fixed, safe category."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ProxyHandler({}) disables environment proxy variables: no env, no auth.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def _resolve_addresses(host: str) -> list:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise _FetchFailed("dns") from exc
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise _FetchFailed("dns")
    return addresses


def _validate_url(url: str) -> None:
    """Raise _FetchFailed unless the URL is explicit, canonical, and public."""
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port  # raises ValueError on a bad port
    except ValueError as exc:
        raise _FetchFailed("invalid-url") from exc
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise _FetchFailed("invalid-url")
    host = parsed.hostname
    if not host:
        raise _FetchFailed("invalid-url")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise _FetchFailed("url-not-public")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = _resolve_addresses(host)
    if not all(address.is_global for address in addresses):
        raise _FetchFailed("url-not-public")


def _fetch(url: str) -> str:
    """Validate, resolve, and GET bounded text. The only network seam."""
    _validate_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "portwright-doc-cache"})
    try:
        with _OPENER.open(request, timeout=_TIMEOUT) as response:
            payload = response.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise _FetchFailed("redirect" if 300 <= exc.code < 400 else "http") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise _FetchFailed("network") from exc
    if len(payload) > _MAX_BYTES:
        raise _FetchFailed("too-large")
    try:
        # Non-text bodies are refused, never replaced (§6.3 document_unavailable).
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _FetchFailed("not-utf8") from exc


def _idna(host: str) -> str | None:
    """Lowercase ASCII (IDNA) form of a hostname, or None when it cannot encode."""
    if not isinstance(host, str) or not host or "\x00" in host:
        return None
    try:
        return host.encode("idna").decode("ascii").lower()
    except (UnicodeError, UnicodeDecodeError):
        return None


def target_host(url: str) -> str | None:
    """IDNA-normalized hostname of ``url`` without resolving anything."""
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port  # raises ValueError on a bad port
    except ValueError:
        return None
    return _idna(parsed.hostname) if parsed.hostname else None


def safe_url_path(path: str) -> str | None:
    """Normalized URL path, or None for escapes, separators or traversal.

    Percent escapes, backslashes and NUL are refused outright, ``..`` segments are
    refused, and empty/``.`` segments are collapsed, so an encoded or traversal
    path can never be mistaken for a prefix outside the approved subtree.
    """
    if not path:
        path = "/"
    if not isinstance(path, str) or "%" in path or "\\" in path or "\x00" in path:
        return None
    segments: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            return None
        segments.append(segment)
    return "/" + "/".join(segments)


def host_allowed(url: str, allowed_hosts, path_prefixes=()) -> bool:
    """Exact IDNA host match plus optional segment-boundary path prefixes.

    No wildcards, no eTLD suffixes, no DNS: the official-doc allowlist decision
    happens before any network use, so an off-domain URL is never fetched. A
    malformed prefix entry, or a URL path carrying an escape or traversal, fails
    closed instead of being ignored.
    """
    host = target_host(url)
    if host is None:
        return False
    allowed = {normalized for normalized in (_idna(item) for item in (allowed_hosts or ())) if normalized}
    if host not in allowed:
        return False
    prefixes = list(path_prefixes or ())
    if not prefixes:
        return True
    path = safe_url_path(urllib.parse.urlsplit(url).path)
    if path is None:
        return False
    bases: list[str] = []
    for prefix in prefixes:
        # Every entry is validated first: a malformed prefix fails the whole check
        # instead of being silently dropped while a sibling entry matches.
        if not isinstance(prefix, str) or not prefix.startswith("/"):
            return False
        base = safe_url_path(prefix)
        if base is None or base == "/":
            return False
        bases.append(base)
    return any(path == base or path.startswith(f"{base}/") for base in bases)


def fetch_document(url: str, allowed_hosts, path_prefixes=()) -> str:
    """Fetch one official document from the exact allowlisted host.

    ``allowed_hosts`` is the policy host set (empty = nothing allowed); pass
    ``None`` only where no commons allowlist exists (the local cache refresh).
    Redirects, environment proxies and auth headers are refused, the body must be
    valid UTF-8 and is capped at 1MiB/20s, and HTML documents are reduced to
    visible text. The public-address DNS check is best effort and runs before the
    connection; §A-12 removed the validated-IP pinning, so the hostname is
    re-resolved by the stack.
    """
    if allowed_hosts is not None and not host_allowed(url, allowed_hosts, path_prefixes):
        raise _FetchFailed("off_domain")
    text = _fetch(url)
    if _HTML_DOC_RE.match(text[:512].lstrip("\ufeff")):
        return html_to_text(text)
    return text


def _evidence_path(root: Path, service_id: str) -> Path:
    return root / "services" / "_private" / "_evidence" / f"{service_id}.json"


def _path_issue(root: Path, path: Path) -> str | None:
    if path.is_symlink():
        return "symlink evidence files are not allowed"
    if not path.parent.resolve().is_relative_to(root):
        return "evidence path resolves outside the Portwright root"
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            return "symlink evidence directories are not allowed"
    return None


def _write_cache(root: Path, path: Path, record: dict) -> None:
    issue = _path_issue(root, path)
    if issue:
        raise RuntimeError(issue)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False))
        issue = _path_issue(root, path)
        if issue:
            raise RuntimeError(issue)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def refresh_document(root: Path, service_id: str, url: str) -> dict:
    """Fetch the configured URL and atomically persist JSON evidence.

    Returns {status, service_id, source_url, sha256?, fetched_at?, changed,
    error?}. ``changed`` compares against the prior cached digest for the same
    URL and is None on first retrieval or failure. Never returns cached text.
    """
    root = root.resolve()
    if not SERVICE_ID_RE.fullmatch(service_id):
        raise ValueError("service id must be lowercase kebab-case")
    path = _evidence_path(root, service_id)
    issue = _path_issue(root, path)
    if issue:
        raise RuntimeError(issue)

    result = {"status": "failed", "service_id": service_id, "source_url": url, "changed": None}
    try:
        # No commons policy in this local cache path: the note names one canonical URL.
        text = fetch_document(url, None)
    except _FetchFailed as exc:
        result["error"] = exc.category
        return result
    except Exception:
        result["error"] = "network"
        return result

    prior = load_document(root, service_id, url)
    record = {
        "service_id": service_id,
        "source_url": url,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "fetched_at": datetime.now(timezone.utc).date().isoformat(),
        "text": text,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_cache(root, path, record)
    except Exception:
        result["error"] = "write"
        return result

    result.update(
        status="fetched",
        sha256=record["sha256"],
        fetched_at=record["fetched_at"],
        changed=None if prior is None else prior["sha256"] != record["sha256"],
    )
    return result


def load_document(root: Path, service_id: str, url: str) -> dict | None:
    """Return valid cached {source_url, sha256, fetched_at, text} or None.

    No network. The cache must match the configured URL, stay within size
    limits, carry a real date, and re-hash to its stored digest.
    """
    root = root.resolve()
    if not SERVICE_ID_RE.fullmatch(service_id):
        return None
    path = _evidence_path(root, service_id)
    if _path_issue(root, path) or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_FILE_BYTES + 1)
        if len(raw) > _MAX_FILE_BYTES:
            return None
        record = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    source_url = record.get("source_url")
    digest = record.get("sha256")
    fetched_at = record.get("fetched_at")
    text = record.get("text")
    if source_url != url or not isinstance(text, str):
        return None
    if not isinstance(digest, str) or not _HEX64_RE.fullmatch(digest):
        return None
    if not isinstance(fetched_at, str):
        return None
    try:
        date.fromisoformat(fetched_at)
    except ValueError:
        return None
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        return None
    if hashlib.sha256(encoded).hexdigest() != digest:
        return None
    return {"source_url": source_url, "sha256": digest, "fetched_at": fetched_at, "text": text}
