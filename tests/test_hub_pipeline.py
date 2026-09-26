"""H3 hub pipeline tests: gates, budget, git writer, run_pipeline, migration, review.

The Hub is a local SQLite-backed HTTP fixture on 127.0.0.1 (no external service),
git remotes are local bare repositories, and model judgments come from JevClient
fixture mode. No production failpoints take part.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import shutil as _shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from portwright import hub_pipeline as hp
from portwright.jev import Judgment


def run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="fixture",
        GIT_AUTHOR_EMAIL="fixture@example.com",
        GIT_COMMITTER_NAME="fixture",
        GIT_COMMITTER_EMAIL="fixture@example.com",
    )
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)


def make_commons(root: Path, *, domains: dict | None = None, notes: dict[str, str] | None = None) -> Path:
    """A clone of a fresh bare origin with a policy file and any existing notes."""
    origin = root / "origin.git"
    work = root / "commons"
    assert run_git("init", "--bare", "--initial-branch=main", "-q", str(origin)).returncode == 0
    assert run_git("clone", "-q", str(origin), str(work)).returncode == 0
    assert run_git("config", "user.name", "fixture", cwd=work).returncode == 0
    assert run_git("config", "user.email", "fixture@example.com", cwd=work).returncode == 0
    (work / "policy").mkdir(parents=True, exist_ok=True)
    (work / "policy" / "official-doc-domains.json").write_text(
        json.dumps(domains if domains is not None else {"schema_version": 1, "services": {}}), encoding="utf-8"
    )
    for relative, text in (notes or {}).items():
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    assert run_git("add", "-A", cwd=work).returncode == 0
    assert run_git("commit", "-q", "-m", "init", cwd=work).returncode == 0
    assert run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=work).returncode == 0
    return work


def procedure_note(service_id: str = "acme", *, distributable: bool = True, extra: str = "") -> str:
    lines = [
        "---",
        f"id: {service_id}",
        f"display_name: {service_id.title()}",
        'version_tag: "v1"',
        'last_verified: "2026-09-01"',
        "endpoint:",
        "  type: cli",
        f'  server: "{service_id}"',
        "human_steps:",
        "  - 없음",
        "agent_can:",
        "  - 직접 실행",
        "status: active",
    ]
    if distributable:
        lines.append("distributable: true")
    if extra:
        lines.append(extra)
    lines += ["---", "## 한 줄 요약", f"{service_id} 배포 절차", "", "## 정답 절차", f"1. `{service_id} deploy --prod` 를 실행한다.", ""]
    return "\n".join(lines)


def lesson_note(service_id: str = "acme", stem: str = "2026-09-01-acme-lesson", *, extra: str = "", date: str = "2026-09-01") -> str:
    lines = [
        "---",
        f'date: "{date}"',
        f"service: {service_id}",
        'service_version: "v1"',
        "status: active",
        "distributable: true",
    ]
    if extra:
        lines.append(extra)
    lines += ["---", "## 진짜 원인", "배포 중 캐시가 남아 있었다.", "", "## 해결", "캐시를 지우고 다시 배포한다.", ""]
    return "\n".join(lines)


def make_self_signed(directory: Path) -> tuple[Path, Path] | None:
    """A throwaway TLS cert for 127.0.0.1, so the real clients do real HTTPS."""
    if _shutil.which("openssl") is None:
        return None
    cert = directory / "fixture-cert.pem"
    key = directory / "fixture-key.pem"
    run = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "2",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0 or not cert.is_file():
        return None
    return cert, key


class FakeBudget:
    """Records reserve/settle/mark_uncertain order and args."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.deny = False
        self.settle_ok = True
        self.expired = False

    def reserve(self, **kwargs):
        self.calls.append(("reserve", kwargs))
        if self.deny:
            raise hp.BudgetExhausted("budget_exhausted")
        if self.expired:
            return {"reservation_id": "res-1", "month": "2026-09", "expires_at": 1, "max_micro_usd": kwargs["reserve_micro_usd"], "operation_id": kwargs["operation_id"]}
        return {"reservation_id": "res-1", "month": "2026-09", "expires_at": int(time.time()) + 3600, "max_micro_usd": kwargs["reserve_micro_usd"], "operation_id": kwargs["operation_id"]}

    def settle(self, reservation_id, actual_micro_usd, **kwargs):
        self.calls.append(("settle", reservation_id, actual_micro_usd, kwargs))
        return self.settle_ok

class FakeLiveTransport:
    """Minimal JevClient-shaped live transport that calls before_send with its bytes."""

    mode = "live"

    def __init__(self, answers: dict | None = None, status: str = "ok", usage: dict | None = None):
        self.before_send = None
        self.sent: list[bytes] = []
        self.answers = answers if answers is not None else passing_answers()
        self.status = status
        self.usage = usage if usage is not None else {"input_tokens": 2000, "output_tokens": 10}

    def ask(self, fixture_id, state, questions):
        body = json.dumps({"state": state, "model": "jev-latest", "questions": questions}).encode("utf-8")
        if self.before_send is not None:
            self.before_send(body)
        self.sent.append(body)
        return Judgment(status=self.status, source="live", answers=self.answers if self.status == "ok" else {}, usage=self.usage)


def passing_answers(**overrides) -> dict:
    answers = {
        "supported": {"type": "noul", "noul": 0.95},
        "evidence_consistent": {"type": "noul", "noul": 0.96},
        "malicious": {"type": "noul", "noul": 0.02},
        "personal": {"type": "noul", "noul": 0.01},
    }
    for key, value in overrides.items():
        answers[key] = value if isinstance(value, dict) else {"type": "noul", "noul": value}
    return answers


def write_jev_fixture(directory: Path, fixture_id: str, **overrides) -> Path:
    """A JevClient fixture-mode response file (never goes live)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{fixture_id}.json"
    path.write_text(
        json.dumps({"request": {"note": "fixture"}, "response": {"answers": passing_answers(**overrides), "usage": {}}}),
        encoding="utf-8",
    )
    return directory


def note_index_entry(entry: dict) -> dict:
    text = entry["text"]
    return {
        "note_id": entry["note_id"],
        "revision": entry["revision"],
        "grade": entry["grade"],
        "path": entry["path"],
        "file_digest": hp.hub_contracts.sha256_digest(text.encode("utf-8")),
        "size": len(text.encode("utf-8")),
        "text": text,
    }


def seed_note(
    commons: Path,
    *,
    relative: str,
    text: str,
    grade: str,
    doc_url: str,
    service_id: str,
    kind: str,
    domains: dict | None = None,
    answers: dict | None = None,
    intake_id: str | None = None,
    proof: bool = True,
) -> dict:
    """Commit a canon note, by default with a real, recomputable Gate-Proof trailer."""
    answers = answers if answers is not None else {"supported": 0.95, "evidence_consistent": 0.96, "malicious": 0.02, "personal": 0.01}
    intake_id = intake_id or str(uuid.uuid4())
    doc_digest = hp.hub_contracts.sha256_digest("official document body")
    revision = hp.hub_contracts.note_revision(kind, service_id, text, doc_url)
    note_id = hp.hub_contracts.note_id_from_path(relative)
    policy = hp.gate_policy(domains or {"schema_version": 1, "services": {}}, service_id)
    gate_proof = hp.gate_proof(note_id, revision, doc_digest, answers, policy)
    rendered = hp.hub_contracts.render_note(
        text,
        updates={
            "grade": grade,
            "revision": revision,
            "intake_id": intake_id,
            "doc_url": doc_url,
            "doc_digest": doc_digest,
            "gate_version": policy["gate_version"],
            "gate_digest": gate_proof["gate_digest"],
            "distributable": True,
        },
        drop=("operator_review", "profile_id", "profile_ids"),
    )
    target = commons / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(hp.hub_contracts.normalize_text(rendered).rstrip("\n") + "\n", encoding="utf-8")
    run_git("add", "-A", cwd=commons)
    trailers = f"Intake-ID: {intake_id}"
    if proof:
        trailers += "\nGate-Proof: " + json.dumps(gate_proof, ensure_ascii=False, sort_keys=True)
    run_git("commit", "-q", "-m", "note", "-m", trailers, cwd=commons)
    run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=commons)
    return {"note_id": note_id, "revision": revision, "gate_digest": gate_proof["gate_digest"], "doc_digest": doc_digest, "intake_id": intake_id, "policy": policy, "answers": answers}


def commit_all(commons: Path, message: str = "change") -> None:
    run_git("add", "-A", cwd=commons)
    run_git("commit", "-q", "-m", message, cwd=commons)
    run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=commons)


def write_domains(commons: Path, domains: dict) -> None:
    (commons / "policy" / "official-doc-domains.json").write_text(json.dumps(domains), encoding="utf-8")
    commit_all(commons, "policy")


def verified_pricing(overhead: int = 100, max_input_tokens: int = 32_000, **overrides) -> hp.Pricing:
    from portwright import jev

    contract = {
        "schema_version": 1,
        "verified": True,
        "model": jev.MODEL,
        "endpoint": jev.ENDPOINT,
        "verified_at": "2026-09-20",
        "source_url": "https://api.example.com/pricing",
        "input_micro_usd_per_million": 3_000_000,
        "output_micro_usd_per_million": 15_000_000,
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": 1024,
        "max_questions": 4,
        "per_request_overhead_micro_usd": overhead,
        **overrides,
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pricing.json"
        path.write_text(json.dumps(contract), encoding="utf-8")
        return hp.load_pricing(path)


# --------------------------------------------------------------------------- hub fixture


class HubFixture:
    """SQLite-backed /admin + /sync surface used by the pipeline."""

    def __init__(self, tls: tuple[Path, Path] | None = None):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.cap = 10_000_000
        self.deliver = True
        self.tamper_sync = False
        self.fail_events = False
        self.fail_intake = False
        self.omit_eligibility: set[str] = set()
        self.delivered: dict[str, list[dict]] = {}
        self.objects: dict[tuple[str, str], bytes] = {}
        self.current: dict | None = None
        # H2 contract: GET {snapshot, etag}; POST {snapshot, expected_etag}; the deployed
        # Hub starts with a valid (empty) projection.
        self.recalls = {"sequence": 1, "entries": [], "commit": "0" * 40, "etag": "r1"}
        self.fail_settle_after: int | None = None
        self.settles = 0
        self.created = int(time.time())
        self._schema()
        handler = type("Handler", (_Handler,), {"fixture": self})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.scheme = "http"
        if tls is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(tls[0]), str(tls[1]))
            self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
            self.scheme = "https"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.conn.close()

    def _schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE tokens (hash TEXT PRIMARY KEY, lineage_id TEXT, org TEXT, scope TEXT, revoked INTEGER DEFAULT 0, expires INTEGER);
            CREATE TABLE intake (id TEXT PRIMARY KEY, token_hash TEXT, lineage_id TEXT, kind TEXT, service_id TEXT,
              target_note_id TEXT, expected_revision TEXT, body TEXT, doc_url TEXT, success_evidence TEXT,
              state TEXT DEFAULT 'pending', reason_code TEXT, note_id TEXT, revision TEXT, gate_digest TEXT,
              commit_sha TEXT, created INTEGER, updated INTEGER);
            CREATE TABLE events (id TEXT PRIMARY KEY, intake_id TEXT, note_id TEXT, revision TEXT, lineage_id TEXT,
              token_hash TEXT, kind TEXT, action TEXT DEFAULT '', operation_id TEXT, value INTEGER DEFAULT 0,
              payload TEXT DEFAULT '{}', month TEXT, created INTEGER, eligible INTEGER DEFAULT 1,
              UNIQUE(kind, operation_id));
            """
        )
        self.conn.commit()

    # -- fixture helpers ---------------------------------------------------
    def add_token(self, token_hash: str, *, lineage_id: str = "lineage-1", scope: str = "operator", revoked: int = 0) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO tokens VALUES (?,?,?,?,?,?)",
                (token_hash, lineage_id, "public", scope, revoked, self.created + 86400),
            )
            self.conn.commit()

    def add_intake(self, *, kind="procedure", service_id="acme", body=None, doc_url="https://docs.acme.com/deploy",
                   evidence=None, expected_revision=None, target_note_id=None, state="pending",
                   token_eligible=True, created=None, revision=None, note_id=None, commit_sha=None,
                   gate_digest=None, intake_id=None) -> str:
        intake_id = intake_id or str(uuid.uuid4())
        with self.lock:
            self.conn.execute(
                "INSERT INTO intake (id, token_hash, lineage_id, kind, service_id, target_note_id, expected_revision,"
                " body, doc_url, success_evidence, state, revision, note_id, commit_sha, gate_digest, created, updated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    intake_id,
                    "token-hash",
                    "lineage-1",
                    kind,
                    service_id,
                    target_note_id,
                    expected_revision,
                    body,
                    doc_url,
                    json.dumps(evidence if evidence is not None else {"action": "ran acme deploy --prod in staging", "outcome": "release completed and health check passed"}, ensure_ascii=False),
                    state,
                    revision,
                    note_id,
                    commit_sha,
                    gate_digest,
                    created or self.created,
                    created or self.created,
                ),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO tokens VALUES (?,?,?,?,?,?)",
                ("token-hash", "lineage-1", "public", "submit", 0 if token_eligible else 1, self.created + 86400),
            )
            self.conn.commit()
        return intake_id

    def add_event(self, *, kind, note_id, revision, lineage_id, action="", payload=None, eligible=1, operation_id="", value=0, month=None) -> str:
        event_id = str(uuid.uuid4())
        with self.lock:
            self.conn.execute(
                "INSERT INTO events (id, note_id, revision, lineage_id, token_hash, kind, action, operation_id, value, payload, month, created, eligible)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    note_id,
                    revision,
                    lineage_id,
                    "hash-" + lineage_id,
                    kind,
                    action,
                    operation_id or str(uuid.uuid4()),
                    value,
                    json.dumps(payload or {}),
                    month,
                    self.created,
                    eligible,
                ),
            )
            self.conn.commit()
        return event_id

    def intake_state(self, intake_id: str) -> str:
        row = self.conn.execute("SELECT state FROM intake WHERE id=?", (intake_id,)).fetchone()
        return row["state"] if row else ""

    def intake_row(self, intake_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM intake WHERE id=?", (intake_id,)).fetchone()
        return dict(row) if row else {}

    def event_count(self, kind: str) -> int:
        return self.conn.execute("SELECT count(*) c FROM events WHERE kind=?", (kind,)).fetchone()["c"]

    def register(self, commit: str, notes: list[dict]) -> None:
        if self.deliver:
            self.delivered[commit] = notes

    # -- request handling --------------------------------------------------
    def handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parsed = urlsplit(handler.path)
        query = {key: value[0] for key, value in parse_qs(parsed.query).items()}
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        route = (method, parsed.path)
        with self.lock:
            status, payload, headers = self._dispatch(route, query, body, raw)
        handler.send_response(status)
        for key, value in (headers or {}).items():
            handler.send_header(key, value)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _deliver_from_objects(self, commit: str) -> None:
        """A real promote publishes the note-index; /sync then serves those bytes."""
        payload = self.objects.get((commit, "note-index.json"))
        if payload is None:
            return
        try:
            index = json.loads(payload.decode("utf-8"))
        except ValueError:
            return
        notes = []
        for entry in index.get("notes", []):
            if not isinstance(entry, dict):
                continue
            shipped = self.objects.get((commit, entry.get("path", "")))
            if shipped is None:
                continue
            text = shipped.decode("utf-8", "replace")
            notes.append(
                {
                    "note_id": entry.get("note_id"),
                    "revision": entry.get("revision"),
                    "grade": entry.get("grade"),
                    "path": entry.get("path"),
                    "file_digest": hp.hub_contracts.sha256_digest(shipped),
                    "size": len(shipped),
                    "text": text,
                }
            )
        self.delivered[commit] = notes

    def _intake_items(self, state: str, limit: int, after: str | None) -> tuple[list[dict], str | None]:
        rows = self.conn.execute(
            "SELECT * FROM intake WHERE state=? ORDER BY created, id", (state,)
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            token = self.conn.execute("SELECT revoked, expires FROM tokens WHERE hash=?", (row["token_hash"],)).fetchone()
            if row["id"] not in self.omit_eligibility:
                item["eligible"] = bool(token and not token["revoked"] and token["expires"] > int(time.time()))
            item["success_evidence"] = json.loads(item["success_evidence"] or "{}")
            if after and item["id"] <= after:
                continue
            items.append(item)
        # Like the Worker: one page of `limit` rows and a cursor when more remain.
        return items[:limit], (items[limit - 1]["id"] if len(items) > limit else None)

    def _dispatch(self, route, query, body, raw):
        method, path = route
        if (method, path) == ("GET", "/admin/intake"):
            if self.fail_intake:
                return 500, {}, {}
            items, cursor = self._intake_items(query.get("state", "pending"), int(query.get("limit", "20")), query.get("after"))
            return 200, {"items": items, "next_cursor": cursor}, {}
        if (method, path) == ("GET", "/admin/events"):
            if self.fail_events:
                return 500, {}, {}
            sql = "SELECT * FROM events"
            clauses, args = [], []
            if query.get("note_id"):
                clauses.append("note_id=?"); args.append(query["note_id"])
            if query.get("revision"):
                clauses.append("revision=?"); args.append(query["revision"])
            if query.get("kind"):
                clauses.append("kind=?"); args.append(query["kind"])
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created, id"
            items = []
            for row in self.conn.execute(sql, args).fetchall():
                item = dict(row)
                item["payload"] = json.loads(item["payload"] or "{}")
                item["eligible"] = bool(item["eligible"])
                items.append(item)
            offset = int(query.get("after") or 0)
            limit = int(query.get("limit", "100"))
            page = items[offset:offset + limit]
            cursor = str(offset + limit) if offset + limit < len(items) else None
            return 200, {"items": page, "next_cursor": cursor}, {}
        if (method, path) == ("POST", "/admin/intake"):
            return self._intake_action(body)
        if (method, path) == ("POST", "/admin/budget"):
            return self._budget(body)
        if (method, path) == ("GET", "/admin/budget"):
            used = self.conn.execute(
                "SELECT COALESCE(SUM(value),0) c FROM events WHERE kind IN ('cost','reservation')"
            ).fetchone()["c"]
            return 200, {"month": "2026-09", "cap_micro_usd": self.cap, "cost_micro_usd": used, "reserved_micro_usd": 0, "available_micro_usd": max(self.cap - used, 0)}, {}
        if (method, path) == ("GET", "/admin/current"):
            if self.current is None:
                return 404, {}, {}
            return 200, {"current": {k: v for k, v in self.current.items() if k != "etag"}, "etag": self.current["etag"]}, {}
        if method == "PUT" and path.startswith("/admin/releases/"):
            parts = path.split("/", 4)
            if len(parts) == 5:
                if (parts[3], parts[4]) in self.objects and self.objects[(parts[3], parts[4])] != raw:
                    return 409, {"error": "immutable"}, {}
                self.objects[(parts[3], parts[4])] = raw
            return 200, {"created": True}, {}
        if (method, path) == ("POST", "/admin/verify"):
            return 200, {"verified": True}, {}
        if (method, path) == ("POST", "/admin/promote"):
            commit = body.get("commit", "")
            self.current = {"commit": commit, "inventory_digest": body.get("inventory_digest", ""), "sequence": body.get("sequence", 1), "etag": "etag-" + commit[:8]}
            self._deliver_from_objects(commit)
            return 200, {"current": {"commit": commit}}, {}
        if (method, path) == ("GET", "/admin/recalls"):
            snapshot = {"schema_version": 1, "audience": "public", "commit": self.recalls["commit"], "sequence": self.recalls["sequence"], "entries": self.recalls["entries"]}
            return 200, {"snapshot": snapshot, "etag": self.recalls["etag"]}, {}
        if (method, path) == ("POST", "/admin/recalls"):
            snapshot = body.get("snapshot") if isinstance(body, dict) and set(body) == {"snapshot", "expected_etag"} else None
            if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1 or snapshot.get("audience") != "public" or not isinstance(snapshot.get("entries"), list):
                return 400, {"code": "INVALID_PARAMS"}, {}
            incoming = {(entry.get("note_id"), entry.get("revision")) for entry in snapshot["entries"]}
            existing = {(entry.get("note_id"), entry.get("revision")) for entry in self.recalls["entries"]}
            if body["expected_etag"] != self.recalls["etag"]:
                return 409, {"error": "etag"}, {}
            if not existing <= incoming or not isinstance(snapshot.get("sequence"), int) or snapshot["sequence"] <= self.recalls["sequence"]:
                return 409, {"error": "monotonic"}, {}
            self.recalls = {"sequence": snapshot["sequence"], "entries": snapshot["entries"], "commit": snapshot.get("commit"), "etag": f"r{snapshot['sequence']}"}
            return 200, {**snapshot, "etag": self.recalls["etag"]}, {}
        if (method, path) == ("GET", "/sync"):
            current = self.current or {}
            commit = current.get("commit", "")
            notes = self.delivered.get(commit, [])
            # The Worker applies the recall projection to every read surface.
            recalled = {(entry.get("note_id"), entry.get("revision")) for entry in self.recalls["entries"]}
            notes = [note for note in notes if (note.get("note_id"), note.get("revision")) not in recalled]
            if self.tamper_sync:
                notes = [dict(note, text=str(note.get("text", "")) + "tampered", file_digest=note.get("file_digest")) for note in notes]
            identity = {"commit": commit, "inventory_digest": current.get("inventory_digest", "")}
            return 200, {"schema_version": 1, "audience": "public", "release_identity": identity, "notes": notes}, {}
        return 404, {}, {}

    def _intake_action(self, body):
        action = body.get("action")
        intake_id = body.get("intake_id")
        row = self.conn.execute("SELECT * FROM intake WHERE id=?", (intake_id,)).fetchone() if intake_id else None
        if action in ("hold", "reject"):
            if row is None:
                return 404, {"error": "missing"}, {}
            state = "held" if action == "hold" else "rejected"
            self.conn.execute(
                "UPDATE intake SET state=?, reason_code=?, body=NULL, doc_url=NULL, success_evidence=NULL, updated=? WHERE id=?",
                (state, body.get("reason_code", ""), int(time.time()), intake_id),
            )
            self.conn.commit()
            return 200, {"intake_id": intake_id, "state": state}, {}
        if action == "committed":
            self.conn.execute(
                "UPDATE intake SET state='committed', note_id=?, revision=?, gate_digest=?, commit_sha=?, updated=? WHERE id=?",
                (body.get("note_id"), body.get("revision"), body.get("gate_digest"), body.get("commit"), int(time.time()), intake_id),
            )
            self.conn.commit()
            return 200, {"intake_id": intake_id, "state": "committed"}, {}
        if action == "ack":
            if row is None:
                return 404, {"error": "missing"}, {}
            if row["commit_sha"] != body.get("commit") or row["revision"] != body.get("revision"):
                return 409, {"error": "conflict"}, {}
            self.conn.execute(
                "UPDATE intake SET state='published', body=NULL, doc_url=NULL, success_evidence=NULL, updated=? WHERE id=?",
                (int(time.time()), intake_id),
            )
            self.conn.commit()
            return 200, {"intake_id": intake_id, "state": "published"}, {}
        if action == "validate_confirm":
            event = self.conn.execute("SELECT * FROM events WHERE id=?", (body.get("event_id"),)).fetchone()
            if event is None:
                return 404, {"error": "missing"}, {}
            payload = json.loads(event["payload"] or "{}")
            if payload.get("payload_digest") != body.get("expected_payload_digest") or payload.get("validation") != "pending":
                return 409, {"error": "conflict"}, {}
            payload["validation"] = body.get("validation")
            payload["gate_digest"] = body.get("gate_digest")
            if payload["validation"] == "rejected":
                # §A-5: an identifier rejection erases the confirmation text.
                payload.pop("success_evidence", None)
            self.conn.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), body.get("event_id")))
            self.conn.commit()
            return 200, {"event_id": body.get("event_id"), "validation": payload["validation"]}, {}
        if action == "redact_report":
            if set(body) != {"action", "event_id", "expected_payload_digest"}:
                return 400, {"error": "shape"}, {}
            event = self.conn.execute("SELECT * FROM events WHERE id=? AND kind='report'", (body["event_id"],)).fetchone()
            if event is None:
                return 404, {"error": "missing"}, {}
            payload = json.loads(event["payload"])
            if payload.get("payload_digest") != body["expected_payload_digest"]:
                return 409, {"error": "conflict"}, {}
            payload.pop("reason", None)
            self.conn.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), body["event_id"]))
            self.conn.commit()
            return 200, {"event_id": body["event_id"]}, {}
        if action == "review":
            self.conn.execute(
                "INSERT INTO events (id, note_id, revision, lineage_id, token_hash, kind, action, operation_id, value, payload, created, eligible)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    body.get("note_id"),
                    body.get("revision"),
                    "lineage-operator",
                    "hash-operator",
                    "ack",
                    "review_" + body.get("decision", ""),
                    body.get("request_id", str(uuid.uuid4())),
                    0,
                    json.dumps({"revision": body.get("revision"), "gate_digest": body.get("gate_digest"), "events_digest": body.get("events_digest")}),
                    int(time.time()),
                    1,
                ),
            )
            self.conn.commit()
            return 200, {"recorded": True}, {}
        return 400, {"error": "action"}, {}

    def _budget(self, body):
        action = body.get("action")
        month = "2026-09"
        if action == "reserve":
            operation_id = body.get("operation_id")
            duplicate = self.conn.execute("SELECT id FROM events WHERE kind='reservation' AND operation_id=?", (operation_id,)).fetchone()
            if duplicate:
                return 200, {"granted": False, "duplicate": True}, {}
            value = int(body.get("reserve_micro_usd", 0))
            # Mirror the Worker: intake reservations need an intake row (D1 enforces the
            # events.intake_id foreign key and answers 500), confirm needs a note revision.
            purpose = body.get("purpose")
            intake_id = body.get("intake_id")
            if purpose not in ("intake", "confirm", "bundle") or (purpose == "intake" and not intake_id) or (
                    purpose == "confirm" and not body.get("note_id")):
                return 400, {"error": "invalid_params"}, {}
            if intake_id and self.conn.execute("SELECT 1 FROM intake WHERE id=?", (intake_id,)).fetchone() is None:
                return 500, {"error": "internal"}, {}
            used = self.conn.execute(
                "SELECT COALESCE(SUM(value),0) c FROM events WHERE month=? AND kind IN ('cost','reservation')", (month,)
            ).fetchone()["c"]
            if value <= 0 or used + value > self.cap:
                return 409, {"error": "budget"}, {}
            reservation_id = str(uuid.uuid4())
            self.conn.execute(
                "INSERT INTO events (id, intake_id, kind, action, operation_id, value, payload, month, created, eligible)"
                " VALUES (?,?,?,?,?,?,?,?,?,1)",
                (reservation_id, intake_id, "reservation", "reserved", operation_id, value,
                 json.dumps({"purpose": purpose, **{key: body[key] for key in ("request_digest", "bytes") if key in body}}),
                 month, int(time.time())),
            )
            self.conn.commit()
            return 200, {"granted": True, "reservation_id": reservation_id, "month": month, "expires_at": self.created + 3600}, {}
        if action == "settle":
            self.settles += 1
            if self.fail_settle_after is not None and self.settles > self.fail_settle_after:
                return 500, {}, {}
            reservation = self.conn.execute(
                "SELECT * FROM events WHERE kind='reservation' AND id=?", (body.get("reservation_id"),)
            ).fetchone()
            if reservation is None:
                return 404, {"error": "missing"}, {}
            self.conn.execute(
                "INSERT INTO events (id, kind, action, operation_id, value, payload, month, created, eligible)"
                " VALUES (?,?,?,?,?,?,?,?,1)",
                (str(uuid.uuid4()), "cost", "settled", reservation["operation_id"], int(body.get("actual_micro_usd", 0)), json.dumps(body.get("usage") or {}), reservation["month"], int(time.time())),
            )
            self.conn.execute("UPDATE events SET value=0, action='settled' WHERE id=?", (body.get("reservation_id"),))
            self.conn.commit()
            return 200, {"settled": True, "month": reservation["month"]}, {}
        return 400, {"error": "action"}, {}


class _Handler(BaseHTTPRequestHandler):
    fixture: HubFixture

    def log_message(self, *args):  # silence
        return

    def do_GET(self):
        self.fixture.handle(self, "GET")

    def do_POST(self):
        self.fixture.handle(self, "POST")

    def do_PUT(self):
        self.fixture.handle(self, "PUT")


def test_origin(raw: str) -> str:
    """Allow the loopback HTTP fixture; every other origin keeps the https rule."""
    if raw.startswith("http://127.0.0.1:"):
        return raw.rstrip("/")
    return _REAL_CLEAN_ORIGIN(raw)


_REAL_CLEAN_ORIGIN = hp._clean_origin


# --------------------------------------------------------------------------- pricing


class PricingTests(unittest.TestCase):
    def test_unverified_contract_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps({"schema_version": 1, "verified": False}), encoding="utf-8")
            with self.assertRaises(hp.PricingUnavailable):
                hp.load_pricing(path)
            path.write_text(json.dumps({"schema_version": 1, "verified": True, "model": "m", "endpoint": "e", "verified_at": "d", "source_url": "u"}), encoding="utf-8")
            with self.assertRaises(hp.PricingUnavailable):
                hp.load_pricing(path)

    def test_endpoint_or_model_mismatch_is_refused(self):
        from portwright import jev

        contract = {
            "schema_version": 1,
            "verified": True,
            "model": jev.MODEL,
            "endpoint": "https://api.example.com/v1",
            "verified_at": "2026-09-20",
            "source_url": "https://api.example.com/pricing",
            "input_micro_usd_per_million": 3_000_000,
            "output_micro_usd_per_million": 15_000_000,
            "max_input_tokens": 32_000,
            "max_output_tokens": 1024,
            "max_questions": 4,
            "per_request_overhead_micro_usd": 100,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaises(hp.PricingUnavailable):
                hp.load_pricing(path)

    def test_request_above_provider_input_cap_is_refused(self):
        pricing = verified_pricing(max_input_tokens=10)
        with self.assertRaises(hp.PricingUnavailable):
            pricing.max_request_micro_usd(50)

    def test_oversized_request_is_refused(self):
        with self.assertRaises(hp.RequestTooLarge):
            verified_pricing().max_request_micro_usd(hp.MAX_MODEL_BYTES + 1)

    def test_usage_includes_the_request_overhead(self):
        pricing = verified_pricing(overhead=100)
        tokens_only = (1_000 * 3_000_000 + 10 * 15_000_000 + 999_999) // 1_000_000
        self.assertEqual(pricing.usage_micro_usd({"input_tokens": 1_000, "output_tokens": 10}), tokens_only + 100)

    def test_ceiling_grows_with_request_bytes_and_bounds_usage(self):
        pricing = verified_pricing()
        small = pricing.max_request_micro_usd(1_000)
        large = pricing.max_request_micro_usd(20_000)
        self.assertLess(small, large)
        self.assertGreater(small, 0)
        actual = pricing.usage_micro_usd({"input_tokens": 1_000, "output_tokens": 10})
        self.assertIsNotNone(actual)
        self.assertLessEqual(actual, large)
        self.assertIsNone(pricing.usage_micro_usd({"input_tokens": 5}))
        with self.assertRaises(hp.PricingUnavailable):
            pricing.max_request_micro_usd(10, questions=9)

    def test_zero_rates_are_valid_but_caps_must_stay_positive(self):
        free_output = verified_pricing(overhead=0, max_input_tokens=24_000, input_micro_usd_per_million=42_000, output_micro_usd_per_million=0, max_output_tokens=4)
        self.assertEqual(free_output.usage_micro_usd({"input_tokens": 1_000_000, "output_tokens": 50}), 42_000)
        self.assertEqual(free_output.max_request_micro_usd(24_000), (24_000 * 42_000 + 999_999) // 1_000_000)
        for field in ("max_input_tokens", "max_output_tokens", "max_questions"):
            with self.subTest(field=field), self.assertRaises(hp.PricingUnavailable):
                verified_pricing(**{field: 0})
        with self.assertRaises(hp.PricingUnavailable):
            verified_pricing(input_micro_usd_per_million=-1)


# --------------------------------------------------------------------------- gate


class GateTests(unittest.TestCase):
    def setUp(self):
        self.domains = {"schema_version": 1, "services": {"acme": {"hosts": ["docs.acme.com"], "status": "verified"}}}
        self.fetch_calls: list[str] = []

        def fake_fetch(url, hosts, prefixes):
            self.fetch_calls.append(url)
            return "Acme deploy documentation body. " + "x" * 40

        self.fetch_patch = patch.object(hp.doc_cache, "fetch_document", fake_fetch)
        self.fetch_patch.start()
        self.addCleanup(self.fetch_patch.stop)

    def item(self, **overrides) -> dict:
        base = {
            "id": str(uuid.uuid4()),
            "kind": "procedure",
            "service_id": "acme",
            "body": procedure_note(),
            "doc_url": "https://docs.acme.com/deploy",
            "success_evidence": {"action": "ran acme deploy --prod in staging", "outcome": "release completed and health check passed"},
            "created": int(time.time()),
            "state": "pending",
            "token_eligible": True,
            "git": {"exists": False},
        }
        base.update(overrides)
        return base

    def test_pass_returns_revision_and_digests(self):
        result = hp.gate_submission(self.item(), self.domains, _fixture_client(), None)
        self.assertEqual(result.state, "passed")
        self.assertTrue(result.revision.startswith("sha256:"))
        self.assertTrue(result.doc_digest.startswith("sha256:"))
        self.assertTrue(result.gate_digest.startswith("sha256:"))
        self.assertEqual(self.fetch_calls, ["https://docs.acme.com/deploy"])

    def test_off_domain_holds_before_any_fetch(self):
        result = hp.gate_submission(self.item(doc_url="https://evil.example.com/deploy"), self.domains, _fixture_client(), None)
        self.assertEqual((result.state, result.reason_code), ("held", "off_domain"))
        self.assertEqual(self.fetch_calls, [])

    def test_unknown_service_domain_holds(self):
        result = hp.gate_submission(self.item(service_id="acme", doc_url="https://docs.acme.com/deploy"), {"services": {}}, _fixture_client(), None)
        self.assertEqual(result.reason_code, "official_domain_unknown")

    def test_secret_and_identifier_are_rejected(self):
        secret = hp.gate_submission(self.item(body=procedure_note() + "\nkey=AKIAABCDEFGHIJKLMNOP"), self.domains, _fixture_client(), None)
        self.assertEqual((secret.state, secret.reason_code), ("rejected", "secret"))
        identifier = hp.gate_submission(self.item(body=procedure_note() + "\nmail me at person@example.com"), self.domains, _fixture_client(), None)
        self.assertEqual((identifier.state, identifier.reason_code), ("rejected", "identifier"))
        # Built at runtime so the public export's home-path substitution cannot rewrite it.
        home_path = "/" + "home" + "/someone/project/bin"
        private_path = hp.gate_submission(self.item(body=procedure_note() + "\npath " + home_path), self.domains, _fixture_client(), None)
        self.assertEqual((private_path.state, private_path.reason_code), ("rejected", "identifier"))

    def test_screening_decodes_percent_escapes_exactly_once(self):
        # Shared corpus rule (install/secret-patterns.json normalization): raw + one decode.
        once = hp.gate_submission(self.item(body=procedure_note() + "\nkey %41KIAIOSFODNN7EXAMPLE used"), self.domains, _fixture_client(), None)
        self.assertEqual((once.state, once.reason_code), ("rejected", "secret"))
        twice = hp.gate_submission(self.item(body=procedure_note() + "\nkey %2541KIAIOSFODNN7EXAMPLE used"), self.domains, _fixture_client(), None)
        self.assertEqual(twice.state, "passed")

    def test_official_document_matches_are_masked_before_the_model(self):
        seen = []

        class Client:
            mode = "fixture"

            def ask(self, fixture_id, state, questions):
                seen.append(state["official_document"])
                return Judgment(status="ok", source="fixture", answers=passing_answers(), usage={})

        # Official pages quote placeholder keys and hosts; only the page is masked.
        example = "Set key " + "AKIA" + "IOSFODNN7EXAMPLE" + " and open http://" + "localhost" + ":8080 today."
        with patch.object(hp.doc_cache, "fetch_document", lambda url, hosts, prefixes: example):
            masked = hp.gate_submission(self.item(), self.domains, Client(), None)
        self.assertEqual(masked.state, "passed")
        self.assertEqual(seen, ["Set key [redacted] and open http://[redacted]:8080 today."])
        self.assertEqual(masked.doc_digest, hp.hub_contracts.sha256_digest(seen[0]))
        # A match that survives masking only in its percent-decoded form still holds.
        encoded = "Set key %41KIA" + "IOSFODNN7EXAMPLE now."
        with patch.object(hp.doc_cache, "fetch_document", lambda url, hosts, prefixes: encoded):
            held = hp.gate_submission(self.item(), self.domains, Client(), None)
        self.assertEqual((held.state, held.reason_code), ("held", "document_unavailable"))
        self.assertEqual(len(seen), 1)

    def test_request_limit_is_thirty_two_thousand_bytes_without_truncation(self):
        self.assertEqual(hp.MAX_MODEL_BYTES, 32_000)
        self.assertEqual(hp.load_pricing().max_input_tokens, 32_000)
        body = "Acme deploy documentation body. "
        with patch.object(hp.doc_cache, "fetch_document", lambda url, hosts, prefixes: body * 800):
            fits = hp.gate_submission(self.item(), self.domains, _fixture_client(), None)
        self.assertEqual(fits.state, "passed", "a 25.6 KB page fits the raised limit")
        # Oversize pages are held before any regex pass (some patterns are quadratic on long runs).
        with patch.object(hp.doc_cache, "fetch_document", lambda url, hosts, prefixes: body * 1100), \
                patch.object(hp, "_mask_document", side_effect=AssertionError("masked an oversize page")):
            too_large = hp.gate_submission(self.item(), self.domains, _fixture_client(), None)
        self.assertEqual((too_large.state, too_large.reason_code), ("held", "evidence_too_large"))

    def test_lessons_ask_for_same_feature_without_contradiction(self):
        asked = {}

        class Client:
            mode = "fixture"

            def ask(self, fixture_id, state, questions):
                asked[fixture_id] = questions
                return Judgment(status="ok", source="fixture", answers=passing_answers(), usage={})

        today = time.strftime("%Y-%m-%d", time.gmtime())
        lesson = self.item(kind="lesson", body=lesson_note(date=today))
        procedure = self.item()
        self.assertEqual(hp.gate_submission(lesson, self.domains, Client(), None).state, "passed")
        self.assertEqual(hp.gate_submission(procedure, self.domains, Client(), None).state, "passed")
        lesson_questions = asked[f"hub-gate-{lesson['id']}"]
        self.assertEqual(asked[f"hub-gate-{procedure['id']}"]["supported"], hp.NOUL_QUESTIONS["supported"])
        self.assertEqual(lesson_questions["supported"], hp.LESSON_SUPPORTED_QUESTION)
        for key in ("evidence_consistent", "malicious", "personal"):
            self.assertEqual(lesson_questions[key], hp.NOUL_QUESTIONS[key])

    def test_a_question_change_invalidates_existing_proofs(self):
        policy = hp.gate_policy(self.domains, "acme")
        self.assertEqual(policy["questions_digest"], hp.QUESTIONS_DIGEST)
        with patch.dict(hp.LESSON_SUPPORTED_QUESTION, {"instructions": "A different question."}):
            changed = hp.hub_contracts.canonical_digest({"procedure": hp.noul_questions("procedure"),
                                                          "lesson": hp.noul_questions("lesson"), "bounds": hp.NOUL_BOUNDS})
        self.assertNotEqual(changed, hp.QUESTIONS_DIGEST)
        proof = hp.gate_proof("service/acme", "sha256:" + "a" * 64, "sha256:" + "b" * 64,
                              {"supported": 0.95, "evidence_consistent": 0.95, "malicious": 0.01, "personal": 0.01},
                              {**policy, "questions_digest": changed})
        entry = {"gate_digest": proof["gate_digest"], "doc_digest": proof["doc_digest"]}
        self.assertEqual(hp._proof_issue(proof, entry, policy), "policy_changed")
        # Proofs written before the questions_digest field existed are a policy change too.
        legacy = {**proof, "policy": {key: value for key, value in policy.items() if key != "questions_digest"}}
        self.assertEqual(hp._proof_issue(legacy, entry, policy), "policy_changed")

    def test_evidence_not_specific_and_malformed_judgments_hold(self):
        vague = hp.gate_submission(self.item(success_evidence={"action": "ok", "outcome": "success"}), self.domains, _fixture_client(), None)
        self.assertEqual((vague.state, vague.reason_code), ("held", "evidence_not_specific"))
        single_word = hp.gate_submission(self.item(success_evidence={"action": "deploy", "outcome": "success"}), self.domains, _fixture_client(), None)
        self.assertEqual((single_word.state, single_word.reason_code), ("held", "evidence_not_specific"))
        missing_field = hp.gate_submission(self.item(success_evidence={"action": "ran deploy"}), self.domains, _fixture_client(), None)
        self.assertEqual(missing_field.state, "held")
        malformed = hp.gate_submission(self.item(), self.domains, _fixture_client(supported="0.99"), None)
        self.assertEqual((malformed.state, malformed.reason_code), ("held", "model_unavailable"))
        unknown_status = hp.gate_submission(self.item(), self.domains, _fixture_client(status="unknown"), None)
        self.assertEqual(unknown_status.state, "held")
        low_support = hp.gate_submission(self.item(), self.domains, _fixture_client(supported=0.4), None)
        self.assertEqual((low_support.state, low_support.reason_code), ("held", "evidence_not_specific"))
        malicious = hp.gate_submission(self.item(), self.domains, _fixture_client(malicious=0.5), None)
        self.assertEqual((malicious.state, malicious.reason_code), ("held", "injection_suspected"))
        personal = hp.gate_submission(self.item(), self.domains, _fixture_client(personal=0.6), None)
        self.assertEqual((personal.state, personal.reason_code), ("rejected", "identifier"))
        # A real injection also scores low document support; the security verdict must win.
        injection = hp.gate_submission(self.item(), self.domains, _fixture_client(supported=0.05, evidence_consistent=0.6, malicious=0.95), None)
        self.assertEqual((injection.state, injection.reason_code), ("held", "injection_suspected"))
        exposed = hp.gate_submission(self.item(), self.domains, _fixture_client(supported=0.5, malicious=0.9, personal=0.6), None)
        self.assertEqual((exposed.state, exposed.reason_code), ("rejected", "identifier"))

    def test_oversized_serialized_request_holds_before_reserve(self):
        big_document = ("문서 " * 8_000)  # > 24KiB of multibyte document text
        self.assertGreater(len(big_document.encode("utf-8")), hp.MAX_MODEL_BYTES)
        self.assertLess(len(procedure_note().encode("utf-8")), hp.MAX_TEXT_BYTES)
        budget = FakeBudget()

        def big_fetch(url, hosts, prefixes):
            return big_document

        with patch.object(hp.doc_cache, "fetch_document", big_fetch):
            result = hp.gate_submission(self.item(), self.domains, hp.ModelGateway(_fixture_client(), budget=budget, pricing=None), budget)
        self.assertEqual((result.state, result.reason_code), ("held", "evidence_too_large"))
        self.assertEqual(budget.calls, [])

    def test_server_owned_frontmatter_and_mismatched_id_hold(self):
        poisoned = procedure_note(extra="revision: sha256:" + "a" * 64)
        self.assertEqual(hp.gate_submission(self.item(body=poisoned), self.domains, _fixture_client(), None).reason_code, "note_mismatch")
        wrong_id = procedure_note(service_id="other")
        self.assertEqual(hp.gate_submission(self.item(body=wrong_id), self.domains, _fixture_client(), None).reason_code, "note_mismatch")

    def test_revision_cas_for_updates(self):
        revision = "sha256:" + "c" * 64
        stale = self.item(
            target_note_id="service/acme",
            expected_revision=revision,
            git={"exists": True, "revision": "sha256:" + "d" * 64},
        )
        self.assertEqual(hp.gate_submission(stale, self.domains, _fixture_client(), None).reason_code, "revision_conflict")
        missing = self.item(target_note_id="service/acme", expected_revision=revision, git={"exists": False})
        self.assertEqual(hp.gate_submission(missing, self.domains, _fixture_client(), None).reason_code, "revision_conflict")
        new_without_revision = self.item(git={"exists": True, "revision": revision})
        self.assertEqual(hp.gate_submission(new_without_revision, self.domains, _fixture_client(), None).reason_code, "existing_note_requires_revision")

    def test_live_call_without_pricing_or_budget_fails_closed(self):
        client = FakeLiveTransport()
        with patch.object(hp, "PRICING_PATH", ROOT / "install" / "missing-pricing.json"):
            no_pricing = hp.gate_submission(self.item(), self.domains, client, FakeBudget())
        self.assertEqual((no_pricing.state, no_pricing.reason_code), ("held", "pricing_unverified"))
        self.assertEqual(client.sent, [])
        gateway = hp.ModelGateway(FakeLiveTransport(), budget=None, pricing=verified_pricing())
        no_budget = hp.gate_submission(self.item(), self.domains, gateway, None)
        self.assertEqual((no_budget.state, no_budget.reason_code), ("held", "budget_unavailable"))

    def test_budget_denial_holds_and_secret_in_request_is_rejected(self):
        budget = FakeBudget()
        budget.deny = True
        denied = hp.gate_submission(self.item(), self.domains, hp.ModelGateway(FakeLiveTransport(), budget=budget, pricing=verified_pricing()), budget)
        self.assertEqual((denied.state, denied.reason_code), ("held", "budget_exhausted"))
        poisoned = self.item(body=procedure_note() + "\ntoken: " + "ghp_" + "A" * 20)
        rejected = hp.gate_submission(poisoned, self.domains, _fixture_client(), None)
        self.assertEqual(rejected.state, "rejected")


def _fixture_client(**answer_overrides) -> SimpleNamespace:
    """A judgment-returning stand-in in fixture mode (no network, no reservation)."""
    status = answer_overrides.pop("status", "ok")

    class _Client:
        mode = "fixture"

        def ask(self, fixture_id, state, questions):
            return Judgment(status=status, source="fixture", answers=passing_answers(**answer_overrides) if status == "ok" else {}, usage={})

    return _Client()


# --------------------------------------------------------------------------- model gateway


class ModelGatewayTests(unittest.TestCase):
    def test_reserves_the_exact_sent_bytes_and_settles_within_the_reservation(self):
        budget = FakeBudget()
        client = FakeLiveTransport()
        gateway = hp.ModelGateway(client, budget=budget, pricing=verified_pricing())
        judgment = gateway.ask("fixture-x", {"note": "n"}, {"q": {"type": "noul"}})
        self.assertEqual(judgment.status, "ok")
        reserved = [call[1] for call in budget.calls if call[0] == "reserve"]
        settled = [call[2] for call in budget.calls if call[0] == "settle"]
        self.assertEqual(len(reserved), 1)
        self.assertEqual(reserved[0]["purpose"], "intake")
        self.assertEqual(reserved[0]["request_digest"], "sha256:" + hashlib.sha256(client.sent[0]).hexdigest())
        self.assertEqual(len(settled), 1)
        self.assertGreater(settled[0], 0)
        self.assertLessEqual(settled[0], reserved[0]["reserve_micro_usd"])

    def test_unknown_response_retains_the_whole_reservation(self):
        budget = FakeBudget()
        gateway = hp.ModelGateway(FakeLiveTransport(status="unknown"), budget=budget, pricing=verified_pricing())
        gateway.ask("fixture-y", {}, {})
        self.assertEqual([call[0] for call in budget.calls], ["reserve"])
        self.assertEqual(gateway.last_cost_micro_usd, 0)
        self.assertEqual(gateway.last_status, "reserved_retained")

    def test_failed_settlement_keeps_the_reservation_and_stops(self):
        budget = FakeBudget()
        budget.settle_ok = False
        gateway = hp.ModelGateway(FakeLiveTransport(), budget=budget, pricing=verified_pricing())
        with self.assertRaises(hp.PipelineError):
            gateway.ask("fixture-settle", {}, {})
        self.assertEqual(gateway.last_status, "settle_failed")
        self.assertEqual([call[0] for call in budget.calls if call[0] == "settle"], ["settle"], "no retry that could double-settle")

    def test_provider_overrun_keeps_the_reservation_and_stops(self):
        budget = FakeBudget()
        huge = {"input_tokens": 10_000_000, "output_tokens": 10_000_000}
        gateway = hp.ModelGateway(FakeLiveTransport(usage=huge), budget=budget, pricing=verified_pricing())
        with self.assertRaises(hp.BillingOverrun):
            gateway.ask("fixture-overrun", {}, {})
        self.assertEqual([call[0] for call in budget.calls], ["reserve"])
        self.assertEqual(gateway.last_status, "billing_overrun")

    def test_hook_not_called_is_refused(self):
        class Silent:
            mode = "live"
            before_send = None

            def ask(self, fixture_id, state, questions):
                return Judgment(status="ok", source="live", answers=passing_answers(), usage={})

        gateway = hp.ModelGateway(Silent(), budget=FakeBudget(), pricing=verified_pricing())
        with self.assertRaises(hp.PipelineError):
            gateway.ask("fixture-silent", {}, {})

    def test_expired_grant_never_sends(self):
        budget = FakeBudget()
        budget.expired = True
        client = FakeLiveTransport()
        gateway = hp.ModelGateway(client, budget=budget, pricing=verified_pricing())
        with self.assertRaises(hp.BudgetUnavailable):
            gateway.ask("fixture-expired", {}, {})
        self.assertEqual(client.sent, [])
        self.assertEqual([call[0] for call in budget.calls], ["reserve"])

    def test_live_transport_without_hook_is_refused(self):
        class NoHook:
            mode = "live"

            def ask(self, fixture_id, state, questions):
                return Judgment(status="ok", source="live", answers=passing_answers(), usage={})

        gateway = hp.ModelGateway(NoHook(), budget=FakeBudget(), pricing=verified_pricing())
        with self.assertRaises(hp.PipelineError):
            gateway.ask("fixture-nohook", {}, {})

    def test_missing_usage_settles_conservative_maximum(self):
        budget = FakeBudget()
        gateway = hp.ModelGateway(FakeLiveTransport(usage={}), budget=budget, pricing=verified_pricing())
        gateway.ask("fixture-z", {}, {})
        self.assertEqual(budget.calls[-1][0], "settle")
        self.assertEqual(budget.calls[-1][2], budget.calls[0][1]["reserve_micro_usd"])

    def test_fixture_mode_makes_no_reservation(self):
        budget = FakeBudget()
        gateway = hp.ModelGateway(_fixture_client(), budget=budget, pricing=None)
        gateway.ask("fixture-q", {}, {})
        self.assertEqual(budget.calls, [])
        self.assertEqual(gateway.last_cost_micro_usd, 0)

    def test_secret_in_serialized_request_raises_without_reservation(self):
        budget = FakeBudget()
        client = FakeLiveTransport()
        gateway = hp.ModelGateway(client, budget=budget, pricing=verified_pricing())
        with self.assertRaises(hp.ModelRequestRejected):
            gateway.ask("fixture-s", {"note": "ghp_" + "B" * 20}, {})
        self.assertEqual(budget.calls, [])
        self.assertEqual(client.sent, [])


# --------------------------------------------------------------------------- revision decisions


class EvaluateRevisionTests(unittest.TestCase):
    def setUp(self):
        self.note = {
            "note_id": "service/acme",
            "revision": "sha256:" + "a" * 64,
            "grade": "trial",
            "gate_digest": "sha256:" + "b" * 64,
        }
        self.now = int(time.time())

    def event(self, kind, lineage, **payload):
        event = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "note_id": self.note["note_id"],
            "revision": self.note["revision"],
            "lineage_id": lineage,
            "token_hash": "h-" + lineage,
            "created": self.now - 10,
            "eligible": True,
            "action": "",
            "payload": payload,
        }
        return event

    def test_three_lineages_plus_review_promotes(self):
        confirms = [self.event("confirm", f"l{n}", validation="passed", gate_digest=self.note["gate_digest"], payload_digest=f"d{n}") for n in range(3)]
        pending = hp.evaluate_revision(self.note, confirms, self.now)
        self.assertEqual(pending["action"], "none")
        self.assertTrue(pending["pending_review"])
        review = self.event("ack", "operator", revision=self.note["revision"], gate_digest=self.note["gate_digest"], events_digest=pending["events_digest"])
        review["action"] = "review_promote"
        decided = hp.evaluate_revision(self.note, confirms + [review], self.now)
        self.assertEqual(decided["action"], "promote")
        self.assertEqual(decided["review_event_id"], review["id"])

    def test_duplicate_or_ineligible_lineages_do_not_count(self):
        confirms = [self.event("confirm", f"l{n}", validation="passed", gate_digest=self.note["gate_digest"]) for n in range(3)]
        duplicated = confirms + [self.event("confirm", "l0", validation="passed", gate_digest=self.note["gate_digest"])]
        self.assertEqual(hp.evaluate_revision(self.note, duplicated, self.now)["confirm_lineages"], 2)
        ineligible = [dict(row, eligible=False) for row in confirms]
        self.assertEqual(hp.evaluate_revision(self.note, ineligible, self.now)["action"], "none")
        wrong_gate = [dict(row, payload={"validation": "passed", "gate_digest": "sha256:" + "e" * 64}) for row in confirms]
        self.assertEqual(hp.evaluate_revision(self.note, wrong_gate, self.now)["confirm_lineages"], 0)
        unvalidated = [self.event("confirm", f"l{n}", validation="pending", gate_digest=self.note["gate_digest"]) for n in range(3)]
        self.assertEqual(hp.evaluate_revision(self.note, unvalidated, self.now)["confirm_lineages"], 0)

    def test_trial_recall_needs_two_lineages_and_stable_needs_review(self):
        reports = [self.event("report", "l1", reason="x"), self.event("report", "l2", reason="y")]
        trial = hp.evaluate_revision(self.note, reports, self.now)
        self.assertEqual((trial["action"], trial["reason_code"]), ("recall", "distinct_reports"))
        self.assertIsNone(trial["review_event_id"])
        stable_note = dict(self.note, grade="stable")
        pending = hp.evaluate_revision(stable_note, reports, self.now)
        self.assertEqual((pending["action"], pending["pending_review"]), ("none", True))
        review = self.event("ack", "operator", revision=self.note["revision"], gate_digest=self.note["gate_digest"], events_digest=pending["events_digest"])
        review["action"] = "review_recall"
        stable = hp.evaluate_revision(stable_note, reports + [review], self.now)
        self.assertEqual((stable["action"], stable["reason_code"]), ("recall", "operator_confirmed"))

    def test_promotion_is_not_repeated_for_stable(self):
        stable_note = dict(self.note, grade="stable")
        confirms = [self.event("confirm", f"l{n}", validation="passed", gate_digest=self.note["gate_digest"]) for n in range(3)]
        self.assertEqual(hp.evaluate_revision(stable_note, confirms, self.now)["action"], "none")


# --------------------------------------------------------------------------- git writer


class GitWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.work = make_commons(self.tmp)
        self.repo = hp.CommonsRepo(self.work)
        self.base = self.repo.head()

    def test_write_stage_commit_push_and_allowlist(self):
        self.repo.write("services/acme.md", b"---\nid: acme\ngrade: trial\n---\nbody\n")
        with self.assertRaises(hp.GitUnsafe):
            self.repo.write("receipts/x.json", b"{}")
        with self.assertRaises(hp.GitUnsafe):
            self.repo.write("services/../escape.md", b"x")
        with self.assertRaises(hp.GitUnsafe):
            self.repo.write(".github/workflows/hub.yml", b"x")
        self.repo.stage(["services/acme.md"])
        self.repo.check_staged(["services/acme.md"], [])
        head = self.repo.commit_files("feat(hub): acme를 trial로 반영한다", ["Intake-ID: " + str(uuid.uuid4())])
        self.assertEqual(self.repo.push(self.base), head)
        self.assertEqual(self.repo.remote_head(), head)

    def test_staged_set_mismatch_and_executable_mode_are_refused(self):
        self.repo.write("services/one.md", b"---\nid: one\ngrade: trial\n---\nbody\n")
        self.repo.stage(["services/one.md"])
        with self.assertRaises(hp.GitUnsafe):
            self.repo.check_staged(["services/other.md"], [])
        self.repo.commit_files("feat(hub): one을 trial로 반영한다", [])
        second = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(second, ignore_errors=True))
        work = make_commons(second)
        repo = hp.CommonsRepo(work)
        target = work / "services" / "exec.md"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"---\nid: exec\n---\nbody\n")
        run_git("add", "services/exec.md", cwd=work)
        run_git("update-index", "--chmod=+x", "services/exec.md", cwd=work)
        with self.assertRaises(hp.GitUnsafe):
            repo.check_staged(["services/exec.md"], [])

    def test_non_fast_forward_and_stale_remote_are_refused(self):
        self.repo.write("services/acme.md", b"---\nid: acme\ngrade: trial\n---\nbody\n")
        self.repo.stage(["services/acme.md"])
        self.repo.commit_files("feat(hub): acme를 trial로 반영한다", [])
        other = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(other, ignore_errors=True))
        rival = other / "rival"
        run_git("clone", "-q", str(self.tmp / "origin.git"), str(rival))
        (rival / "rival.md").write_text("rival", encoding="utf-8")
        run_git("add", "-A", cwd=rival)
        run_git("commit", "-q", "-m", "rival", cwd=rival)
        run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=rival)
        rival_head = run_git("rev-parse", "HEAD", cwd=rival).stdout.strip()
        with self.assertRaises(hp.GitUnsafe):
            self.repo.push(rival_head)
        self.repo.git("fetch", "origin", check=False)
        with self.assertRaises(hp.GitUnsafe):
            self.repo.push(self.base)

    def test_delete_status_only_allowed_for_declared_deletes(self):
        self.repo.write("services/acme.md", b"---\nid: acme\ngrade: trial\n---\nbody\n")
        self.repo.stage(["services/acme.md"])
        self.repo.commit_files("feat(hub): acme를 trial로 반영한다", [])
        self.repo.remove("services/acme.md")
        self.repo.stage(["services/acme.md"])
        with self.assertRaises(hp.GitUnsafe):
            self.repo.check_staged(["services/acme.md"], [])

    def test_trailer_recovery_maps_intakes_without_receipts(self):
        intake_id = str(uuid.uuid4())
        self.repo.write("services/acme.md", b"---\nid: acme\ngrade: trial\n---\nbody\n")
        self.repo.stage(["services/acme.md"])
        head = self.repo.commit_files("feat(hub): acme를 trial로 반영한다", [f"Intake-ID: {intake_id}"])
        self.assertEqual(self.repo.intake_commits()[intake_id], head)
        self.assertFalse((self.work / "receipts").exists())


# --------------------------------------------------------------------------- hub client / budget


class HubClientTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HubFixture()
        self.addCleanup(self.fixture.close)
        self.clean_patch = patch.object(hp, "_clean_origin", test_origin)
        self.clean_patch.start()
        self.addCleanup(self.clean_patch.stop)
        self.client = hp.HubClient(self.fixture.url, "workflow-token")

    def test_origin_validation_still_requires_https_outside_the_fixture(self):
        for bad in ("http://example.com", "https://user:pw@example.com", "https://example.com/path?q=1", "https://example.com/#frag"):
            with self.assertRaises(hp.PipelineError):
                hp.HubClient(bad, "t")
        self.assertEqual(hp.HubClient("https://hub.example.com", "t").origin, "https://hub.example.com")

    def test_paging_fails_on_a_repeated_cursor(self):
        with patch.object(HubFixture, "_dispatch", lambda self, route, query, body, raw: (200, {"items": [{"id": "x"}], "next_cursor": "cursor-1"}, {})):
            with self.assertRaises(hp.PipelineError):
                self.client.intake("held")

    def test_budget_reserve_settle_and_duplicate(self):
        client = self.client
        budget = hp.Budget(client)
        grant = budget.reserve(operation_id="op-1", purpose="bundle", target_digest="sha256:" + "a" * 64, reserve_micro_usd=5000)
        self.assertEqual(grant["month"], "2026-09")
        self.assertTrue(budget.settle(grant["reservation_id"], 3000, usage={"input_tokens": 1}))
        rows = self.fixture.conn.execute("SELECT kind, action, value, month FROM events ORDER BY rowid").fetchall()
        self.assertEqual([(row["kind"], row["action"], row["value"], row["month"]) for row in rows], [("reservation", "settled", 0, "2026-09"), ("cost", "settled", 3000, "2026-09")])
        with self.assertRaises(hp.BudgetExhausted):
            budget.reserve(operation_id="op-1", purpose="bundle", target_digest="sha256:" + "a" * 64, reserve_micro_usd=5000)

    def test_budget_cap_retains_reservations_and_reports_availability(self):
        self.fixture.cap = 4000
        budget = hp.Budget(self.client)
        with self.assertRaises(hp.BudgetExhausted):
            budget.reserve(operation_id="op-over", purpose="bundle", target_digest="x", reserve_micro_usd=9000)
        budget.reserve(operation_id="op-2", purpose="bundle", target_digest="x", reserve_micro_usd=3000)
        reserved = self.fixture.conn.execute("SELECT value, action FROM events WHERE kind='reservation'").fetchone()
        self.assertEqual((reserved["value"], reserved["action"]), (3000, "reserved"))
        self.assertTrue(budget.allows_new_work())
        with self.assertRaises(hp.BudgetExhausted):
            budget.reserve(operation_id="op-3", purpose="bundle", target_digest="x", reserve_micro_usd=3000)
        self.fixture.cap = 0
        self.assertFalse(budget.allows_new_work())

    def test_recalls_use_the_snapshot_envelope(self):
        status, current = self.client.recalls()
        self.assertEqual(status, 200)
        snapshot, etag = current["snapshot"], current["etag"]
        self.assertEqual(snapshot["sequence"], 1)
        entry = {"note_id": "service/acme", "revision": "sha256:" + "a" * 64}
        status, body = self.client.put_recalls({**snapshot, "sequence": 2, "entries": [entry]}, expected_etag=etag)
        self.assertEqual(status, 200)
        self.assertEqual(self.client.recalls()[1]["snapshot"]["entries"], [entry])
        status, _ = self.client.put_recalls({**snapshot, "sequence": 3, "entries": []}, expected_etag=body.get("etag"))
        self.assertEqual(status, 409, "a projection never drops a recall")
        status, _ = self.client.put_recalls({**snapshot, "sequence": 3, "entries": [entry]}, expected_etag="stale")
        self.assertEqual(status, 409)


# --------------------------------------------------------------------------- run_pipeline


class StubModules:
    """Stands in for the code-root builder/gate/publisher (no real build needed)."""

    def __init__(self, fixture: HubFixture, commons: Path, *, deliver: bool = True, fail_gate: bool = False):
        self.fixture = fixture
        self.commons = commons
        self.deliver = deliver
        self.fail_gate = fail_gate
        self.build_calls = 0
        self.publish_calls = 0

    def build(self, out, *, content_root=None, audience=None, withheld=frozenset()):
        self.build_calls += 1
        self.withheld = set(withheld)
        head = hp.CommonsRepo(content_root).head()
        Path(out).mkdir(parents=True, exist_ok=True)
        return {"schema_version": 2, "audience": audience, "commit": head}

    def check_shared(self, out, content_root, audience, *, withheld=frozenset()):
        self.gate_withheld = set(withheld)
        if self.fail_gate:
            raise SystemExit("gate")
        return {"ok": True}

    def publish(self, out, origin, token, audience, *, dry_run=False):
        self.publish_calls += 1
        repo = hp.CommonsRepo(self.commons)
        commit = repo.head()
        if self.deliver and self.fixture.deliver:
            shipped = [entry for entry in hp.note_entries(repo) if (entry["note_id"], entry["revision"]) not in self.withheld]
            self.fixture.register(commit, [note_index_entry(entry) for entry in shipped])
        self.fixture.current = {"commit": commit, "inventory_digest": "sha256:" + "0" * 64, "sequence": 1, "etag": "etag-" + commit[:8]}
        return {"release": commit, "commit": commit, "inventory_digest": "sha256:" + "0" * 64, "sequence": 1, "promoted": True, "confirmed_via": "admin/promote"}


class RunPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.fixture = HubFixture()
        self.addCleanup(self.fixture.close)
        self.domains = {"schema_version": 1, "services": {"acme": {"hosts": ["docs.acme.com"], "status": "verified"}}}
        self.commons = make_commons(self.tmp, domains=self.domains)
        self.jev = self.tmp / "jev"
        self.jev.mkdir(parents=True, exist_ok=True)
        self.fetch_patch = patch.object(hp.doc_cache, "fetch_document", lambda url, hosts, prefixes: "Acme deploy docs. " + "x" * 40)
        self.fetch_patch.start()
        self.clean_patch = patch.object(hp, "_clean_origin", test_origin)
        self.clean_patch.start()
        env = {"HUB_WORKFLOW_TOKEN": "workflow-token", "PORTWRIGHT_JEV_FIXTURES": str(self.jev)}
        self.env_patch = patch.dict(os.environ, env, clear=False)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.clean_patch.stop()
        self.fetch_patch.stop()

    def stub(self, **kwargs) -> StubModules:
        stub = StubModules(self.fixture, self.commons, **kwargs)
        modules = {
            "build_skill_bundles": SimpleNamespace(build=stub.build),
            "jev_bundle_gate": SimpleNamespace(check_shared=stub.check_shared),
            "publish_release": SimpleNamespace(publish=stub.publish),
        }
        self.module_patch = patch.object(hp, "_code_module", side_effect=lambda code_root, name: modules[name])
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        return stub

    def test_pending_intake_is_gated_committed_published_and_acked(self):
        stub = self.stub()
        intake_id = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["errors"], [])
        self.assertEqual(self.fixture.intake_state(intake_id), "published")
        self.assertIn(intake_id, report["acked"])
        note_path = self.commons / "services" / "acme.md"
        self.assertTrue(note_path.is_file())
        text = note_path.read_text(encoding="utf-8")
        self.assertIn("grade: trial", text)
        self.assertIn("intake_id: " + intake_id, text)
        log = run_git("log", "--format=%B", "-1", cwd=self.commons).stdout
        self.assertIn("Intake-ID: " + intake_id, log)
        self.assertEqual(self.fixture.current["commit"], report["commit"])
        self.assertFalse((self.commons / "receipts").exists())
        proof_line = [line for line in log.splitlines() if line.startswith("Gate-Proof:")][0]
        proof = json.loads(proof_line.split(":", 1)[1].strip())
        revision = report["intakes"][intake_id]["revision"]
        recomputed = hp.gate_proof("service/acme", revision, proof["doc_digest"], proof["answers"], hp.gate_policy(self.domains, "acme"))
        self.assertEqual(proof, recomputed)
        self.assertIn(f'gate_digest: "{recomputed["gate_digest"]}"', text)
        self.assertEqual(proof["answers"]["supported"], 0.95)
        self.assertEqual(stub.withheld, set())

    def test_revoked_token_rejects_without_any_model_call(self):
        self.stub()
        intake_id = self.fixture.add_intake(body=procedure_note(), token_eligible=False)
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(intake_id), "rejected")
        self.assertEqual(self.fixture.intake_row(intake_id)["reason_code"], "token_revoked")
        self.assertEqual(self.fixture.intake_row(intake_id)["body"], None)
        self.assertIn({"intake_id": intake_id, "reason_code": "token_revoked"}, report["rejected"])
        self.assertEqual(self.fixture.event_count("reservation"), 0)
        self.assertFalse((self.commons / "services" / "acme.md").exists())

    def test_expired_and_superseded_are_finished(self):
        self.stub()
        old = int(time.time()) - 40 * 86400
        expired = self.fixture.add_intake(body=procedure_note(), created=old)
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="stable",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        superseded = self.fixture.add_intake(
            kind="procedure",
            service_id="acme",
            body=procedure_note(),
            state="committed",
            target_note_id="service/acme",
            revision="sha256:" + "a" * 64,
            note_id="service/acme",
            commit_sha="0" * 40,
        )
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(expired), "rejected")
        self.assertEqual(self.fixture.intake_state(superseded), "rejected")
        self.assertEqual(self.fixture.intake_row(superseded)["reason_code"], "superseded")
        self.assertEqual(report["errors"], [])
        self.assertEqual(seeded["note_id"], "service/acme")

    def test_recall_only_records_marker_and_projects_without_build(self):
        stub = self.stub()
        note_path = self.commons / "services" / "acme.md"
        note_path.parent.mkdir(exist_ok=True)
        revision = "sha256:" + "a" * 64
        note_path.write_text(
            "---\nid: acme\nstatus: active\ndistributable: true\ngrade: trial\nrevision: " + revision + "\ngate_digest: sha256:" + "b" * 64 + "\ndoc_url: https://docs.acme.com/deploy\n---\nbody\n",
            encoding="utf-8",
        )
        run_git("add", "-A", cwd=self.commons)
        run_git("commit", "-q", "-m", "note", cwd=self.commons)
        run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.commons)
        self.fixture.add_event(kind="report", note_id="service/acme", revision=revision, lineage_id="l1")
        self.fixture.add_event(kind="report", note_id="service/acme", revision=revision, lineage_id="l2")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public", recall_only=True)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["recalled"][0]["reason_code"], "distinct_reports")
        self.assertTrue(any(self.commons.glob("recalls/services/acme/*.json")))
        self.assertEqual(self.fixture.recalls["entries"], [{"note_id": "service/acme", "revision": revision}])
        self.assertEqual(stub.build_calls, 0)
        self.assertEqual(self.fixture.event_count("reservation"), 0)

    def mark_published(self, commit: str | None = None) -> None:
        """Simulate a delivered release so promotions have a published revision."""
        repo = hp.CommonsRepo(self.commons)
        head = repo.head()
        self.fixture.register(head, [note_index_entry(entry) for entry in hp.note_entries(repo)])
        self.fixture.current = {"commit": head, "inventory_digest": "sha256:" + "0" * 64, "sequence": 1, "etag": "etag-" + head[:8]}

    def test_promotion_requires_review_and_writes_grade_change(self):
        stub = self.stub()
        note_path = self.commons / "services" / "acme.md"
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="trial",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        self.mark_published()
        revision = seeded["revision"]
        gate_digest = seeded["gate_digest"]
        confirms = []
        for index in range(3):
            confirms.append(self.fixture.add_event(kind="confirm", note_id="service/acme", revision=revision, lineage_id=f"l{index}", payload={"validation": "passed", "gate_digest": gate_digest, "payload_digest": f"d{index}"}))
        pending = hp.evaluate_revision(
            {"note_id": "service/acme", "revision": revision, "grade": "trial", "gate_digest": gate_digest},
            [dict(row) for row in self._event_items()],
            int(time.time()),
        )
        self.assertTrue(pending["pending_review"])
        self.fixture.add_event(kind="ack", note_id="service/acme", revision=revision, lineage_id="operator", action="review_promote", payload={"revision": revision, "gate_digest": gate_digest, "events_digest": pending["events_digest"]})
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["promoted"][0]["note_id"], "service/acme")
        self.assertEqual(report["acked"], [], "a promotion publishes without acking an intake")
        self.assertTrue(stub.publish_calls >= 1, "a promotion-only run must still publish")
        self.assertIn("grade: stable", note_path.read_text(encoding="utf-8"))
        log = run_git("log", "--format=%B", "-1", cwd=self.commons).stdout
        self.assertIn("Promote: service/acme " + revision, log)
        self.assertEqual(len(confirms), 3)

    def _event_items(self) -> list[dict]:
        rows = self.fixture.conn.execute("SELECT * FROM events").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"] or "{}")
            item["eligible"] = bool(item["eligible"])
            items.append(item)
        return items

    def test_pending_confirmation_is_validated_and_cas_bound(self):
        self.stub()
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="trial",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        revision = seeded["revision"]
        gate_digest = seeded["gate_digest"]
        write_jev_fixture(self.jev, "hub-confirm-" + revision[7:19])
        self.fixture.add_event(
            kind="confirm",
            note_id="service/acme",
            revision=revision,
            lineage_id="l1",
            payload={
                "validation": "pending",
                "payload_digest": "d1",
                "success_evidence": {"action": "ran acme deploy --prod", "outcome": "health check returned 200"},
            },
        )
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["errors"], [])
        payload = json.loads(self.fixture.conn.execute("SELECT payload FROM events WHERE kind='confirm'").fetchone()["payload"])
        self.assertEqual(payload["validation"], "passed")
        self.assertEqual(payload["gate_digest"], gate_digest)

    def test_budget_exhaustion_blocks_promotion_and_publish(self):
        stub = self.stub()
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="trial",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        self.mark_published()
        revision, gate_digest = seeded["revision"], seeded["gate_digest"]
        for index in range(3):
            self.fixture.add_event(
                kind="confirm",
                note_id="service/acme",
                revision=revision,
                lineage_id=f"l{index}",
                payload={"validation": "passed", "gate_digest": gate_digest, "payload_digest": f"d{index}"},
            )
        review_digest = hp.evaluate_revision(
            {"note_id": "service/acme", "revision": revision, "grade": "trial", "gate_digest": gate_digest, "published": True},
            self._event_items(),
            int(time.time()),
        )["events_digest"]
        self.fixture.add_event(
            kind="ack",
            note_id="service/acme",
            revision=revision,
            lineage_id="operator",
            action="review_promote",
            payload={"revision": revision, "gate_digest": gate_digest, "events_digest": review_digest},
        )
        self.fixture.cap = 0
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertIn({"note_id": "service/acme", "reason_code": "budget_exhausted"}, report["skipped"])
        self.assertEqual(report["promoted"], [])
        self.assertIn("grade: trial", (self.commons / "services" / "acme.md").read_text(encoding="utf-8"))
        self.assertEqual(stub.publish_calls, 0)

    def test_exhausted_budget_blocks_a_committed_retry(self):
        self.stub()
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="trial",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        intake_id = self.fixture.add_intake(
            state="committed",
            note_id="service/acme",
            revision=seeded["revision"],
            gate_digest=seeded["gate_digest"],
            commit_sha=hp.CommonsRepo(self.commons).head(),
            intake_id=seeded["intake_id"],
        )
        self.fixture.cap = 0
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["errors"][0]["reason_code"], "budget_exhausted")
        self.assertEqual(report["acked"], [])
        self.assertEqual(self.fixture.intake_state(intake_id), "committed")

    def test_exhausted_budget_still_recalls_and_projects(self):
        self.stub()
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="trial",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        revision = seeded["revision"]
        self.fixture.add_event(kind="report", note_id="service/acme", revision=revision, lineage_id="r1")
        self.fixture.add_event(kind="report", note_id="service/acme", revision=revision, lineage_id="r2")
        self.fixture.cap = 0
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["recalled"][0]["reason_code"], "distinct_reports")
        self.assertTrue(any(self.commons.glob("recalls/services/acme/*.json")))
        self.assertEqual(self.fixture.recalls["entries"], [{"note_id": "service/acme", "revision": revision}])

    def test_second_submission_for_the_same_note_in_one_batch_is_held(self):
        self.stub()
        first = self.fixture.add_intake(body=procedure_note())
        second = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{first}")
        write_jev_fixture(self.jev, f"hub-gate-{second}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        states = {first: self.fixture.intake_state(first), second: self.fixture.intake_state(second)}
        self.assertEqual(sorted(states.values()), ["held", "published"], "exactly one wins its generated path")
        released = [intake_id for intake_id, state in states.items() if state == "published"][0]
        held = [intake_id for intake_id, state in states.items() if state == "held"][0]
        # Per-item commits: the second sees the first note and must name its revision.
        self.assertEqual(self.fixture.intake_row(held)["reason_code"], "existing_note_requires_revision")
        self.assertEqual(report["acked"], [released])
        self.assertEqual(len(list(self.commons.glob("services/*.md"))), 1)

    def test_pending_secret_rejects_only_that_intake(self):
        stub = self.stub()
        seeded = seed_note(
            self.commons,
            relative="services/other.md",
            text=procedure_note("other"),
            grade="trial",
            doc_url="https://docs.acme.com/deploy",
            service_id="other",
            kind="procedure",
            domains=self.domains,
        )
        self.fixture.add_event(kind="report", note_id="service/other", revision=seeded["revision"], lineage_id="r1")
        self.fixture.add_event(kind="report", note_id="service/other", revision=seeded["revision"], lineage_id="r2")
        secret = "ghp_" + "C" * 24
        poisoned = self.fixture.add_intake(body=procedure_note(), evidence={"action": f"used token {secret}", "outcome": "deploy completed"})
        good = self.fixture.add_intake(kind="lesson", body=self.today_lesson())
        write_jev_fixture(self.jev, f"hub-gate-{poisoned}")
        write_jev_fixture(self.jev, f"hub-gate-{good}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(poisoned), "rejected")
        self.assertEqual(self.fixture.intake_row(poisoned)["reason_code"], "secret")
        self.assertIsNone(report["publish_blocked"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(self.fixture.intake_state(good), "published", "a rejected submission ends only itself")
        self.assertEqual(stub.publish_calls, 1)
        self.assertTrue(any(self.commons.glob("recalls/services/other/*.json")))
        self.assertEqual(len(self.fixture.recalls["entries"]), 1)

    def test_secret_in_committed_bytes_halts_the_whole_publish(self):
        stub = self.stub()
        seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(extra="notes: \"token " + "ghp_" + "C" * 24 + "\""),
            grade="trial",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        good = self.fixture.add_intake(kind="lesson", body=self.today_lesson())
        write_jev_fixture(self.jev, f"hub-gate-{good}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["publish_blocked"], "secret_in_delivery")
        self.assertEqual(stub.publish_calls, 0)
        self.assertEqual(self.fixture.intake_state(good), "committed")
        self.assertEqual(report["acked"], [])

    def test_readback_rejects_tampered_bytes_and_does_not_ack(self):
        self.stub()
        intake_id = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        self.fixture.tamper_sync = True
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["acked"], [])
        self.assertEqual(self.fixture.intake_state(intake_id), "committed")
        self.assertEqual(report["errors"][-1]["reason_code"], "not_delivered")

    def test_held_revoked_item_is_rejected_without_the_retry_flag(self):
        self.stub()
        intake_id = self.fixture.add_intake(body=procedure_note(), state="held", token_eligible=False)
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(intake_id), "rejected")
        self.assertEqual(self.fixture.intake_row(intake_id)["reason_code"], "token_revoked")
        self.assertIn({"intake_id": intake_id, "reason_code": "token_revoked"}, report["rejected"])

    def test_unknown_eligibility_is_never_gated(self):
        self.stub()
        intake_id = self.fixture.add_intake(body=procedure_note())
        self.fixture.omit_eligibility.add(intake_id)
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(intake_id), "pending")
        self.assertEqual(report["intakes"], {})
        self.assertIn({"intake_id": intake_id, "reason_code": "eligibility_unknown"}, report["skipped"])
        self.assertFalse((self.commons / "services" / "acme.md").exists())

    def test_recall_projection_survives_an_analysis_failure(self):
        self.stub()
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="trial",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        revision = seeded["revision"]
        marker = self.commons / "recalls" / "services" / "acme" / f"{revision[7:]}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"schema_version": 1, "note_id": "service/acme", "revision": revision, "reason_code": "distinct_reports", "created": 1, "event_ids": [], "operator_review_event_id": None}), encoding="utf-8")
        run_git("add", "-A", cwd=self.commons)
        run_git("commit", "-q", "-m", "recall", cwd=self.commons)
        run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.commons)
        self.fixture.fail_events = True
        with self.assertRaises(hp.PipelineError):
            hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.recalls["entries"], [{"note_id": "service/acme", "revision": revision}])

    def test_gate_holds_a_note_missing_required_schema_or_root_cause(self):
        incomplete = procedure_note().replace("endpoint:\n  type: cli\n  server: \"acme\"\n", "")
        intake_id = self.fixture.add_intake(body=incomplete)
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_row(intake_id)["reason_code"], "note_mismatch")
        self.assertEqual(report["intakes"][intake_id]["state"], "held")
        lesson = lesson_note().replace("## 진짜 원인", "## 원인")
        lesson_id = self.fixture.add_intake(kind="lesson", service_id="acme", body=lesson, doc_url="https://docs.acme.com/deploy")
        write_jev_fixture(self.jev, f"hub-gate-{lesson_id}")
        report2 = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_row(lesson_id)["reason_code"], "note_mismatch")
        self.assertEqual(report2["intakes"][lesson_id]["state"], "held")

    def test_gate_rejects_a_secret_in_an_unexpected_field(self):
        intake_id = self.fixture.add_intake(body=procedure_note())
        self.fixture.conn.execute("UPDATE intake SET success_evidence=? WHERE id=?", (json.dumps({"action": "ok fine", "outcome": "ghp_" + "D" * 24}), intake_id))
        self.fixture.conn.commit()
        hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(intake_id), "rejected")
        self.assertEqual(self.fixture.intake_row(intake_id)["reason_code"], "secret")

    def test_publish_resume_recovers_from_trailers_and_acks(self):
        stub = self.stub(deliver=False)
        intake_id = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        first = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertTrue(first["errors"])
        self.assertEqual(self.fixture.intake_state(intake_id), "committed")
        self.assertEqual(stub.publish_calls, 1)
        self.fixture.deliver = True
        stub.deliver = True
        second = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(second["errors"], [])
        self.assertEqual(self.fixture.intake_state(intake_id), "published")
        self.assertIn(intake_id, second["acked"])

    def test_gate_failure_blocks_ack(self):
        self.stub(fail_gate=True)
        intake_id = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(intake_id), "committed")
        self.assertNotIn(intake_id, report["acked"])
        self.assertEqual(report["errors"][0]["stage"], "bundle_gate")

    def test_held_intake_is_not_retried_without_flag(self):
        self.stub()
        intake_id = self.fixture.add_intake(body=procedure_note(), doc_url="https://evil.example.com/deploy", state="held")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public", retry_held=False)
        self.assertEqual(self.fixture.intake_state(intake_id), "held")
        self.assertEqual(report["intakes"], {})
        retried = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public", retry_held=True)
        self.assertEqual(self.fixture.intake_row(intake_id)["reason_code"], "off_domain")
        self.assertIn(intake_id, retried["intakes"])

    def test_stable_update_uses_trial_sibling_and_promotion_moves_it(self):
        stub = self.stub()
        path = self.commons / "services" / "acme.md"
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="stable",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        revision = seeded["revision"]
        body = procedure_note().replace("deploy --prod", "deploy --prod --region eu")
        expected = hp.hub_contracts.note_revision("procedure", "acme", body, "https://docs.acme.com/deploy")
        intake_id = self.fixture.add_intake(
            body=body,
            target_note_id="service/acme",
            expected_revision=revision,
        )
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["errors"], [])
        staged = self.commons / "trial" / "services" / "acme.md"
        self.assertTrue(staged.is_file(), "stable update must stage the trial sibling")
        self.assertIn("grade: trial", staged.read_text(encoding="utf-8"))
        self.assertIn("grade: stable", path.read_text(encoding="utf-8"))
        self.assertNotEqual(expected, revision)
        self.assertEqual(self.fixture.intake_state(intake_id), "published")
        delivered = {(note["note_id"], note["revision"]): note["grade"] for note in self.fixture.delivered[self.fixture.current["commit"]]}
        self.assertEqual(delivered, {("service/acme", revision): "stable", ("service/acme", expected): "trial"})
        self.assertEqual(stub.withheld, set(), "both siblings carry their own gate proof")

    def test_stale_expected_revision_holds(self):
        self.stub()
        seeded = seed_note(
            self.commons,
            relative="services/acme.md",
            text=procedure_note(),
            grade="stable",
            doc_url="https://docs.acme.com/deploy",
            service_id="acme",
            kind="procedure",
            domains=self.domains,
        )
        intake_id = self.fixture.add_intake(
            body=procedure_note(),
            target_note_id="service/acme",
            expected_revision="sha256:" + "e" * 64,
        )
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(intake_id), "held")
        self.assertEqual(self.fixture.intake_row(intake_id)["reason_code"], "revision_conflict")
        self.assertEqual(report["errors"], [])
        self.assertNotEqual(seeded["revision"], "sha256:" + "e" * 64)

    # -- review regressions (2026-09-25 H3 review scenarios) -------------------------

    def seed(self, relative: str, text: str, grade: str, *, service_id: str = "acme", domains: dict | None = None, **kwargs) -> dict:
        return seed_note(
            self.commons,
            relative=relative,
            text=text,
            grade=grade,
            doc_url=f"https://docs.{service_id}.com/deploy",
            service_id=service_id,
            kind="procedure",
            domains=domains or self.domains,
            **kwargs,
        )

    def today_lesson(self) -> str:
        """A lesson dated like the server-generated failures/<created day>-... path."""
        return lesson_note(date=datetime.fromtimestamp(self.fixture.created, timezone.utc).date().isoformat())

    def delivered_keys(self) -> set[tuple[str, str]]:
        return {(note["note_id"], note["revision"]) for note in self.fixture.delivered.get(self.fixture.current["commit"], [])}

    def pad_history(self, count: int) -> None:
        """`count` empty commits on the current branch, pushed (one fast-import process)."""
        branch = run_git("symbolic-ref", "HEAD", cwd=self.commons).stdout.strip()
        head = run_git("rev-parse", "HEAD", cwd=self.commons).stdout.strip()
        stream = "".join(
            f"commit {branch}\ncommitter fixture <fixture@example.com> {1_790_000_000 + n} +0000\ndata 3\npad\n" + (f"from {head}\n" if n == 0 else "") + "\n"
            for n in range(count)
        )
        subprocess.run(["git", "fast-import", "--quiet"], cwd=self.commons, input=stream.encode("utf-8"), check=True, capture_output=True)
        run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.commons)

    def model_calls(self) -> list[str]:
        """Every JevClient.ask made by the run (fixture judgments pass)."""
        from portwright import jev

        calls: list[str] = []

        def ask(client, fixture_id, state, questions):
            calls.append(fixture_id)
            return Judgment(status="ok", source="fixture", answers=passing_answers(), usage={})

        ask_patch = patch.object(jev.JevClient, "ask", ask)
        ask_patch.start()
        self.addCleanup(ask_patch.stop)
        return calls

    def test_adding_another_service_to_the_domain_cache_keeps_older_proofs_valid(self):
        stub = self.stub()
        seeded = self.seed("services/acme.md", procedure_note(), "stable")
        self.mark_published()
        write_domains(self.commons, {"schema_version": 1, "services": {**self.domains["services"], "beta": {"hosts": ["docs.beta.com"], "status": "verified"}}})
        intake_id = self.fixture.add_intake(service_id="beta", body=procedure_note("beta"), doc_url="https://docs.beta.com/deploy")
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["held"], [])
        self.assertEqual(self.fixture.intake_state(intake_id), "published")
        self.assertEqual(stub.withheld, set())
        self.assertIn(("service/acme", seeded["revision"]), self.delivered_keys())

    def test_changed_service_policy_withholds_only_that_services_revisions(self):
        stub = self.stub()
        domains = {"schema_version": 1, "services": {**self.domains["services"], "beta": {"hosts": ["docs.beta.com"], "status": "verified"}}}
        write_domains(self.commons, domains)
        acme = self.seed("services/acme.md", procedure_note(), "stable", domains=domains)
        sibling = self.seed("trial/services/acme.md", procedure_note().replace("deploy --prod", "deploy --prod --region eu"), "trial", domains=domains)
        beta = self.seed("services/beta.md", procedure_note("beta"), "stable", service_id="beta", domains=domains)
        self.mark_published()
        changed = {"schema_version": 1, "services": {**domains["services"], "acme": {"hosts": ["docs.acme.com", "developer.acme.com"], "status": "verified"}}}
        write_domains(self.commons, changed)
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        withheld = {("service/acme", acme["revision"]), ("service/acme", sibling["revision"])}
        self.assertEqual(report["errors"], [])
        self.assertEqual({(entry["note_id"], entry["revision"]) for entry in report["held"] if entry.get("reason_code") == "policy_changed"}, withheld)
        self.assertEqual(stub.withheld, withheld)
        self.assertEqual(stub.gate_withheld, withheld, "the bundle gate rebuilds with the same withheld set")
        self.assertEqual(self.delivered_keys(), {("service/beta", beta["revision"])}, "policy-invalid bytes stop being delivered")
        hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(stub.publish_calls, 1, "an unchanged withheld set does not republish")

    def test_gate_proof_older_than_three_hundred_commits_still_publishes(self):
        stub = self.stub()
        seeded = self.seed("services/acme.md", procedure_note(), "stable")
        self.pad_history(301)
        self.mark_published()
        intake_id = self.fixture.add_intake(body=procedure_note().replace("deploy --prod", "deploy --prod --region eu"), target_note_id="service/acme", expected_revision=seeded["revision"])
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["errors"], [])
        self.assertEqual(stub.withheld, set())
        self.assertEqual(self.fixture.intake_state(intake_id), "published")
        self.assertIn(("service/acme", seeded["revision"]), self.delivered_keys())

    def test_trial_sibling_is_reviewed_through_review_revision_and_promoted(self):
        self.stub()
        self.seed("services/acme.md", procedure_note(), "stable")
        sibling = self.seed("trial/services/acme.md", procedure_note().replace("deploy --prod", "deploy --prod --region eu"), "trial")
        self.mark_published()
        for index in range(3):
            self.fixture.add_event(kind="confirm", note_id="service/acme", revision=sibling["revision"], lineage_id=f"l{index}", payload={"validation": "passed", "gate_digest": sibling["gate_digest"], "payload_digest": f"d{index}"})
        with patch.dict(os.environ, {"HUB_OPERATOR_TOKEN": "operator-token"}, clear=False):
            summary = hp.review_revision(self.commons, ROOT, self.fixture.url, "public", "service/acme", sibling["revision"], "promote")
            self.assertTrue(summary["pending_review"])
            posted = hp.review_revision(self.commons, ROOT, self.fixture.url, "public", "service/acme", sibling["revision"], "promote", approve=True, expected_events_digest=summary["events_digest"])
            self.assertTrue(posted["posted"])
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(report["errors"], [])
        self.assertEqual([entry["revision"] for entry in report["promoted"]], [sibling["revision"]])
        self.assertFalse((self.commons / "trial" / "services" / "acme.md").exists())
        canon = (self.commons / "services" / "acme.md").read_text(encoding="utf-8")
        self.assertIn(f'revision: "{sibling["revision"]}"', canon)
        self.assertIn("grade: stable", canon)

    def test_all_replaced_revisions_and_trial_siblings_are_recallable(self):
        self.stub()
        replaced: set[tuple[str, str]] = set()
        for service in ("alpha", "beta", "gamma"):
            for index in range(3):
                seeded = self.seed(f"services/{service}.md", procedure_note(service).replace("배포 절차", f"배포 절차 {index}"), "trial", service_id=service)
                if index < 2:
                    replaced.add((seeded["note_id"], seeded["revision"]))
        self.seed("services/acme.md", procedure_note(), "stable")
        first_sibling = self.seed("trial/services/acme.md", procedure_note().replace("deploy --prod", "deploy --prod --v2"), "trial")
        self.seed("trial/services/acme.md", procedure_note().replace("deploy --prod", "deploy --prod --v3"), "trial")
        replaced.add((first_sibling["note_id"], first_sibling["revision"]))
        for note_id, revision in replaced:
            for lineage in ("r1", "r2"):
                self.fixture.add_event(kind="report", note_id=note_id, revision=revision, lineage_id=lineage)
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public", recall_only=True)
        self.assertEqual(report["errors"], [])
        self.assertEqual({(entry["note_id"], entry["revision"]) for entry in report["recalled"]}, replaced)
        self.assertEqual({(entry["note_id"], entry["revision"]) for entry in self.fixture.recalls["entries"]}, replaced)

    def test_confirm_with_an_identifier_is_rejected_and_erased_without_a_model_call(self):
        self.stub()
        calls = self.model_calls()
        seeded = self.seed("services/acme.md", procedure_note(), "trial")
        self.fixture.add_event(
            kind="confirm", note_id="service/acme", revision=seeded["revision"], lineage_id="l1",
            payload={"validation": "pending", "payload_digest": "d1", "success_evidence": {"action": "ran as john.doe@example.com on 10.0.0.5", "outcome": "deploy finished and health check passed"}},
        )
        hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        payload = json.loads(self.fixture.conn.execute("SELECT payload FROM events WHERE kind='confirm'").fetchone()["payload"])
        self.assertEqual(payload["validation"], "rejected")
        self.assertNotIn("success_evidence", payload)
        self.assertEqual(calls, [])

    def test_confirm_evidence_is_screened_and_the_document_masked_before_the_model(self):
        self.stub()
        from portwright import jev

        seen: list[str] = []

        def ask(client, fixture_id, state, questions):
            seen.append(state["official_document"])
            return Judgment(status="ok", source="fixture", answers=passing_answers(), usage={})

        seeded = self.seed("services/acme.md", procedure_note(), "trial")
        shaped = self.fixture.add_event(
            kind="confirm", note_id="service/acme", revision=seeded["revision"], lineage_id="l1",
            payload={"validation": "pending", "payload_digest": "d1", "success_evidence": {"action": "ran acme deploy --prod", "outcome": "health check returned 200", "extra": "x" * 5000}},
        )
        with patch.object(jev.JevClient, "ask", ask):
            hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
            self.assertEqual(seen, [], "a malformed confirmation never reaches the model")
            with patch.object(hp.doc_cache, "fetch_document", lambda url, hosts, prefixes: "Contact admin@corp.internal at 192.168.1.10 for deploys."):
                documented = self.fixture.add_event(
                    kind="confirm", note_id="service/acme", revision=seeded["revision"], lineage_id="l2",
                    payload={"validation": "pending", "payload_digest": "d2", "success_evidence": {"action": "ran acme deploy --prod", "outcome": "health check returned 200"}},
                )
                hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        states = {event_id: json.loads(self.fixture.conn.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()["payload"])["validation"]
                  for event_id in (shaped, documented)}
        self.assertEqual(states, {shaped: "held", documented: "passed"})
        # The official page reached the model only with its identifiers masked.
        self.assertEqual(seen, ["Contact [redacted] at [redacted] for deploys."])

    def test_intake_committed_to_git_but_not_recorded_is_recovered_without_regating(self):
        self.stub()
        intake_id = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        with patch.object(hp.HubClient, "mark_committed", side_effect=hp.PipelineError("lost")):
            with self.assertRaises(hp.PipelineError):
                hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(intake_id), "pending")
        self.assertIn("Intake-ID: " + intake_id, run_git("log", "--format=%B", "-1", cwd=self.commons).stdout)
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertNotIn(intake_id, report["intakes"], "a committed intake is never gated twice")
        self.assertEqual(report["errors"], [])
        self.assertEqual(self.fixture.intake_state(intake_id), "published")
        self.assertIn(intake_id, report["acked"])

    def test_pending_queue_is_read_twenty_per_run_and_committed_per_item(self):
        self.stub()
        ids = [self.fixture.add_intake(kind="lesson", body=self.today_lesson()) for _ in range(25)]
        for intake_id in ids:
            write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        before = hp.CommonsRepo(self.commons).head()
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(len(report["intakes"]), 20)
        self.assertEqual(sum(self.fixture.intake_state(intake_id) == "pending" for intake_id in ids), 5)
        messages = [message for message in run_git("log", "--format=%B%x1e", f"{before}..HEAD", cwd=self.commons).stdout.split("\x1e") if "Intake-ID:" in message]
        self.assertEqual(len(messages), 20)
        self.assertTrue(all(message.count("Intake-ID:") == 1 for message in messages))

    def test_revoked_token_does_not_reject_a_committed_intake(self):
        self.stub()
        seeded = self.seed("services/acme.md", procedure_note(), "trial")
        intake_id = self.fixture.add_intake(
            state="committed", note_id="service/acme", revision=seeded["revision"], gate_digest=seeded["gate_digest"],
            commit_sha=hp.CommonsRepo(self.commons).head(), intake_id=seeded["intake_id"], token_eligible=False,
        )
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(intake_id), "published")
        self.assertNotIn({"intake_id": intake_id, "reason_code": "token_revoked"}, report["rejected"])

    def test_recall_decisions_do_not_wait_for_intake_paging(self):
        self.stub()
        seeded = self.seed("services/acme.md", procedure_note(), "trial")
        self.fixture.add_event(kind="report", note_id="service/acme", revision=seeded["revision"], lineage_id="r1")
        self.fixture.add_event(kind="report", note_id="service/acme", revision=seeded["revision"], lineage_id="r2")
        self.fixture.fail_intake = True
        with self.assertRaises(hp.PipelineError):
            hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertTrue(any(self.commons.glob("recalls/services/acme/*.json")))
        self.assertEqual(self.fixture.recalls["entries"], [{"note_id": "service/acme", "revision": seeded["revision"]}])
        self.assertEqual(self.fixture.recalls["sequence"], 2, "the projection advanced through the {snapshot, expected_etag} envelope")

    def test_failed_settlement_blocks_all_publication_for_the_run(self):
        from portwright import jev

        stub = self.stub()
        first = self.fixture.add_intake(kind="lesson", body=self.today_lesson(), created=self.fixture.created)
        second = self.fixture.add_intake(kind="lesson", body=self.today_lesson(), created=self.fixture.created + 1)
        self.fixture.fail_settle_after = 1
        with patch.object(jev.JevClient, "from_env", lambda: FakeLiveTransport(usage={"input_tokens": 100, "output_tokens": 4})):
            report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(self.fixture.intake_state(first), "committed", "the settled note was committed but must not ship")
        self.assertNotEqual(self.fixture.intake_state(second), "committed")
        self.assertEqual(report["publish_blocked"], "settlement_failed")
        self.assertEqual(stub.publish_calls, 0)
        self.assertEqual(report["acked"], [])
        self.assertEqual(self.fixture.event_count("reservation"), 2, "both intakes reached a paid, reserved model call")
        retained = self.fixture.conn.execute("SELECT count(*) c FROM events WHERE kind='reservation' AND action='reserved'").fetchone()["c"]
        self.assertEqual(retained, 1, "the unsettled reservation stays reserved")

    def test_report_secrets_and_identifiers_are_erased_before_recall(self):
        self.stub()
        seeded = self.seed("services/acme.md", procedure_note(), "trial")
        cases = [
            ("ghp_" + "A" * 36, "a" * 64),
            ("person" + "@example.com", "b" * 64),
            ("Deployment returns an error.", "c" * 64),
        ]
        event_ids = [self.fixture.add_event(
            kind="report", note_id=seeded["note_id"], revision=seeded["revision"],
            lineage_id=f"report-{index}", payload={"reason": reason, "payload_digest": digest},
        ) for index, (reason, digest) in enumerate(cases)]
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public", recall_only=True)
        self.assertEqual(report["errors"], [])
        for index, event_id in enumerate(event_ids):
            payload = json.loads(self.fixture.conn.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()["payload"])
            self.assertEqual(payload.get("reason"), cases[index][0] if index == 2 else None)
            self.assertEqual(payload["payload_digest"], cases[index][1])
        self.assertEqual([entry["revision"] for entry in report["recalled"]], [seeded["revision"]])
        self.assertEqual(self.fixture.event_count("reservation"), 0)

    def test_ack_uses_each_intakes_committed_sha_after_later_commits(self):
        self.stub(deliver=False)
        ids = [self.fixture.add_intake(
            kind="lesson", body=self.today_lesson(), created=self.fixture.created + index,
        ) for index in range(2)]
        for intake_id in ids:
            write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        commits = [self.fixture.intake_row(intake_id)["commit_sha"] for intake_id in ids]
        self.assertNotEqual(commits[0], commits[1])
        self.module_patch.stop()
        self.stub()
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(set(report["acked"]), set(ids))
        self.assertEqual([self.fixture.intake_state(intake_id) for intake_id in ids], ["published", "published"])

    def test_recall_discovery_reads_beyond_fifty_report_pages(self):
        self.stub()
        seeded = self.seed("services/acme.md", procedure_note(), "trial")
        for index in range(5001):
            last = index >= 4999
            event_id = self.fixture.add_event(
                kind="report", note_id="service/acme",
                revision=seeded["revision"] if last else "sha256:" + "e" * 64,
                lineage_id=f"lineage-{index}" if last else "same-lineage",
            )
            self.fixture.conn.execute("UPDATE events SET created=? WHERE id=?", (self.fixture.created - 5001 + index, event_id))
        self.fixture.conn.commit()
        report = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public", recall_only=True)
        self.assertEqual(report["errors"], [])
        self.assertEqual([entry["revision"] for entry in report["recalled"]], [seeded["revision"]])


class ReviewRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.commons = make_commons(self.tmp)
        self.fixture = HubFixture()
        self.addCleanup(self.fixture.close)
        self.clean_patch = patch.object(hp, "_clean_origin", test_origin)
        self.clean_patch.start()
        self.addCleanup(self.clean_patch.stop)
        self.revision = "sha256:" + "a" * 64
        self.gate_digest = "sha256:" + "b" * 64
        path = self.commons / "services" / "acme.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "---\nid: acme\ndistributable: true\ngrade: trial\nrevision: " + self.revision + "\ngate_digest: " + self.gate_digest + "\ndoc_url: https://docs.acme.com/deploy\n---\nbody\n",
            encoding="utf-8",
        )
        run_git("add", "-A", cwd=self.commons)
        run_git("commit", "-q", "-m", "note", cwd=self.commons)

    def test_summary_then_approved_review_with_digest_cas(self):
        repo = hp.CommonsRepo(self.commons)
        head = repo.head()
        self.fixture.register(head, [note_index_entry(entry) for entry in hp.note_entries(repo)])
        self.fixture.current = {"commit": head, "inventory_digest": "sha256:" + "0" * 64, "sequence": 1, "etag": "e"}
        confirms = [self.fixture.add_event(kind="confirm", note_id="service/acme", revision=self.revision, lineage_id=f"l{n}", payload={"validation": "passed", "gate_digest": self.gate_digest, "payload_digest": f"d{n}"}) for n in range(3)]
        self.assertEqual(len(confirms), 3)
        with patch.dict(os.environ, {"HUB_OPERATOR_TOKEN": "operator-token"}, clear=False):
            summary = hp.review_revision(self.commons, ROOT, self.fixture.url, "public", "service/acme", self.revision, "promote", approve=False)
            self.assertTrue(summary["pending_review"])
            digest = summary["events_digest"]
            with self.assertRaises(hp.PipelineError):
                hp.review_revision(self.commons, ROOT, self.fixture.url, "public", "service/acme", self.revision, "promote", approve=True, expected_events_digest="sha256:" + "z" * 64)
            posted = hp.review_revision(self.commons, ROOT, self.fixture.url, "public", "service/acme", self.revision, "promote", approve=True, expected_events_digest=digest)
            self.assertTrue(posted["posted"])
        row = self.fixture.conn.execute("SELECT action FROM events WHERE kind='ack'").fetchone()
        self.assertEqual(row["action"], "review_promote")

    def test_operator_token_is_required_only_for_review(self):
        with patch.dict(os.environ, {"HUB_OPERATOR_TOKEN": ""}, clear=False):
            with self.assertRaises(hp.PipelineError):
                hp.review_revision(self.commons, ROOT, self.fixture.url, "public", "service/acme", self.revision, "recall", approve=True, expected_events_digest="x")


# --------------------------------------------------------------------------- migration


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.source = make_commons(
            self.tmp / "src",
            notes={
                "services/acme.md": (
                    "---\nid: acme\ndisplay_name: Acme\nversion_tag: \"v1\"\nlast_verified: \"2026-09-01\"\n"
                    "endpoint:\n  type: cli\n  server: \"acme\"\nhuman_steps:\n  - 없음\nagent_can:\n  - 직접 실행\n"
                    "status: active\ndistributable: true\nfreshness_evidence:\n  url: https://docs.acme.com/guide\n---\n"
                    "## 한 줄 요약\nAcme\n\n## 정답 절차\n1. `acme deploy`\n"
                ),
                "failures/2026-09-01-acme-lesson.md": lesson_note(),
                "services/private-helper.md": procedure_note("private-helper", extra="profile_ids:\n  - internal"),
            },
        )
        script = importlib.util.spec_from_file_location("parent_hub_cli", ROOT / "scripts" / "hub_pipeline.py")
        self.cli = importlib.util.module_from_spec(script)
        script.loader.exec_module(self.cli)

    def test_evidence_generator_skips_profile_and_fills_urls(self):
        entries = self.cli.migration_evidence(self.source)
        paths = [entry["source_path"] for entry in entries]
        self.assertIn("services/acme.md", paths)
        self.assertIn("failures/2026-09-01-acme-lesson.md", paths)
        self.assertNotIn("services/private-helper.md", paths)
        by_path = {entry["source_path"]: entry for entry in entries}
        self.assertEqual(by_path["services/acme.md"]["doc_url"], "https://docs.acme.com/guide")
        self.assertEqual(by_path["failures/2026-09-01-acme-lesson.md"]["doc_url"], "https://docs.acme.com/guide")
        for entry in entries:
            self.assertEqual(entry["success_evidence"], {"action": "", "outcome": ""})

    def test_untracked_and_private_notes_are_not_read(self):
        (self.source / "services" / "untracked.md").write_text(procedure_note("untracked"), encoding="utf-8")
        (self.source / "services" / "_private").mkdir(exist_ok=True)
        (self.source / "services" / "_private" / "secret.md").write_text(procedure_note("secret"), encoding="utf-8")
        paths = [entry["source_path"] for entry in self.cli.migration_evidence(self.source)]
        self.assertNotIn("services/untracked.md", paths)
        self.assertNotIn("services/_private/secret.md", paths)


class MigrateNotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.domains = {"schema_version": 1, "services": {"acme": {"hosts": ["docs.acme.com"], "status": "verified"}}}
        self.commons = make_commons(self.tmp, domains=self.domains)
        self.source = make_commons(self.tmp / "src", notes={"services/acme.md": procedure_note()})
        self.evidence = self.tmp / "evidence.json"
        self.evidence.write_text(
            json.dumps([{"source_path": "services/acme.md", "doc_url": "https://docs.acme.com/deploy", "success_evidence": {"action": "verifier ran the deploy", "outcome": "release completed"}}]),
            encoding="utf-8",
        )
        self.fixture = HubFixture()
        self.addCleanup(self.fixture.close)
        self.jev = self.tmp / "jev"
        self.jev.mkdir(parents=True, exist_ok=True)
        source_commit = run_git("rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.migration_intake_id = str(uuid.uuid5(hp.MIGRATION_NAMESPACE, f"{source_commit}:services/acme.md"))
        write_jev_fixture(self.jev, f"hub-gate-{self.migration_intake_id}")
        self.fetch_patch = patch.object(hp.doc_cache, "fetch_document", lambda url, hosts, prefixes: "Acme deploy docs. " + "x" * 40)
        self.fetch_patch.start()
        self.clean_patch = patch.object(hp, "_clean_origin", test_origin)
        self.clean_patch.start()
        self.env_patch = patch.dict(os.environ, {"HUB_WORKFLOW_TOKEN": "workflow-token", "PORTWRIGHT_JEV_FIXTURES": str(self.jev)}, clear=False)
        self.env_patch.start()
        stub = StubModules(self.fixture, self.commons)
        modules = {
            "build_skill_bundles": SimpleNamespace(build=stub.build),
            "jev_bundle_gate": SimpleNamespace(check_shared=stub.check_shared),
            "publish_release": SimpleNamespace(publish=stub.publish),
        }
        self.module_patch = patch.object(hp, "_code_module", side_effect=lambda code_root, name: modules[name])
        self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop()
        self.env_patch.stop()
        self.clean_patch.stop()
        self.fetch_patch.stop()

    def test_migration_commits_trial_with_source_trailers_and_rerun_is_idempotent(self):
        report = hp.migrate_notes(self.commons, ROOT, self.fixture.url, "public", self.source, self.evidence)
        self.assertTrue(self.commons.joinpath("services", "acme.md").is_file())
        text = self.commons.joinpath("services", "acme.md").read_text(encoding="utf-8")
        self.assertIn("grade: trial", text)
        log = run_git("log", "--format=%B", "-1", cwd=self.commons).stdout
        self.assertIn("Source-Path: services/acme.md", log)
        self.assertIn("Source-File-Digest: sha256:", log)
        self.assertIn("Intake-ID: ", log)
        self.assertTrue(report["commit"])
        second = hp.migrate_notes(self.commons, ROOT, self.fixture.url, "public", self.source, self.evidence)
        self.assertTrue(any(entry.get("reason_code") == "already_migrated" for entry in second["skipped"]))

    def test_tracked_template_outside_manifest_is_skipped_not_parsed(self):
        (self.source / "failures").mkdir(exist_ok=True)
        (self.source / "failures" / "_TEMPLATE.md").write_text("---\ndate: YYYY-MM-DD\n---\n", encoding="utf-8")
        commit_all(self.source, "add template")
        source_commit = hp.CommonsRepo(self.source).head()
        write_jev_fixture(self.jev, f"hub-gate-{uuid.uuid5(hp.MIGRATION_NAMESPACE, f'{source_commit}:services/acme.md')}")
        report = hp.migrate_notes(self.commons, ROOT, self.fixture.url, "public", self.source, self.evidence)
        self.assertTrue(self.commons.joinpath("services", "acme.md").is_file())
        self.assertIn({"source_path": "failures/_TEMPLATE.md", "reason_code": "not_in_manifest"}, report["skipped"])
        self.assertFalse(self.commons.joinpath("failures", "_TEMPLATE.md").exists())

    def test_live_migration_reserves_without_an_intake_row(self):
        from portwright import jev

        with patch.object(jev.JevClient, "from_env", lambda: FakeLiveTransport()):
            report = hp.migrate_notes(self.commons, ROOT, self.fixture.url, "public", self.source, self.evidence)
        self.assertEqual(report["held"], [])
        self.assertTrue(self.commons.joinpath("services", "acme.md").is_file())
        rows = self.fixture.conn.execute("SELECT intake_id, payload FROM events WHERE kind='reservation'").fetchall()
        self.assertEqual([(row["intake_id"], json.loads(row["payload"])["purpose"]) for row in rows], [(None, "bundle")])

    def test_missing_document_url_holds_without_publishing(self):
        missing = self.tmp / "missing.json"
        missing.write_text(json.dumps([{"source_path": "services/acme.md", "doc_url": "", "success_evidence": {"action": "", "outcome": ""}}]), encoding="utf-8")
        report = hp.migrate_notes(self.commons, ROOT, self.fixture.url, "public", self.source, missing)
        self.assertTrue(report["held"])
        self.assertFalse(self.commons.joinpath("services", "acme.md").exists())
        self.assertFalse(self.commons.joinpath("trial", "services", "acme.md").exists())

    def test_migration_settlement_failure_blocks_already_committed_publication(self):
        from portwright import jev

        seed_note(
            self.commons, relative="services/ready.md", text=procedure_note("ready"), grade="trial",
            doc_url="https://docs.acme.com/deploy", service_id="ready", kind="procedure", domains=self.domains,
        )
        self.fixture.fail_settle_after = 0
        with patch.object(jev.JevClient, "from_env", lambda: FakeLiveTransport()):
            report = hp.migrate_notes(self.commons, ROOT, self.fixture.url, "public", self.source, self.evidence)
        self.assertEqual(report["publish_blocked"], "settlement_failed")
        self.assertIsNone(report["published"])
        retained = self.fixture.conn.execute("SELECT count(*) c FROM events WHERE kind='reservation' AND action='reserved'").fetchone()["c"]
        self.assertEqual(retained, 1)

    def test_migration_preserves_lesson_identity_across_unrelated_source_commits(self):
        relative = "failures/2026-09-01-acme-lesson.md"
        (self.source / relative).parent.mkdir(exist_ok=True)
        (self.source / relative).write_text(lesson_note())
        commit_all(self.source, "add lesson")
        plan = json.loads(self.evidence.read_text())
        plan.append({**plan[0], "source_path": relative})
        self.evidence.write_text(json.dumps(plan))
        source_commit = hp.CommonsRepo(self.source).head()
        for path in ("services/acme.md", relative):
            intake_id = str(uuid.uuid5(hp.MIGRATION_NAMESPACE, f"{source_commit}:{path}"))
            write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        first = hp.migrate_notes(self.commons, ROOT, self.fixture.url, "public", self.source, self.evidence)
        self.assertEqual(first["held"], [])
        original = (self.commons / relative).read_bytes()
        (self.source / "unrelated.txt").write_text("unrelated")
        commit_all(self.source, "unrelated source change")
        with patch.object(hp.ModelGateway, "ask", side_effect=AssertionError("must not re-gate")):
            for _ in range(2):
                report = hp.migrate_notes(self.commons, ROOT, self.fixture.url, "public", self.source, self.evidence)
                self.assertEqual(report["held"], [])
        self.assertEqual((self.commons / relative).read_bytes(), original)
        self.assertEqual(hp.CommonsRepo(self.commons).list_paths("failures"), [relative])

class CliIntegrationTests(unittest.TestCase):
    """The real CLI + real builder/gate/publisher against the local Hub fixture.

    Only IO seams are configured: a trusted loopback TLS certificate and the
    official-document fetch (no external network). Product stages run for real.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        tls = make_self_signed(self.tmp)
        if tls is None:
            self.skipTest("openssl is required for the HTTPS fixture")
        self.fixture = HubFixture(tls=tls)
        self.addCleanup(self.fixture.close)
        self.domains = {"schema_version": 1, "services": {"acme": {"hosts": ["docs.acme.com"], "status": "verified"}}}
        self.commons = make_commons(self.tmp, domains=self.domains)
        self.jev = self.tmp / "jev"
        self.jev.mkdir(parents=True, exist_ok=True)
        self.fetch_patch = patch.object(hp.doc_cache, "fetch_document", lambda url, hosts, prefixes: "Acme deploy docs. " + "x" * 40)
        self.fetch_patch.start()
        self.addCleanup(self.fetch_patch.stop)
        self.env_patch = patch.dict(
            os.environ,
            {
                "HUB_WORKFLOW_TOKEN": "workflow-token",
                "HUB_ORIGIN": self.fixture.url,
                "PORTWRIGHT_JEV_FIXTURES": str(self.jev),
                "SSL_CERT_FILE": str(tls[0]),
            },
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        # The publisher may have been imported before SSL_CERT_FILE was set by
        # this test; give its real HTTPS transport this fixture's CA explicitly.
        import publish_release
        import urllib.request

        opener = urllib.request.build_opener(
            hp.doc_cache.NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(tls[0]))))
        transport_patch = patch.object(publish_release, "urlopen", opener.open)
        transport_patch.start()
        self.addCleanup(transport_patch.stop)
        os.environ.pop("PORTWRIGHT_JEV_RECORD", None)
        self.cli = importlib.util.spec_from_file_location("parent_hub_cli_mode", ROOT / "scripts" / "hub_pipeline.py")
        self.cli_module = importlib.util.module_from_spec(self.cli)
        self.cli.loader.exec_module(self.cli_module)

    def test_cli_publishes_with_the_real_builder_gate_and_publisher(self):
        import contextlib
        import io

        self.assertTrue(self.fixture.url.startswith("https://"))
        intake_id = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        argv = ["hub_pipeline.py", "--commons", str(self.commons), "--code-root", str(ROOT), "--audience", "public"]
        buffer = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(buffer):
            code = self.cli_module.main()
        output = buffer.getvalue()
        self.assertEqual(code, 0, output)
        report = json.loads(output.strip().splitlines()[-1])
        self.assertEqual(report["errors"], [], output)
        self.assertEqual(report["acked"], [intake_id], output)
        self.assertEqual(self.fixture.intake_state(intake_id), "published")
        # the real builder shipped the note bytes and the real publisher promoted them
        self.assertTrue(self.fixture.objects, "the real publisher uploaded objects")
        index = json.loads(self.fixture.objects[(report["commit"], "note-index.json")].decode("utf-8"))
        self.assertEqual([entry["note_id"] for entry in index["notes"]], ["service/acme"])
        shipped = self.fixture.objects[(report["commit"], index["notes"][0]["path"])]
        self.assertEqual(shipped, (self.commons / "services" / "acme.md").read_bytes())
        self.assertEqual(self.fixture.recalls["entries"], [])

    def test_policy_only_change_can_publish_an_empty_immutable_release_once(self):
        intake_id = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{intake_id}")
        first = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
        self.assertEqual(first["errors"], [])
        old_commit = self.fixture.current["commit"]
        old_objects = dict(self.fixture.objects)
        pricing_path = self.tmp / "changed-pricing.json"
        pricing = json.loads(hp.PRICING_PATH.read_text())
        pricing["note"] += " New verification."
        pricing_path.write_text(json.dumps(pricing))
        with patch.object(hp, "PRICING_PATH", pricing_path):
            changed = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
            self.assertEqual(changed["errors"], [])
            new_commit = self.fixture.current["commit"]
            self.assertNotEqual(new_commit, old_commit)
            index = json.loads(self.fixture.objects[(new_commit, "note-index.json")])
            self.assertEqual(index["notes"], [])
            rerun = hp.run_pipeline(self.commons, ROOT, self.fixture.url, "public")
            self.assertEqual(rerun["commit"], new_commit)
            self.assertIsNone(rerun["published"])
        self.assertTrue(all(self.fixture.objects[key] == value for key, value in old_objects.items()))


class LifecycleProgressionTests(CliIntegrationTests):
    """Real builder/gate/publisher over HTTPS: trial -> stable -> sibling -> promote -> recall."""

    def setUp(self):
        super().setUp()
        self.delivered = {}

    def _events(self, note_id: str, revision: str) -> list[dict]:
        rows = self.fixture.conn.execute("SELECT * FROM events WHERE note_id=? AND revision=?", (note_id, revision)).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload"] or "{}"), "eligible": bool(row["eligible"])} for row in rows]

    def _review(self, note_id: str, revision: str, gate_digest: str, decision: str) -> str:
        evaluation = hp.evaluate_revision(
            {"note_id": note_id, "revision": revision, "grade": "trial", "gate_digest": gate_digest, "published": True},
            self._events(note_id, revision),
            int(time.time()),
        )
        client = hp.HubClient(test_origin(self.fixture.url), "workflow-token")
        status, _ = client.review(
            note_id=note_id,
            revision=revision,
            decision=decision,
            gate_digest=gate_digest,
            events_digest=evaluation["events_digest"],
            request_id=str(uuid.uuid4()),
        )
        self.assertEqual(status, 200)
        return evaluation["events_digest"]

    def _run(self) -> dict:
        import contextlib
        import io

        argv = ["hub_pipeline.py", "--commons", str(self.commons), "--code-root", str(ROOT), "--audience", "public"]
        buffer = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(buffer):
            code = self.cli_module.main()
        self.assertEqual(code, 0, buffer.getvalue())
        return json.loads(buffer.getvalue().strip().splitlines()[-1])

    def _delivered(self) -> dict[tuple[str, str], str]:
        repo = hp.CommonsRepo(self.commons)
        status, body = hp.HubClient(test_origin(self.fixture.url), "workflow-token").sync(include_trial=True)
        self.assertEqual(status, 200)
        return {(note["note_id"], note["revision"]): note["grade"] for note in body["notes"]}

    def test_full_progression_with_budget_exhausted_recall_and_idempotent_rerun(self):
        # 1. initial submission becomes a published trial
        first = self.fixture.add_intake(body=procedure_note())
        write_jev_fixture(self.jev, f"hub-gate-{first}")
        report = self._run()
        self.assertEqual(report["errors"], [], report)
        trial_revision = report["intakes"][first]["revision"]
        gate_digest = report["intakes"][first]["gate_digest"]
        self.assertEqual(self._delivered()[("service/acme", trial_revision)], "trial")

        # 2. three distinct lineages + operator review promote it to stable
        for index in range(3):
            self.fixture.add_event(
                kind="confirm",
                note_id="service/acme",
                revision=trial_revision,
                lineage_id=f"c{index}",
                payload={"validation": "passed", "gate_digest": gate_digest, "payload_digest": f"d{index}"},
            )
        digest = self._review("service/acme", trial_revision, gate_digest, "promote")
        self.assertTrue(digest)
        promoted = self._run()
        self.assertEqual(promoted["errors"], [], promoted)
        self.assertEqual(promoted["promoted"][0]["note_id"], "service/acme")
        self.assertEqual(self._delivered()[("service/acme", trial_revision)], "stable")
        self.assertIn("grade: stable", (self.commons / "services" / "acme.md").read_text(encoding="utf-8"))

        # 3. a new revision of the stable note stages the trial sibling
        new_body = procedure_note().replace("deploy --prod", "deploy --prod --region eu")
        update = self.fixture.add_intake(body=new_body, target_note_id="service/acme", expected_revision=trial_revision)
        write_jev_fixture(self.jev, f"hub-gate-{update}")
        update_report = self._run()
        self.assertEqual(update_report["errors"], [], update_report)
        new_revision = update_report["intakes"][update]["revision"]
        self.assertNotEqual(new_revision, trial_revision)
        self.assertTrue((self.commons / "trial" / "services" / "acme.md").is_file())
        delivered = self._delivered()
        self.assertEqual(delivered[("service/acme", trial_revision)], "stable")
        self.assertEqual(delivered[("service/acme", new_revision)], "trial")

        # 4. the sibling is promoted too: one commit moves it to the canon path
        new_gate = update_report["intakes"][update]["gate_digest"]
        for index in range(3):
            self.fixture.add_event(
                kind="confirm",
                note_id="service/acme",
                revision=new_revision,
                lineage_id=f"s{index}",
                payload={"validation": "passed", "gate_digest": new_gate, "payload_digest": f"e{index}"},
            )
        self.assertTrue(self._review("service/acme", new_revision, new_gate, "promote"))
        moved = self._run()
        self.assertEqual(moved["errors"], [], moved)
        self.assertFalse((self.commons / "trial" / "services" / "acme.md").exists())
        self.assertIn("grade: stable", (self.commons / "services" / "acme.md").read_text(encoding="utf-8"))
        delivered = self._delivered()
        self.assertEqual(delivered[("service/acme", new_revision)], "stable")
        self.assertNotIn(("service/acme", trial_revision), delivered)

        # 5. reports recall the current stable revision (with its review) and the
        # replaced trial revision is recalled from git history by two reports.
        for lineage in ("r1", "r2"):
            self.fixture.add_event(kind="report", note_id="service/acme", revision=new_revision, lineage_id=lineage)
        self.assertTrue(self._review("service/acme", new_revision, new_gate, "recall"))
        for lineage in ("h1", "h2"):
            self.fixture.add_event(kind="report", note_id="service/acme", revision=trial_revision, lineage_id=lineage)
        self.assertTrue(self._review("service/acme", trial_revision, gate_digest, "recall"))
        recalled = self._run()
        self.assertEqual(recalled["errors"], [], recalled)
        by_revision = {entry["revision"]: entry for entry in recalled["recalled"]}
        self.assertEqual(by_revision[new_revision]["reason_code"], "operator_confirmed")
        self.assertEqual(by_revision[trial_revision]["reason_code"], "operator_confirmed")
        self.assertTrue(by_revision[trial_revision]["historical"])
        self.assertTrue((self.commons / "recalls" / "services" / "acme" / f"{trial_revision[7:]}.json").is_file())
        marker = self.commons / "recalls" / "services" / "acme" / f"{new_revision[7:]}.json"
        self.assertTrue(marker.is_file())
        self.assertNotIn(("service/acme", new_revision), self._delivered())
        before = marker.read_bytes()
        head_before = hp.CommonsRepo(self.commons).head()

        # 6. an exhausted budget changes nothing and never rewrites the marker
        self.fixture.cap = 0
        starved = self._run()
        self.assertEqual(marker.read_bytes(), before)
        self.assertEqual(hp.CommonsRepo(self.commons).head(), head_before)
        self.assertEqual(
            self.fixture.recalls["entries"],
            [
                {"note_id": "service/acme", "revision": trial_revision},
                {"note_id": "service/acme", "revision": new_revision},
            ],
        )

        # 7. an identical rerun is a no-op: same commit, same markers, no duplicate ack
        self.fixture.cap = 10_000_000
        again = self._run()
        self.assertEqual(again["errors"], [], again)
        self.assertEqual(again["acked"], [])
        self.assertEqual(hp.CommonsRepo(self.commons).head(), head_before)
        self.assertEqual(marker.read_bytes(), before)
        self.assertEqual(starved["commit"], head_before)



if __name__ == "__main__":
    unittest.main()
