"""H5 acceptance: synthetic evidence must never earn a live completion literal.

The wire tests run the imported verifier in a subprocess against a stdlib
ThreadingHTTPServer fake Worker, a throwaway git commons checkout under /tmp and
a fake ``wrangler`` executable backed by sqlite. Only that subprocess maps the
synthetic https origins to loopback; ``verify_hub`` itself still refuses any
non-https origin and never learns that the target is local.

No test asserts a live acceptance result: they assert that the verifier's
observation logic rejects synthetic evidence that is wrong, incomplete or
self-reported, and that a fully synthetic happy path reaches the scenario literal
only after setup, exercise and teardown all succeed.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_hub.py"
sys.path.insert(0, str(ROOT / "lib"))
from portwright import hub_contracts as hc

SYNTHETIC_TOKEN = lambda seed: base64.urlsafe_b64encode(  # noqa: E731
    hashlib.sha256(seed.encode()).digest()).decode().rstrip("=")
TOKEN_NAMES = {
    "read": "HUB_TEST_READ_TOKEN", "a": "HUB_TEST_SUBMIT_A_TOKEN",
    "b": "HUB_TEST_SUBMIT_B_TOKEN", "c": "HUB_TEST_SUBMIT_C_TOKEN",
    "operator": "HUB_TEST_OPERATOR_TOKEN",
    "other": "HUB_TEST_OTHER_AUDIENCE_TOKEN",
    "personal": "HUB_TEST_PERSONAL_READ_TOKEN",
}


def load_verify_hub():
    spec = importlib.util.spec_from_file_location("verify_hub", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Local wire harness: fake H2 Worker + fake GitHub + fake wrangler + git.
# --------------------------------------------------------------------------

WRANGLER_SOURCE = '''#!{python}
import json, os, sqlite3, sys

DB = {database!r}
MODE = {mode!r}
ALLOWED = {{"CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "HOME", "PATH",
           "WRANGLER_SEND_METRICS", "WRANGLER_WRITE_LOGS"}}
PLATFORM = {{"LC_CTYPE", "__CF_USER_TEXT_ENCODING", "LANG", "LC_ALL", "TMPDIR", "COMM"}}
extra = sorted(set(os.environ) - ALLOWED - PLATFORM)
assert not extra, extra
assert os.environ.get("WRANGLER_WRITE_LOGS") == "false"
assert os.environ.get("WRANGLER_SEND_METRICS") == "false"
assert os.environ.get("CLOUDFLARE_API_TOKEN") == {token!r}
assert os.environ.get("CLOUDFLARE_ACCOUNT_ID") == {account!r}
argv = sys.argv[1:]
if MODE == "d1_missing":
    print(json.dumps([{{"success": False, "errors": [{{"code": 1000}}]}}], indent=4))
    sys.exit(0)
sql = argv[argv.index("--command") + 1]
assert sql.upper().startswith("SELECT "), sql
assert not any(marker in sql for marker in (";", "--", "/*", "*/")), sql
connection = sqlite3.connect(DB, timeout=30)
try:
    cursor = connection.execute(sql)
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
finally:
    connection.close()
print(json.dumps([{{"success": True, "results": rows, "meta": {{}}}}], indent=4))
'''


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class _Response:
    """Minimal urllib response for in-process r2 tests."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self, *args):
        return self._payload

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Wire:
    """Everything a synthetic public Hub needs to answer the real verifier."""

    def __init__(self, workdir, mode="success"):
        self.workdir = Path(workdir)
        self.mode = mode
        self.commons = self.workdir / "commons"
        self.bare = self.workdir / "commons.git"
        self.fake_root = self.workdir / "fake_root"
        self.database = self.workdir / "hub.sqlite3"
        self.home = self.workdir / "home"
        self.var_cap = 10_000_000
        self.audience = "public"
        self.month = time.strftime("%Y-%m", time.gmtime())
        self.generation = 0
        self.commit = hashlib.sha1(b"gen0").hexdigest()
        self.var_reserve = 4_000
        self.ports = {}
        self.requests = []
        self.servers = []
        self.runs = []
        self.next_run = 1
        self.race_lock = Lock()
        self.workflow_lock = Lock()
        self.workflow_threads = []
        self.workflow_gates = []
        self.overlap_observed = False
        self.pending_intake_overlap = False
        self.pending_review_overlap = False
        self.race_fail_status = {"race_400": 400, "race_500": 500}.get(mode)
        self.failed_reserve = False
        self.r2_objects = {}
        self.opener = urllib.request.build_opener(_RefuseRedirect()).open
        self.env = {}
        self.missing_evidence = mode == "d1_missing"

    # -- setup -------------------------------------------------------------
    def start(self):
        self.commons.mkdir(parents=True)
        self.fake_root.mkdir(parents=True)
        self.home.mkdir(parents=True)
        self._init_git()
        self._init_database()
        self._install_wrangler()
        scripts = self.fake_root / "scripts"
        scripts.mkdir()
        (scripts / "hub_pipeline.py").write_text(
            "import argparse,json,sqlite3,uuid\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser()\n"
            "for key in ('commons','code-root','audience','review-note-id','review-revision',"
            "'review-decision','expected-events-digest'): p.add_argument('--'+key)\n"
            "p.add_argument('--approve-review',action='store_true')\n"
            "a=p.parse_args()\n"
            "kind,slug=a.review_note_id.split('/')\n"
            "text=Path(a.commons,kind+'s',slug+'.md').read_text()\n"
            "assert a.review_revision in text\n"
            f"db=sqlite3.connect({str(self.database)!r})\n"
            "digest='sha256:'+'d'*64\n"
            "reason=('lineages_pending_review' if a.review_decision=='promote' "
            "else 'distinct_reports_pending_review')\n"
            "if a.approve_review:\n"
            " assert a.expected_events_digest==digest\n"
            " db.execute(\"INSERT INTO events(id,note_id,revision,kind,action,operation_id,value,payload) "
            "VALUES(?,?,?,'ack',?,?,0,'{}')\",(str(uuid.uuid4()),a.review_note_id,a.review_revision,"
            "'review_'+a.review_decision,str(uuid.uuid4())))\n"
            f" if {self.mode!r}=='duplicate_ack':\n"
            "  db.execute(\"INSERT INTO events(id,note_id,revision,kind,action,operation_id,value,payload) "
            "VALUES(?,?,?,'ack',?,?,0,'{}')\",(str(uuid.uuid4()),a.review_note_id,a.review_revision,"
            "'review_'+a.review_decision,str(uuid.uuid4())))\n"
            " db.commit()\n"
            "print(json.dumps({'events_digest':digest,'pending_review':True,"
            "'reason_code':reason,'posted':a.approve_review}))\n")
        worker = self._serve(WorkerHandler)
        github = self._serve(GitHubHandler)
        cloudflare = self._serve(CloudflareHandler)
        self.ports = {"hub.test": worker, "api.github.com": github,
                      "api.cloudflare.com": cloudflare}
        # Test-only transport mapping. The real CLI, sync, catalog, preflight and MCP
        # run unchanged in child processes; no production HTTP escape hatch is added.
        library = self.fake_root / "lib"
        library.mkdir()
        (library / "portwright").symlink_to(ROOT / "lib/portwright", target_is_directory=True)
        (library / "sitecustomize.py").write_text(
            "from urllib.request import Request, urlopen\n"
            "from portwright import hub_sync\n"
            "def local_open(request, **kwargs):\n"
            " assert request.full_url.startswith('https://hub.test/')\n"
            f" target='http://127.0.0.1:{worker}'+request.full_url[len('https://hub.test'):]\n"
            " response=urlopen(Request(target,headers=dict(request.header_items())),**kwargs)\n"
            " response.url=request.full_url\n"
            " return response\n"
            "hub_sync.urlopen=local_open\n")
        self.r2_objects["recalls/current.json"] = json.dumps(
            {"audience": self.audience, "entries": []}).encode()
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "HUB_ORIGIN": "https://hub.test",
            "HUB_VERIFY_COMMONS": str(self.commons),
            "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic-cf",
            "HUB_VERIFY_ACCOUNT_ID": "synthetic-account",
            "HUB_VERIFY_GITHUB_TOKEN": "synthetic-github",
            "HUB_VERIFY_WRANGLER_CONFIG": "/dev/null",
            "HUB_VERIFY_COMMONS_REPO": "owner/repo",
            "HUB_VERIFY_BUCKET": "synthetic-bucket",
            "HUB_VERIFY_WORKER": "synthetic-worker",
        }
        for role, name in TOKEN_NAMES.items():
            self.env[name] = SYNTHETIC_TOKEN("token-" + role)

    def stop(self):
        self.release_workflows()
        for thread in self.workflow_threads:
            thread.join(30)
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def _git(self, *args):
        run = subprocess.run(["git", *args], capture_output=True, timeout=60)
        if run.returncode != 0:
            raise AssertionError("git %s: %s" % (" ".join(args), run.stderr.decode()))
        return run.stdout

    def _init_git(self):
        self._git("init", "-q", "-b", "main", str(self.commons))
        self._git("-C", str(self.commons), "config", "user.email", "harness@example.invalid")
        self._git("-C", str(self.commons), "config", "user.name", "Synthetic Harness")
        (self.commons / "README.md").write_text("synthetic commons\n")
        self._git("-C", str(self.commons), "add", "-A")
        self._git("-C", str(self.commons), "commit", "-qm", "synthetic base")
        self._git("init", "-q", "--bare", "-b", "main", str(self.bare))
        self._git("-C", str(self.commons), "remote", "add", "origin", str(self.bare))
        self._git("-C", str(self.commons), "push", "-q", "origin", "main")

    def _install_wrangler(self):
        binary = self.fake_root / "hub" / "node_modules" / ".bin"
        binary.mkdir(parents=True)
        script = binary / "wrangler"
        script.write_text(WRANGLER_SOURCE.format(python=sys.executable, database=str(self.database),
                                                 mode=self.mode, token="synthetic-cf",
                                                 account="synthetic-account"))
        script.chmod(0o755)

    def _serve(self, base):
        class Bound(base):
            wire = self
        server = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
        Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server.server_port

    def open_https(self, request, timeout=30):
        parts = urllib.parse.urlsplit(request.full_url)
        if parts.scheme != "https" or parts.netloc not in self.ports:
            raise AssertionError("harness refuses unmapped origin " + request.full_url)
        target = urllib.parse.urlunsplit(("http", "127.0.0.1:%d" % self.ports[parts.netloc],
                                          parts.path, parts.query, parts.fragment))
        rewritten = urllib.request.Request(target, data=request.data,
                                           method=request.get_method(),
                                           headers=dict(request.headers))
        return self.opener(rewritten, timeout=timeout)

    # -- sqlite ------------------------------------------------------------
    def _init_database(self):
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            "CREATE TABLE intake (id TEXT PRIMARY KEY, token_hash TEXT, lineage_id TEXT,"
            " request_id TEXT, request_digest TEXT, kind TEXT, service_id TEXT,"
            " target_note_id TEXT, expected_revision TEXT, body TEXT, doc_url TEXT,"
            " success_evidence TEXT, state TEXT, note_id TEXT, revision TEXT, grade TEXT,"
            " uri TEXT, published_commit TEXT, reason_code TEXT, created INTEGER,"
            " gate_digest TEXT, commit_sha TEXT, updated INTEGER, day INTEGER, UNIQUE(lineage_id, request_id));"
            "CREATE TABLE events (id TEXT PRIMARY KEY, intake_id TEXT, note_id TEXT,"
            " revision TEXT, lineage_id TEXT, token_hash TEXT, kind TEXT, action TEXT,"
            " operation_id TEXT, value INTEGER, payload TEXT, month TEXT, created INTEGER,"
            " day INTEGER, UNIQUE(kind, operation_id));"
            "CREATE TABLE tokens (hash TEXT PRIMARY KEY, org TEXT, scope TEXT,"
            " lineage_id TEXT, revoked INTEGER, expires INTEGER, created INTEGER);"
            "CREATE TABLE budget_override (id INTEGER PRIMARY KEY, cap_microusd INTEGER);"
            "CREATE TABLE recalls (note_id TEXT, revision TEXT, PRIMARY KEY(note_id, revision));")
        for index, role in enumerate(("read", "a", "b", "c", "operator", "other", "personal")):
            scope = {"read": "read", "personal": "read", "operator": "operator"}.get(role, "submit")
            connection.execute("INSERT INTO tokens VALUES (?,?,?,?,?,?,?)", (
                hashlib.sha256(self.env_token(role).encode()).hexdigest(), "synthetic-org", scope,
                "lineage-%s" % role, 0, int(time.time()) + 3600, int(time.time())))
        connection.commit()
        connection.close()

    @staticmethod
    def env_token(role):
        return SYNTHETIC_TOKEN("token-" + role)

    def db(self, sql, params=()):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            cursor = connection.execute(sql, params)
            columns = [item[0] for item in cursor.description] if cursor.description else []
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            connection.commit()
            return rows
        finally:
            connection.close()

    # -- product simulation ------------------------------------------------
    def authenticate(self, headers):
        match = re.fullmatch(r"Bearer (.+)", headers.get("Authorization") or "")
        if not match:
            return None
        digest = hashlib.sha256(match.group(1).encode()).hexdigest()
        rows = self.db("SELECT hash, scope, lineage_id FROM tokens"
                       " WHERE hash=? AND org='synthetic-org' AND revoked=0 AND expires>?",
                       (digest, int(time.time())))
        return rows[0] if rows else None

    def uri(self, note_id, revision, grade="trial"):
        return f"skill://gisul/commons/{grade}/github/notes/{note_id.split('/', 1)[1]}.md"

    def sync_payload(self, query):
        trial = (query.get("include_trial") or ["false"])[-1] == "true"
        recalled = {(row["note_id"], row["revision"]) for row in self.db("SELECT * FROM recalls")}
        notes = []
        for row in self.db("SELECT * FROM intake WHERE state='published'"):
            if (row["note_id"], row["revision"]) in recalled:
                continue
            if not trial and row["grade"] != "stable":
                continue
            text = self.note_text(row)
            uri = self.uri(row["note_id"], row["revision"], row["grade"])
            notes.append({"note_id": row["note_id"], "revision": row["revision"],
                          "kind": "lesson", "service_id": row["service_id"],
                          "grade": row["grade"], "uri": uri,
                          "path": uri.removeprefix("skill://gisul/commons/"),
                          "file_digest": hc.sha256_digest(text), "size": len(text.encode()), "text": text})
        notes.sort(key=lambda note: (note["note_id"], note["revision"]))
        entries = [{"note_id": row["note_id"], "revision": row["revision"]}
                   for row in self.db("SELECT * FROM recalls")]
        recalls = {"schema_version": 1, "audience": self.audience, "commit": self.commit,
                   "sequence": self.generation + 1, "entries": entries}
        return {"schema_version": 1, "audience": self.audience,
                "release_identity": {"commit": self.commit, "release": "synthetic",
                                     "inventory_digest": hc.canonical_digest(notes)},
                "notes": notes, "notes_digest": hc.canonical_digest(notes),
                "recalls": recalls, "recalls_digest": hc.canonical_digest(recalls)}

    def effective_cap(self):
        rows = self.db("SELECT cap_microusd FROM budget_override WHERE id=1")
        return min(self.var_cap, rows[0]["cap_microusd"]) if rows else self.var_cap

    def budget_summary(self):
        cost = self.db("SELECT COALESCE(SUM(value),0) AS n FROM events WHERE kind='cost'")[0]["n"]
        reserved = self.db("SELECT COALESCE(SUM(value),0) AS n FROM events"
                           " WHERE kind='reservation' AND action!='settled'")[0]["n"]
        cap = self.effective_cap()
        return {"month": self.month, "cap_micro_usd": cap, "cost_micro_usd": cost,
                "reserved_micro_usd": reserved, "available_micro_usd": cap - cost - reserved}

    def reserve(self, body, session):
        operation_id = body.get("operation_id")
        amount = body.get("reserve_micro_usd")
        if (not isinstance(operation_id, str) or not isinstance(amount, int)
                or body.get("request_digest") != "sha256:" + "0" * 64 or body.get("bytes") != 0
                or body.get("action") != "reserve"
                or body.get("purpose") not in ("intake", "confirm", "bundle")):
            return 400, {"code": "INVALID_PARAMS"}
        if self.missing_evidence:
            return 400, {"code": "INVALID_PARAMS"}
        if self.race_fail_status:
            with self.race_lock:
                fail = not self.failed_reserve
                self.failed_reserve = True
            if fail:
                return self.race_fail_status, {"code": "INVALID_PARAMS"}
        # One serialized writer: the balance check and the insert share a transaction,
        # exactly like the Worker's conditional INSERT.
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT id FROM events WHERE kind='reservation' AND operation_id=?",
                (operation_id,)).fetchone()
            if duplicate:
                connection.execute("COMMIT")
                return 200, {"granted": False, "duplicate": True}
            override = connection.execute("SELECT cap_microusd FROM budget_override WHERE id=1").fetchone()
            cap = min(self.var_cap, override[0]) if override else self.var_cap
            cost = connection.execute(
                "SELECT COALESCE(SUM(value),0) FROM events WHERE kind='cost'").fetchone()[0]
            reserved = connection.execute(
                "SELECT COALESCE(SUM(value),0) FROM events WHERE kind='reservation'"
                " AND action!='settled'").fetchone()[0]
            if amount + cost + reserved > cap:
                connection.execute("COMMIT")
                return 409, {"code": "BUDGET_EXHAUSTED"}
            reservation = str(uuid.uuid4())
            now = int(time.time())
            connection.execute(
                "INSERT INTO events (id, kind, action, operation_id, value, payload, month,"
                " created, day) VALUES (?,?,?,?,?,?,?,?,?)",
                (reservation, "reservation", "reserved", operation_id, amount,
                 json.dumps({"purpose": body.get("purpose")}), self.month, now, now // 86400))
            connection.execute("COMMIT")
            return 200, {"granted": True, "reservation_id": reservation, "month": self.month,
                         "expires_at": now + 86400}
        finally:
            connection.close()

    def settle(self, body):
        reservation = body.get("reservation_id")
        rows = self.db("SELECT * FROM events WHERE id=? AND kind='reservation'", (reservation,))
        if not rows:
            return 404, {"code": "RESERVATION_NOT_FOUND"}
        if rows[0]["action"] == "settled":
            return 200, {"settled": True, "reservation_id": reservation, "cost_id": "dup",
                         "actual_micro_usd": 0, "month": self.month, "duplicate": True}
        self.db("UPDATE events SET action='settled' WHERE id=?", (reservation,))
        cost = str(uuid.uuid4())
        self.db("INSERT INTO events (id, kind, action, operation_id, value, payload, month,"
                " created, day) VALUES (?,?,?,?,?,?,?,?,?)",
                (cost, "cost", "settled", "settle:" + reservation, 0, "{}", self.month,
                 int(time.time()), int(time.time() // 86400)))
        return 200, {"settled": True, "reservation_id": reservation, "cost_id": cost,
                     "actual_micro_usd": 0, "month": self.month, "duplicate": False}

    def set_override(self, value):
        if value is None:
            self.db("DELETE FROM budget_override WHERE id=1")
            return 200, {"cap_micro_usd": self.var_cap, "override_micro_usd": None}
        if not isinstance(value, int) or value < 0 or value > self.var_cap:
            return 400, {"code": "INVALID_PARAMS"}
        self.db("INSERT INTO budget_override (id, cap_microusd) VALUES (1, ?)"
                " ON CONFLICT(id) DO UPDATE SET cap_microusd=excluded.cap_microusd", (value,))
        return 200, {"cap_micro_usd": value, "override_micro_usd": value}

    def submit(self, arguments, session):
        request_id = arguments.get("request_id") or str(uuid.uuid4())
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        existing = self.db("SELECT * FROM intake WHERE lineage_id=? AND request_id=?",
                           (session["lineage_id"], request_id))
        if existing:
            if existing[0]["request_digest"] != digest:
                return 409, {"code": "IDEMPOTENCY_CONFLICT"}
            return 200, {"intake_id": existing[0]["id"], "state": existing[0]["state"],
                         "duplicate": True}
        intake = str(uuid.uuid4())
        now = int(time.time())
        try:
            self.db("INSERT INTO intake (id, token_hash, lineage_id, request_id, request_digest,"
                    " kind, service_id, target_note_id, expected_revision, body, doc_url,"
                    " success_evidence, state, created, updated, day)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (intake, session["hash"], session["lineage_id"], request_id, digest,
                     arguments.get("kind"), arguments.get("service_id"), arguments.get("note_id"),
                     arguments.get("expected_revision"), arguments.get("body"),
                     arguments.get("doc_url"), json.dumps(arguments.get("success_evidence")),
                     "pending", now, now, now // 86400))
        except sqlite3.IntegrityError:
            return self.submit(arguments, session)
        return 200, {"intake_id": intake, "state": "pending", "duplicate": False}

    def confirm(self, arguments, session):
        operation = hashlib.sha256(("confirm:%s:%s:%s" % (
            session["lineage_id"], arguments.get("note_id"), arguments.get("revision"))).encode()).hexdigest()
        existing = self.db("SELECT id FROM events WHERE kind='confirm' AND operation_id=?", (operation,))
        if not existing:
            self.db("INSERT OR IGNORE INTO events (id, note_id, revision, lineage_id, token_hash, kind,"
                    " action, operation_id, value, payload, month, created, day)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), arguments.get("note_id"), arguments.get("revision"),
                     session["lineage_id"], session["hash"], "confirm", "confirmed", operation, 0,
                     json.dumps({"validation": "passed"}), self.month, int(time.time()),
                     int(time.time() // 86400)))
        return 200, {"note_id": arguments.get("note_id"), "revision": arguments.get("revision"),
                     "lineage_id": session["lineage_id"]}

    def report(self, arguments, session):
        operation = hashlib.sha256(("report:%s:%s:%s" % (
            session["lineage_id"], arguments.get("note_id"), arguments.get("revision"))).encode()).hexdigest()
        if not self.db("SELECT id FROM events WHERE kind='report' AND operation_id=?", (operation,)):
            self.db("INSERT INTO events (id, note_id, revision, lineage_id, token_hash, kind,"
                    " action, operation_id, value, payload, month, created, day)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), arguments.get("note_id"), arguments.get("revision"),
                     session["lineage_id"], session["hash"], "report", "reported", operation, 0,
                     json.dumps({"reason": arguments.get("reason")}), self.month, int(time.time()),
                     int(time.time() // 86400)))
        return 200, {"note_id": arguments.get("note_id"), "revision": arguments.get("revision"),
                     "lineage_id": session["lineage_id"]}

    def note_text(self, row):
        return (self.commons / "failures" / (row["note_id"].split("/", 1)[1] + ".md")).read_text()

    def read_resource(self, uri, include_trial):
        rows = self.db("SELECT * FROM intake WHERE uri=? AND state='published'", (uri,))
        if not rows:
            return 404, {"code": "NOT_FOUND"}
        row = rows[0]
        if self.db("SELECT revision FROM recalls WHERE note_id=? AND revision=?",
                   (row["note_id"], row["revision"])):
            return 410, {"code": "REVISION_RECALLED", "revision": row["revision"]}
        if row["grade"] != "stable" and not include_trial:
            return 404, {"code": "NOT_FOUND"}
        return 200, {"result": {"contents": [{"uri": uri, "text": self.note_text(row)}]}}

    def publish(self, row):
        # H3 publishes kind=lesson submissions as failure/<stem> notes (hub_pipeline _note_target);
        # the stem date is the server's UTC creation day, which the dated() body matches.
        slug = f"{time.strftime('%Y-%m-%d', time.gmtime(row['created']))}-{row['service_id']}-{row['id']}"
        note_id = "failure/" + slug
        text = hc.render_note(row["body"], {"grade": "trial", "doc_url": row["doc_url"],
                                          "intake_id": row["id"]})
        revision = hc.note_revision("lesson", row["service_id"], text, row["doc_url"])
        text = hc.render_note(text, {"revision": revision})
        path = self.commons / "failures" / (slug + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        now = int(time.time())
        reservation = self.db("SELECT id FROM events WHERE kind='reservation' AND operation_id=?",
                              ("budget:" + row["id"],))
        if reservation:
            self.db("UPDATE events SET action='settled' WHERE id=?", (reservation[0]["id"],))
        self.db("INSERT INTO events (id, intake_id, kind, action, operation_id, value, payload,"
                " month, created, day) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), row["id"], "cost", "settled", "model:" + row["id"],
                 self.var_reserve, "{}", self.month, now, now // 86400))
        self._commit("Publish %s %s\n\nIntake-ID: %s" % (note_id, revision, row["id"]))
        self.r2_write("notes/%s/%s.json" % (note_id, revision.removeprefix("sha256:")),
                      {"note_id": note_id, "revision": revision, "grade": "trial"})
        self.r2_write("manifests/current.json",
                      {"generation": self.generation, "objects": sorted(self.r2_objects)})
        self.db("UPDATE intake SET state='published', note_id=?, revision=?, grade='trial',"
                " uri=?, published_commit=?, updated=? WHERE id=?",
                (note_id, revision, self.uri(note_id, revision), self.commit, now, row["id"]))

    def r2_write(self, key, value):
        self.r2_objects[key] = (value if isinstance(value, bytes)
                                else json.dumps(value).encode())

    def recall(self, note_id, revision):
        if self.db("SELECT revision FROM recalls WHERE note_id=? AND revision=?", (note_id, revision)):
            return
        self.db("INSERT INTO recalls (note_id, revision) VALUES (?,?)", (note_id, revision))
        slug = note_id.split("/", 1)[1]
        path = self.commons / "recalls" / ("services" if note_id.startswith("service/") else
                                           "failures") / slug / (revision.removeprefix("sha256:") + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"note_id": note_id, "revision": revision}))
        self._commit("Recall %s %s" % (note_id, revision))
        self.r2_write("recalls/current.json", {"audience": self.audience, "entries": [
            {"note_id": row["note_id"], "revision": row["revision"]}
            for row in self.db("SELECT * FROM recalls")]})

    def _commit(self, message):
        self._git("-C", str(self.commons), "add", "-A")
        self._git("-C", str(self.commons), "commit", "-qm", message, "--allow-empty")
        self._git("-C", str(self.commons), "push", "-q", "origin", "main")
        self.generation += 1
        self.commit = hashlib.sha1(("gen%d" % self.generation).encode()).hexdigest()

    def run_workflow(self):
        for row in self.db("SELECT * FROM intake WHERE state='pending'"):
            summary = self.budget_summary()
            if summary["available_micro_usd"] >= self.var_reserve:
                self.db("INSERT INTO events (id, intake_id, kind, action, operation_id, value,"
                        " payload, month, created, day) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), row["id"], "reservation", "reserved",
                         "budget:" + row["id"], self.var_reserve, "{}", self.month,
                         int(time.time()), int(time.time() // 86400)))
                self.publish(row)
        for row in self.db("SELECT * FROM intake WHERE state='published' AND grade='trial'"):
            if not self.db("SELECT id FROM events WHERE kind='ack' AND action='review_promote'"
                           " AND note_id=? AND revision=?", (row["note_id"], row["revision"])):
                continue
            path = self.commons / "failures" / (row["note_id"].split("/", 1)[1] + ".md")
            path.write_text(path.read_text().replace("grade: trial", "grade: stable"))
            self._commit("Promote: %s %s synthetic" % (row["note_id"], row["revision"]))
            self.db("UPDATE intake SET grade='stable' WHERE id=?", (row["id"],))
            self.r2_write("stable/current.json", {"note_id": row["note_id"], "revision": row["revision"]})
        for group in self.db("SELECT note_id, revision, COUNT(DISTINCT lineage_id) AS lineages"
                             " FROM events WHERE kind='report' GROUP BY note_id, revision"):
            if group["lineages"] >= 2:
                self.recall(group["note_id"], group["revision"])

    def dispatch_workflow(self):
        run = {"id": self.next_run, "status": "queued", "conclusion": None,
               "event": "workflow_dispatch", "head_branch": "main"}
        self.next_run += 1
        self.overlap_observed |= any(item["status"] != "completed" for item in self.runs)
        pending = {row["id"] for row in self.db("SELECT id FROM intake WHERE state='pending'")}
        reviews = {row["id"] for row in self.db(
            "SELECT e.id FROM events e JOIN intake i ON i.note_id=e.note_id AND i.revision=e.revision"
            " WHERE e.kind='ack' AND e.action='review_promote' AND i.grade='trial'")}
        for prior in self.runs:
            if prior["status"] in ("queued", "in_progress"):
                self.pending_intake_overlap |= bool(pending & set(prior["pending"]))
                self.pending_review_overlap |= bool(reviews & set(prior["reviews"]))
        run.update(pending=sorted(pending), reviews=sorted(reviews))
        self.runs.append(run)
        gate = Event()
        self.workflow_gates.append(gate)

        def complete():
            # Dispatch acknowledgement is not workflow completion. Serialize git
            # mutation as the real workflow concurrency group does, not the HTTP 204.
            gate.wait()
            with self.workflow_lock:
                run["status"] = "in_progress"
                try:
                    self.run_workflow()
                    run["conclusion"] = "success"
                except Exception:
                    run["conclusion"] = "failure"
                run["status"] = "completed"
        thread = Thread(target=complete)
        self.workflow_threads.append(thread)
        return thread

    def release_workflows(self):
        for gate in self.workflow_gates:
            gate.set()
        self.workflow_gates.clear()

    def summary(self):
        override = self.db("SELECT cap_microusd FROM budget_override WHERE id=1")
        return {
            "overlap_observed": self.overlap_observed,
            "pending_intake_overlap": self.pending_intake_overlap,
            "pending_review_overlap": self.pending_review_overlap,
            "recalls": len(self.db("SELECT * FROM recalls")),
            "override": override[0]["cap_microusd"] if override else None,
            "states": {row["state"]: row["id"] for row in self.db("SELECT * FROM intake")},
            "token_revoked": self.db("SELECT COUNT(*) AS n FROM tokens WHERE revoked=1")[0]["n"],
            "reservation_rows": self.db("SELECT COUNT(*) AS n FROM events WHERE kind='reservation'")[0]["n"],
            "unsettled": self.db("SELECT COUNT(*) AS n FROM events WHERE kind='reservation'"
                                 " AND action!='settled'")[0]["n"],
        }


class WorkerHandler(BaseHTTPRequestHandler):
    wire: Wire = None  # type: ignore[assignment]

    def log_message(self, *args):
        pass

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _send(self, status, payload):
        data = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        self._handle("GET", b"")

    def do_POST(self):
        self._handle("POST", self._body())

    def _handle(self, method, body):
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query)
        self.wire.requests.append([method, parts.path])
        status, payload = self.dispatch(method, parts.path, query, body)
        self._send(status, payload)

    def dispatch(self, method, path, query, body):
        wire = self.wire
        if path == "/healthz":
            return 200, {"status": "ok"}
        session = wire.authenticate(self.headers)
        if session is None:
            return 401, {"code": "UNAUTHORIZED"}
        if path == "/sync":
            return 200, wire.sync_payload(query)
        if path == "/mcp":
            raw = body.decode("utf-8", errors="replace")
            if "synthetic-capture-marker" in raw:
                # Only this synthetic origin echoes; it proves capture() reads error bodies.
                return 400, {"echo": raw}
            if "synthetic-clean-reject" in raw:
                return 400, {"code": "INVALID_PARAMS"}
            return self.mcp(json.loads(raw), session)
        if path == "/admin/budget":
            if method == "GET":
                return 200, wire.budget_summary()
            payload = json.loads(body.decode())
            if "cap_microusd" in payload:
                return wire.set_override(payload["cap_microusd"])
            action = payload.get("action")
            if action == "reserve":
                return wire.reserve(payload, session)
            if action == "settle":
                return wire.settle(payload)
            return 400, {"code": "INVALID_PARAMS"}
        if path == "/admin/tokens":
            return self.tokens(json.loads(body.decode()), session)
        if path == "/admin/intake":
            if wire.mode == "teardown_fail":
                return 500, {"code": "CONFIG_INVALID"}
            payload = json.loads(body.decode())
            wire.db("UPDATE intake SET state='rejected', body=NULL, doc_url=NULL,"
                    " success_evidence=NULL, reason_code=? WHERE id=?",
                    (payload.get("reason_code"), payload.get("intake_id")))
            return 200, {"intake_id": payload.get("intake_id"), "state": "rejected"}
        if path == "/admin/current":
            return 200, {"current": wire.commit, "generation": wire.generation}
        if path == "/admin/events":
            return 200, {"events": []}
        return 404, {"code": "NOT_FOUND"}

    def mcp(self, request, session):
        wire = self.wire
        identifier = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if method == "tools/list":
            return 200, rpc(identifier, {"tools": [{"name": "submit_lesson"},
                                                   {"name": "confirm_lesson"},
                                                   {"name": "report_failure"}]})
        if method == "skills/list":
            return 200, rpc(identifier, {"skills": []})
        if method == "resources/list":
            return 200, rpc(identifier, {"resources": []})
        if method == "resources/read":
            include = (params.get("_meta") or {}).get("io.portwright/include_trial") is True
            status, payload = wire.read_resource(params.get("uri"), include)
            return status, payload if status != 200 else dict(payload, jsonrpc="2.0", id=identifier)
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            handler = {"submit_lesson": wire.submit, "confirm_lesson": wire.confirm,
                       "report_failure": wire.report}.get(name)
            if handler is None:
                return 200, {"jsonrpc": "2.0", "id": identifier,
                             "error": {"code": -32601, "message": "not found"}}
            status, structured = handler(arguments, session)
            if status != 200:
                return status, structured
            return 200, rpc(identifier, structured)
        return 200, {"jsonrpc": "2.0", "id": identifier,
                     "error": {"code": -32601, "message": "not found"}}

    def tokens(self, payload, session):
        wire = self.wire
        action = payload.get("action")
        if action == "issue":
            token = SYNTHETIC_TOKEN(str(uuid.uuid4()))
            digest = hashlib.sha256(token.encode()).hexdigest()
            lineage = str(uuid.uuid4())
            wire.db("INSERT INTO tokens VALUES (?,?,?,?,?,?,?)",
                    (digest, "synthetic-org", payload.get("scope"), lineage, 0,
                     payload.get("expires"), int(time.time())))
            return 200, {"action": "issue", "token": token, "hash": digest, "org": "synthetic-org",
                         "scope": payload.get("scope"), "expires": payload.get("expires"),
                         "lineage_id": lineage}
        if action == "reissue":
            old = payload.get("token_hash")
            rows = wire.db("SELECT * FROM tokens WHERE hash=?", (old,))
            token = SYNTHETIC_TOKEN(str(uuid.uuid4()))
            digest = hashlib.sha256(token.encode()).hexdigest()
            lineage = rows[0]["lineage_id"] if rows else str(uuid.uuid4())
            wire.db("UPDATE tokens SET revoked=1 WHERE hash=?", (old,))
            wire.db("INSERT INTO tokens VALUES (?,?,?,?,?,?,?)",
                    (digest, "synthetic-org", rows[0]["scope"] if rows else "submit", lineage, 0,
                     payload.get("expires"), int(time.time())))
            return 200, {"action": "reissue", "token": token, "hash": digest, "org": "synthetic-org",
                         "scope": rows[0]["scope"] if rows else "submit",
                         "expires": payload.get("expires"), "lineage_id": lineage}
        if action == "revoke":
            wire.db("UPDATE tokens SET revoked=1 WHERE hash=?", (payload.get("token_hash"),))
            return 200, {"action": "revoke", "token_hash": payload.get("token_hash"),
                         "revoked": True, "duplicate": False}
        return 400, {"code": "INVALID_PARAMS"}


class CloudflareHandler(BaseHTTPRequestHandler):
    """Minimal read-only R2 + Worker settings surface for the verifier."""

    wire: Wire = None  # type: ignore[assignment]

    def log_message(self, *args):
        pass

    def _send(self, status, payload, raw=None):
        data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else b"")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        self.wire.requests.append(["GET", parts.path])
        wire = self.wire
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        if token != wire.env.get("HUB_VERIFY_CLOUDFLARE_TOKEN"):
            return self._send(401, {"success": False})
        marker = "/r2/buckets/"
        if marker in parts.path:
            rest = parts.path.split(marker, 1)[1]
            _, _, suffix = rest.partition("/objects")
            key = urllib.parse.unquote(suffix[1:]) if suffix.startswith("/") else ""
            if not key:
                objects = [{"key": name, "etag": hashlib.sha256(value).hexdigest()}
                           for name, value in sorted(wire.r2_objects.items())]
                return self._send(200, {"success": True, "result": objects,
                                        "result_info": {"is_truncated": False, "cursor": None}})
            value = wire.r2_objects.get(key)
            if value is None:
                return self._send(404, {"success": False})
            return self._send(200, None, raw=value)
        if "/workers/scripts/" in parts.path and parts.path.endswith("/settings"):
            return self._send(200, {"success": True, "result": {
                "bindings": [], "observability": {"enabled": False}, "logpush": False,
                "tail_consumers": []}})
        return self._send(404, {"success": False})


class GitHubHandler(BaseHTTPRequestHandler):
    wire: Wire = None  # type: ignore[assignment]

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        data = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        self.wire.requests.append(["GET", urllib.parse.urlsplit(self.path).path])
        self._send(200, {"workflow_runs": list(reversed(self.wire.runs))})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.wire.requests.append(["POST", urllib.parse.urlsplit(self.path).path])
        thread = self.wire.dispatch_workflow()
        self._send(204, None)
        thread.start()


def rpc(identifier, structured):
    return {"jsonrpc": "2.0", "id": identifier,
            "result": {"content": [{"type": "text", "text": "ok"}],
                       "structuredContent": structured}}


def idempotency_probe(module, wire):
    """Exercise the real overlap/ack paths of the idempotency scenario."""
    hub = module.Hub("public", "idempotency")
    hub.setup()
    probe = {}
    previous = hub.dispatch()
    hub.dispatch()
    hub.await_workflows(previous, 2)
    probe["runs"] = len(wire.runs)
    probe["overlap_observed"] = wire.overlap_observed
    revision = "sha256:" + "a" * 64
    wire.db("INSERT INTO events (id, note_id, revision, kind, action, operation_id, value,"
            " payload) VALUES (?,?,?,?,?,?,?,?)",
            ("ack-1", "service/github", revision, "ack", "review_promote", "ack:1", 0, "{}"))
    where = ("kind='ack' AND action='review_promote' AND note_id='service/github'"
             " AND revision='%s'" % revision)
    probe["ack_found"] = hub.count("events", where)
    probe["ack_absent"] = hub.count("events", where.replace("service/github", "service/other"))
    received = hub.submit(hub.note())
    columns = ["state", "published_commit"]
    pending_rows = hub.rows("intake", "id='%s'" % received["intake_id"], columns)
    hub.await_workflows(hub.dispatch(), 1)
    before = hub.rows("intake", "id='%s'" % received["intake_id"], columns)
    hub.await_workflows(hub.dispatch(), 1)
    after = hub.rows("intake", "id='%s'" % received["intake_id"], columns)
    probe["initial_state"] = pending_rows[0]["state"]
    probe["commit_after"] = before[0]["published_commit"]
    probe["intake_stable"] = before == after and before[0]["state"] == "published"
    previous = hub.dispatch()
    outcome = {}

    def short_wait():
        try:
            hub.await_workflows(previous, 2)
            outcome["short"] = "returned"
        except module.Blocked as exc:
            outcome["short"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            outcome["short"] = "harness:" + type(exc).__name__

    ticks = iter(range(0, 100000, 120))
    clock = getattr(module, "time", time)
    with patch.object(clock, "monotonic", lambda: next(ticks)):
        waiter = Thread(target=short_wait, daemon=True)
        waiter.start()
        waiter.join(20)
    probe["short"] = outcome.get("short", "timeout")
    return probe


def capture_probe(module, wire):
    """Exercise the bounded error-body reader the sample-echo check now depends on."""
    runner = module.Hub.__new__(module.Hub)
    runner.origin = os.environ["HUB_ORIGIN"]
    runner.dynamic_tokens = {}
    marker = "synthetic-capture-marker"
    raw = json.dumps({"marker": marker}).encode()
    code, text = runner.capture("POST", "/mcp", "a", raw=raw)
    echoed = marker in (text or "")
    try:
        module.check_rejected_response(runner, b'{"clean": "synthetic-clean-reject"}', marker)
        clean_ok = True
    except module.Failed:
        clean_ok = False
    try:
        module.check_rejected_response(runner, raw, marker)
        raised_on_echo = False
    except module.Failed:
        raised_on_echo = True
    try:
        module.check_rejected_response(runner, b"{}", marker)
        accepted_2xx = True  # a 200 response must never look like a refusal
    except module.Failed:
        accepted_2xx = False
    return {"code": code, "echoed": echoed, "clean_ok": clean_ok,
            "raised_on_echo": raised_on_echo, "accepted_2xx": accepted_2xx}


def h4_probe(module, wire):
    runner = module.Hub("public", "lifecycle")
    runner.setup()
    received = runner.submit(runner.note())
    row = runner.drain(received["intake_id"])
    runner.local_visibility(row["note_id"], row["revision"], "trial")
    runner.review(row["note_id"], row["revision"], "promote", "lineages_pending_review")
    runner.drain(received["intake_id"])
    runner.local_visibility(row["note_id"], row["revision"], "stable")
    other = runner.drain(runner.submit(runner.note(suffix="\nRecall control.\n"))["intake_id"])
    runner.local_sync(other["note_id"], other["revision"],
                      lambda: wire.recall(other["note_id"], other["revision"]))
    return {"trial_stable_recall": True}


def wire_main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--scenario", default="budget")
    parser.add_argument("--mode", default="success")
    options, _ = parser.parse_known_args(argv)
    wire = Wire(Path(options.workdir), options.mode)
    result = {"code": None, "message": None, "summary": None, "probe": None}
    try:
        wire.start()
        module = load_verify_hub()
        module.ROOT = wire.fake_root
        module.OPEN = wire.open_https
        module.POLL_SECONDS = 0.05
        # Deterministic scheduling: neither dispatch can finish before the verifier
        # reaches its wait. The original waiter still polls the independent HTTP API.
        await_workflows = module.Hub.await_workflows
        def release_and_wait(runner, previous, count=1, **kwargs):
            wire.release_workflows()
            return await_workflows(runner, previous, count, **kwargs)
        module.Hub.await_workflows = release_and_wait
        os.environ.clear()
        os.environ.update(wire.env)
        if options.scenario == "idempotency-probe":
            result["probe"] = idempotency_probe(module, wire)
        elif options.scenario == "capture-probe":
            result["probe"] = capture_probe(module, wire)
        elif options.scenario == "h4-probe":
            result["probe"] = h4_probe(module, wire)
        else:
            runner = module.Hub("public", options.scenario)
            code, message = module.execute(options.scenario, runner)
            result.update(code=code, message=message)
    except BaseException as exc:  # harness failure, never a verifier finding
        traceback.print_exc()
        result["harness_error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        try:
            wire.stop()
        except Exception:
            pass
    result["summary"] = wire.summary()
    result["requests"] = wire.requests
    print("WIRE_RESULT " + json.dumps(result))
    return 0


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
class VerifyHubTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_verify_hub()

    def run_cli(self, *args, env=None):
        clean = {key: value for key, value in os.environ.items()
                 if not key.startswith(("HUB_", "PORTWRIGHT_JEV_"))}
        clean.update(env or {})
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=clean, timeout=8)

    def wire(self, scenario="budget", mode="success"):
        """Run the real verifier against the local synthetic Hub in a subprocess."""
        launcher = ("import sys; sys.path.insert(0, %r); import test_verify_hub as harness;"
                    " sys.exit(harness.wire_main(sys.argv[1:]))" % str(ROOT / "tests"))
        with tempfile.TemporaryDirectory(prefix="h5-wire-") as workdir:
            run = subprocess.run(
                [sys.executable, "-B", "-c", launcher, "--workdir", workdir,
                 "--scenario", scenario, "--mode", mode],
                capture_output=True, text=True, timeout=300, cwd=workdir,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": workdir})
            line = next((row for row in run.stdout.splitlines() if row.startswith("WIRE_RESULT ")), None)
            self.assertIsNotNone(line, "no harness result\n%s\n%s" % (run.stdout, run.stderr))
            return json.loads(line.removeprefix("WIRE_RESULT ")), run

    # -- CLI boundaries ----------------------------------------------------
    def test_missing_credentials_blocks_without_disclosing_values(self):
        result = self.run_cli("--hub", "public", "--scenario", "secrets")
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCKED", result.stdout)
        self.assertIn("missing-credentials", result.stdout)
        self.assertNotIn("SECRETS OK", result.stdout)

    def test_reference_injection_runs_once_and_rejects_literal_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "_local/deploy/verify-hub.env"
            ref.parent.mkdir(parents=True)
            ref.write_text("HUB_TEST_PERSONAL_READ_TOKEN=op:" + "//RUNTIME/TEST/token\n")
            ref.chmod(0o600)
            count = root / "calls"
            fake = root / "opsvc"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import os,sys\nfrom pathlib import Path\n"
                "p=Path(os.environ['PW_TEST_COUNT']); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
                "assert sys.argv[1:4] == ['run','--env-file',os.environ['PW_TEST_REF']]\n"
                "i=sys.argv.index('--'); os.execv(sys.argv[i+1],sys.argv[i+1:])\n")
            fake.chmod(0o700)
            clean = {"HUB_ORIGIN": "", "HUB_TEST_PERSONAL_READ_TOKEN": "",
                     "PORTWRIGHT_HUB_VERIFY_INJECTED": "", "PORTWRIGHT_JEV_FIXTURES": "",
                     "PORTWRIGHT_JEV_RECORD": "", "PW_TEST_COUNT": str(count),
                     "PW_TEST_REF": str(ref)}
            with patch.object(self.module, "ROOT", root), patch.object(self.module, "OPSVC", fake, create=True), patch.dict(os.environ, clean):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = self.module.main(["--hub", "personal", "--scenario", "reachable"])
                self.assertEqual(code, 1)
                self.assertTrue(count.is_file(), "missing credentials did not attempt approved injection")
                self.assertEqual(count.read_text(), "x")
                self.assertEqual(out.getvalue().strip(), "BLOCKED reachable missing-credentials")
                with patch.object(self.module.os, "getuid", return_value=os.getuid() + 1):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(self.module.main(["--hub", "personal", "--scenario", "reachable"]), 1)
                self.assertEqual(count.read_text(), "x", "foreign-owned reference was used")
                ref.parent.chmod(0o777)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(self.module.main(["--hub", "personal", "--scenario", "reachable"]), 1)
                self.assertEqual(count.read_text(), "x", "group/world-writable parent was used")
                ref.parent.chmod(0o755)
                ref.write_text("HUB_TEST_PERSONAL_READ_TOKEN=literal-token\n")
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = self.module.main(["--hub", "personal", "--scenario", "reachable"])
                self.assertEqual(code, 1)
                self.assertEqual(count.read_text(), "x")
                self.assertEqual(out.getvalue().strip(), "BLOCKED reachable missing-credentials")

    def test_injection_cannot_promote_a_different_scenario_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "_local/deploy/verify-hub.env"
            ref.parent.mkdir(parents=True)
            ref.write_text("HUB_TEST_PERSONAL_READ_TOKEN=op:" + "//RUNTIME/TEST/token\n")
            ref.chmod(0o600)
            fake = root / "opsvc"
            fake.write_text(f"#!{sys.executable}\nprint('TEARDOWN OK')\n")
            fake.chmod(0o700)
            clean = {"HUB_ORIGIN": "", "HUB_TEST_PERSONAL_READ_TOKEN": "",
                     "PORTWRIGHT_HUB_VERIFY_INJECTED": "", "PORTWRIGHT_JEV_FIXTURES": "",
                     "PORTWRIGHT_JEV_RECORD": ""}
            with patch.object(self.module, "ROOT", root), patch.object(self.module, "OPSVC", fake), patch.dict(os.environ, clean):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = self.module.main(["--hub", "personal", "--scenario", "reachable"])
            self.assertEqual(code, 1)
            self.assertEqual(out.getvalue().strip(), "BLOCKED reachable missing-credentials")

    def test_invalid_run_id_fails_without_echoing_it(self):
        invalid = "a" * 35 + "-"
        result = self.run_cli("--hub", "public", "--scenario", "secrets", "--run-id", invalid)
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCKED", result.stdout)
        self.assertNotIn(invalid, result.stderr + result.stdout)

    def test_incompatible_hub_scenario_is_blocked(self):
        result = self.run_cli("--hub", "personal", "--scenario", "budget")
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCKED budget incompatible-hub", result.stdout)

    def test_synthetic_model_fixture_env_cannot_authorize_a_literal(self):
        env = {name: "synthetic" for name in self.module.required_env("public", "lifecycle")}
        env["PORTWRIGHT_JEV_FIXTURES"] = "synthetic"
        result = self.run_cli("--hub", "public", "--scenario", "lifecycle", env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCKED lifecycle synthetic-model", result.stdout)
        self.assertNotIn("LIFECYCLE OK", result.stdout)

    def test_forged_recovery_evidence_cannot_authorize_a_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "00000000-0000-0000-0000-000000000001"
            (Path(tmp) / (run_id + ".json")).write_text(json.dumps({"passed": True}))
            env = {name: "synthetic" for name in self.module.required_env("public", "lifecycle")}
            env["HUB_ORIGIN"] = "https://example.invalid"
            result = self.run_cli("--hub", "public", "--scenario", "lifecycle",
                                  "--run-id", run_id, "--evidence-dir", tmp, env=env)
            self.assertEqual(result.returncode, 1)
            self.assertIn("BLOCKED lifecycle recovery-state-invalid", result.stdout)
            self.assertNotIn("LIFECYCLE OK", result.stdout)

    # -- execute() contract ------------------------------------------------
    def test_teardown_failure_blocks_even_with_good_observations(self):
        class Runner:
            def setup(self): pass
            def exercise(self): return None
            def teardown(self): raise RuntimeError("failure with private data")
        code, message = self.module.execute("injection", Runner())
        self.assertEqual(code, 1)
        self.assertEqual(message, "FAIL injection teardown")
        self.assertNotIn(self.module.LITERALS["injection"], message)

    def test_unexpected_exception_never_echoes_payload_text(self):
        class Runner:
            def setup(self):
                raise RuntimeError("payload synthetic-marker")
            def exercise(self): return None
            def teardown(self): pass
        code, message = self.module.execute("lifecycle", Runner())
        self.assertEqual(code, 1)
        self.assertNotIn("synthetic-marker", message)

    def test_tail_seals_after_all_receipts_and_checks_shutdown_bytes(self):
        for mode in ("clean", "late", "shutdown", "missing", "partial"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                binary = root / "hub/node_modules/.bin/wrangler"
                binary.parent.mkdir(parents=True)
                binary.write_text(
                    f"#!{sys.executable}\n"
                    "import json,signal,sys,time\n"
                    "def emit(name, text=''):\n"
                    " print(json.dumps({'outcome':'ok','event':{'request':{'url':"
                    "'https://hub.test/'+name}},'logs':[text]},indent=2),flush=True)\n"
                    f"mode={mode!r}\n"
                    "def stop(*args):\n"
                    " if mode=='shutdown': emit('extra','synthetic-leak')\n"
                    " if mode=='partial': print('{',flush=True)\n"
                    " sys.exit(0)\n"
                    "signal.signal(signal.SIGTERM,stop)\n"
                    "emit('healthz?verify=start'); emit('healthz?verify=end')\n"
                    "time.sleep(0.7)\n"
                    "if mode!='missing': emit('mcp?verify=sample',"
                    "'synthetic-leak' if mode=='late' else '')\n"
                    "while True: time.sleep(0.1)\n")
                binary.chmod(0o755)
                with patch.dict(os.environ, {
                        "HUB_ORIGIN": "https://hub.test", "HUB_VERIFY_COMMONS": tmp,
                        "HUB_VERIFY_WORKER": "synthetic", "HUB_VERIFY_WRANGLER_CONFIG": "/dev/null",
                        "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic", "HUB_VERIFY_ACCOUNT_ID": "synthetic"}), \
                     patch.object(self.module, "ROOT", root):
                    runner = self.module.Hub("public", "secrets")
                    runner.start_tail()
                    urls = {"https://hub.test/" + path for path in
                            ("healthz?verify=start", "healthz?verify=end", "mcp?verify=sample")}
                    try:
                        if mode == "clean":
                            runner.seal_tail(urls, ["synthetic-leak"], timeout=2)
                            self.assertFalse(runner.tail_reader.is_alive())
                            self.assertIsInstance(runner.tail_snapshot, str)
                        else:
                            error = self.module.Failed if mode in ("late", "shutdown") else self.module.Blocked
                            with self.assertRaises(error):
                                runner.seal_tail(urls, ["synthetic-leak"], timeout=2)
                    finally:
                        runner.stop_tail()

    def test_r2_preexisting_unchanged_sample_is_not_a_verified_baseline(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        runner.r2_listing = lambda: {"old/object": "unchanged"}
        runner._r2_body = lambda key: b"synthetic-leak"
        with self.assertRaisesRegex(self.module.Failed, "secret-leaked-r2"):
            runner.check_r2_samples(["synthetic-leak"])
        runner._r2_body = lambda key: b"clean"
        pages = iter(({"old/object": "before"}, {"old/object": "after"}))
        runner.r2_listing = lambda: next(pages)
        with self.assertRaises(self.module.Blocked):
            runner.check_r2_samples(["synthetic-leak"])

    def test_r2_verified_baseline_accepts_empty_new_and_changed_clean_objects(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        listing, bodies = {}, {}
        runner.r2_listing = lambda: dict(listing)
        runner._r2_body = lambda key: bodies[key]
        baseline = runner.check_r2_samples(["synthetic-leak"])
        self.assertEqual(baseline, {})
        listing["current.json"], bodies["current.json"] = "v1", b'{"revision":1}'
        first = runner.check_r2_samples(["synthetic-leak"], baseline=baseline)
        self.assertEqual(first["current.json"], ("v1", hashlib.sha256(bodies["current.json"]).hexdigest()))
        self.assertEqual(runner.check_r2_samples(["synthetic-leak"], baseline=first), first)
        listing["current.json"], bodies["current.json"] = "v2", b'{"revision":2}'
        listing["new/note"], bodies["new/note"] = "new", b"clean note"
        final = runner.check_r2_samples(["synthetic-leak"], baseline=first)
        self.assertEqual(set(final), {"current.json", "new/note"})
        self.assertNotEqual(final["current.json"][1], first["current.json"][1])
        for key in listing:
            saved = bodies[key]
            bodies[key] = b"synthetic-leak"
            with self.subTest(key=key), self.assertRaisesRegex(self.module.Failed, "secret-leaked-r2"):
                runner.check_r2_samples(["synthetic-leak"], baseline=first)
            bodies[key] = saved

    def test_r2_unchanged_etag_requires_original_byte_digest(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        runner.r2_listing = lambda: {"old/object": "unchanged"}
        runner._r2_body = lambda key: b"\xff"
        baseline = runner.check_r2_samples([])
        runner._r2_body = lambda key: b"\xfe"
        with self.assertRaisesRegex(self.module.Blocked, "r2-baseline-mismatch"):
            runner.check_r2_samples([], baseline=baseline)

    def test_r2_scans_decoded_json_strings_not_only_raw_bytes(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        sample = "synthetic-leak"
        escaped = "".join(f"\\u{ord(char):04x}" for char in sample)
        runner.r2_listing = lambda: {"old/note-index.json": "etag"}
        runner._r2_body = lambda key: ('{"note":"' + escaped + '"}').encode()
        with self.assertRaisesRegex(self.module.Failed, "secret-leaked-r2"):
            runner.check_r2_samples([sample])

    def test_r2_missing_baseline_object_blocks_verification(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        listing = {"old/inventory.json": "etag"}
        runner.r2_listing = lambda: dict(listing)
        runner._r2_body = lambda key: b'{"clean":true}'
        baseline = runner.check_r2_samples(["synthetic-leak"])
        listing.clear()
        with self.assertRaisesRegex(self.module.Blocked, "r2-baseline-mismatch"):
            runner.check_r2_samples(["synthetic-leak"], baseline=baseline)

    def test_d1_secret_scan_includes_event_operation_and_intake_request_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            wire = Wire(tmp)
            wire._init_database()
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.d1 = wire.db
            runner.start_time = time.time() - 1
            runner.dynamic_tokens = {}
            token_hash = hashlib.sha256(wire.env_token("a").encode()).hexdigest()
            wire.db("INSERT INTO events(id,token_hash,kind,operation_id,payload,created)"
                    " VALUES('event',?,'report','synthetic-leak','{}',?)",
                    (token_hash, int(time.time())))
            with patch.dict(os.environ, {TOKEN_NAMES["a"]: wire.env_token("a")}):
                with self.assertRaisesRegex(self.module.Failed, "secret-leaked-d1"):
                    runner.secret_rows(["synthetic-leak"])
                wire.db("DELETE FROM events")
                wire.db("INSERT INTO intake(id,token_hash,request_digest,created)"
                        " VALUES('intake',?,'synthetic-leak',?)", (token_hash, int(time.time())))
                with self.assertRaisesRegex(self.module.Failed, "secret-leaked-d1"):
                    runner.secret_rows(["synthetic-leak"])

    def test_d1_secret_scan_covers_other_tokens_and_detects_incomplete_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            wire = Wire(tmp)
            wire._init_database()
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.start_time = time.time() - 1
            runner.d1 = wire.db
            owner = hashlib.sha256(wire.env_token("operator").encode()).hexdigest()
            now = int(time.time())
            wire.db("INSERT INTO events(id,token_hash,operation_id,payload,created)"
                    " VALUES('foreign',?,'synthetic-leak','{}',?)", (owner, now))
            with self.assertRaisesRegex(self.module.Failed, "secret-leaked-d1"):
                runner.secret_rows(["synthetic-leak"])
            wire.db("DELETE FROM events")
            for index in range(205):
                wire.db("INSERT INTO events(id,token_hash,operation_id,payload,created)"
                        " VALUES(?,?,?,'{}',?)", (f"event-{index:04}", owner,
                        "synthetic-leak" if index == 204 else "clean", now))
            with self.assertRaisesRegex(self.module.Failed, "secret-leaked-d1"):
                runner.secret_rows(["synthetic-leak"])
            def truncated(sql):
                rows = wire.db(sql)
                return rows[:1] if "operation_id" in sql else rows
            runner.d1 = truncated
            with self.assertRaisesRegex(self.module.Blocked, "d1-window-incomplete"):
                runner.secret_rows(["synthetic-leak"])

    def test_secrets_blocks_when_observability_field_is_absent(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        runner.deployment = lambda resource="settings": {"logpush": False, "tail_consumers": []}
        with self.assertRaisesRegex(self.module.Failed, "log-channel-exposed"):
            runner.scenario_secrets()

    def test_directory_not_found_after_recall_can_hide_the_old_package(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        target = "skill://gisul/commons/trial/github/notes/target.md"
        sibling = target.replace("target.md", "sibling.md")
        visible = [target, sibling]
        runner.rpc = lambda *args: (200, {"result": {
            "resources": [{"uri": uri} for uri in visible]}})
        remaining = runner.check_directory(target, "a" * 40, recalled=False)
        self.assertEqual(remaining, {sibling})
        visible.remove(target)
        runner.check_directory(target, "a" * 40, recalled=True, remaining=remaining)
        runner.rpc = lambda *args: (200, {"error": {"message": "NOT_FOUND"}})
        # §7.3 revokes the entire old package; the normal sibling is checked in
        # current note-index/HTTP reads, not guaranteed to survive on the old pin.
        runner.check_directory(target, "a" * 40, recalled=True, remaining=remaining)
        with self.assertRaises(self.module.Blocked):
            runner.check_directory(target, "a" * 40, recalled=True)

    @unittest.skipUnless(shutil.which("node"), "Node is required for actual H2 rejection")
    def test_secrets_scenario_executes_every_tool_field_and_seals_receipts(self):
        source = """
import {createInterface} from 'node:readline';
import {directFetch} from './hub/src/direct.ts';
let writes=0;
for await (const line of createInterface({input:process.stdin})) {
  const response=await directFetch(new Request('https://hub.test/mcp',{method:'POST',
    headers:{'content-type':'application/json'},body:Buffer.from(line,'base64')}),
    {HUB_AUDIENCE:'public',HUB_INTAKE_ENABLED:'true',HUB_DB:{prepare(){writes++;throw Error();}}},
    {scope:'submit'});
  process.stdout.write(JSON.stringify({code:response.status,text:await response.text(),writes})+'\\n');
}
"""
        child = subprocess.Popen(["node", "--experimental-transform-types", "--input-type=module",
                                  "-e", source], cwd=ROOT, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        module = self.module
        runner = module.Hub.__new__(module.Hub)
        runner.origin, runner.run_id = "https://hub.test", "matrix"
        runner.dynamic_tokens, runner.tail_events = {}, []
        runner.tail_broken = runner.tail_pending = False
        runner.tail = type("Live", (), {"poll": lambda self: None})()
        runner.stop_tail = lambda: None
        runner.deployment = lambda resource="settings": {"observability": None} if resource == "script-settings" else {}
        runner.issue_token = lambda: "synthetic"
        runner.token = lambda role: "synthetic-" + role
        runner.count = lambda *args: 0
        runner.admin = lambda *args: {}
        accepted = []
        def submit(*args):
            accepted.append(str(uuid.uuid4()))
            return {"intake_id": accepted[-1]}
        runner.submit = submit
        runner.drain = lambda *args: {"state": "published"}
        def receipt(path):
            runner._drain_events(json.dumps({"outcome": "ok",
                "event": {"request": {"url": runner.origin + path}}}), json.JSONDecoder())
        def request(method, path, role):
            receipt(path)
            return 200, {}
        runner.request = request
        runner.git_head = "a" * 40
        runner.git = lambda *args: b""
        runner.r2_listing = lambda: {}
        runner.setup = lambda: setattr(runner, "r2_baseline", runner.check_r2_samples([]))
        runner.exercise = runner.scenario_secrets
        runner.teardown = runner.stop_tail
        measurement = {"request_digest": "sha256:" + "a" * 64, "bytes": 12,
                       "reservation_id": "reservation", "status": "actual"}
        runner.start_time = time.time()
        runner.secret_rows = lambda *args: None
        runner.rows = lambda table, *args: (
            [{"id": identifier, "state": "published"} for identifier in accepted] if table == "intake" else
            [{"id": "reservation", "kind": kind, "intake_id": accepted[0],
              "payload": json.dumps(measurement), "value": 1} for kind in ("reservation", "cost")])
        cases = json.loads((ROOT / "tests/fixtures/hub/secrets-corpus.json").read_text())["cases"]
        samples = [module.assemble(case["parts"]) for case in cases if case["expect"] == "reject"]
        observed, damaged, urls, oversize = set(), 0, set(), set()
        def capture(method, path, role, *, raw):
            nonlocal damaged
            self.assertNotIn(path, urls)
            urls.add(path)
            child.stdin.write(base64.b64encode(raw).decode() + "\n")
            child.stdin.flush()
            reply = json.loads(child.stdout.readline())
            self.assertEqual(reply["writes"], 0, "rejection reached D1")
            try:
                document = json.loads(raw)
            except (ValueError, UnicodeError):
                damaged += 1
            else:
                tool = document["params"]["name"]
                if len(raw) > 16384:
                    for index, sample in enumerate(samples):
                        if sample in document["params"]["arguments"].get("unknown", ""):
                            oversize.add((tool, index))
                if len(raw) <= 16384:
                    tool = document["params"]["name"]
                    def visit(value, prefix):
                        if isinstance(value, dict):
                            for key, item in value.items():
                                for index, sample in enumerate(samples):
                                    if sample in key:
                                        observed.add((tool, "unknown-key", index))
                                visit(item, prefix + "." + key if prefix else key)
                        elif isinstance(value, str):
                            for index, sample in enumerate(samples):
                                if sample in value:
                                    observed.add((tool, prefix, index))
                    visit(document["params"]["arguments"], "")
                    visit(document["id"], "rpc-id")
                    if b"\\u" in raw:
                        for index, sample in enumerate(samples):
                            if sample == document["params"]["arguments"].get("unknown"):
                                observed.add((tool, "escaped-json", index))
            receipt(path)
            return reply["code"], reply["text"]
        runner.capture = capture
        try:
            code, message = module.execute("secrets", runner)
            self.assertEqual((code, message), (0, "SECRETS OK"))
            fields = {"submit_lesson": ("request_id", "service_id", "kind", "body", "doc_url",
                                       "success_evidence.action", "success_evidence.outcome",
                                       "note_id", "expected_revision", "rpc-id", "unknown",
                                       "unknown-key", "escaped-json"),
                      "confirm_lesson": ("note_id", "revision", "success_evidence.action",
                                        "success_evidence.outcome", "rpc-id", "unknown",
                                        "unknown-key", "escaped-json"),
                      "report_failure": ("note_id", "revision", "reason", "rpc-id", "unknown",
                                         "unknown-key", "escaped-json")}
            for tool, names in fields.items():
                for name in names:
                    for index in range(len(samples)):
                        self.assertIn((tool, name, index), observed)
            self.assertEqual(damaged, len(samples) * 3 * 2)
            self.assertEqual(oversize, {(tool, index) for tool in fields for index in range(len(samples))})
            self.assertEqual(len(urls), len(samples) * 37)
            self.assertEqual(len(runner.tail_events), len(urls) + 2)
            self.assertIsInstance(runner.tail_snapshot, str)
            expected = {event["event"]["request"]["url"] for event in runner.tail_events}
            runner.tail_events.pop(1)
            with self.assertRaisesRegex(module.Blocked, "tail-receipts-missing"):
                runner.seal_tail(expected, samples, timeout=0.01)
        finally:
            child.stdin.close()
            child.wait(timeout=10)
            child.stdout.close()
            child.stderr.close()

    def test_tail_receipt_uses_header_when_platform_redacts_query(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        runner.origin = "https://hub.test"
        runner.tail_broken = runner.tail_pending = False
        runner.tail = type("Tail", (), {"poll": lambda self: None})()
        runner.stop_tail = lambda: None
        marker = "12345678-1234-1234-1234-123456789abc-2"
        health = runner.origin + "/healthz?verify=start"
        target = runner.origin + "/mcp?verify=" + marker
        runner.tail_events = [
            {"outcome": "ok", "event": {"request": {"url": health}}},
            {"outcome": "ok", "event": {"request": {
                "url": runner.origin + "/mcp?verify=REDACTED",
                "headers": {"x-portwright-verify": marker}}}},
        ]
        runner.seal_tail({health, target}, [])
        self.assertEqual(len(runner.tail_events), 2)
        runner.tail_events[1]["event"]["request"]["headers"] = {}
        with self.assertRaisesRegex(self.module.Blocked, "tail-receipts-missing"):
            runner.seal_tail({health, target}, [], timeout=0.01)

    def test_overlap_requires_two_live_runs_and_pending_work(self):
        module = self.module
        def run(identifier, status="queued", conclusion=None):
            return {"id": identifier, "status": status, "conclusion": conclusion,
                    "event": "workflow_dispatch", "head_branch": "main"}
        for mode in ("complete_first", "cancelled", "collapsed", "work_finished"):
            runner = module.Hub.__new__(module.Hub)
            dispatched = []
            def dispatch():
                dispatched.append(len(dispatched) + 1)
                return 0
            runner.dispatch = dispatch
            def runs(previous):
                if len(dispatched) == 1:
                    return [run(1)]
                if mode == "collapsed":
                    return [run(1)]
                return [run(1, "completed", "cancelled" if mode == "cancelled" else "success")
                        if mode != "work_finished" else run(1), run(2)]
            runner.workflow_runs = runs
            ticks = iter(range(1000))
            with self.subTest(mode=mode), patch.object(module.time, "monotonic", lambda: next(ticks)), \
                 patch.object(module.time, "sleep", lambda _: None), self.assertRaises(module.Blocked):
                runner.overlapping_workflows(lambda: mode != "work_finished" or len(dispatched) < 2)
            self.assertEqual(dispatched, [1, 2])

    def test_overlap_completion_cannot_substitute_an_unobserved_run(self):
        module = self.module
        runner = module.Hub.__new__(module.Hub)
        for cancelled in (False, True):
            runner.workflow_runs = lambda _: [
                {"id": 1, "status": "completed", "conclusion": "success"},
                {"id": 2, "status": "completed" if cancelled else "queued",
                 "conclusion": "cancelled" if cancelled else None},
                {"id": 3, "status": "completed", "conclusion": "success"}]
            ticks = iter(range(0, 1000, 100))
            with self.subTest(cancelled=cancelled), patch.object(module.time, "monotonic", lambda: next(ticks)), \
                 patch.object(module.time, "sleep", lambda _: None), self.assertRaises(module.Blocked):
                runner.await_workflows(0, 2, run_ids={1, 2})

    def test_tail_parses_multiline_json_and_stderr_warning_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "hub" / "node_modules" / ".bin"
            binary.mkdir(parents=True)
            script = binary / "wrangler"
            script.write_text(
                "#!%s\n"
                "import json, sys, time\n"
                "print(json.dumps({'event': {'request': {'url':"
                " 'https://hub.test/healthz?verify=RUN-start'}}}, indent=4), flush=True)\n"
                "print('connection lost; reconnecting', file=sys.stderr, flush=True)\n"
                "time.sleep(30)\n" % sys.executable)
            script.chmod(0o755)
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.tail = None
            runner.tail_events = []
            runner.tail_broken = False
            runner.tail_pending = False
            runner.tail_reader = None
            runner.origin = "https://hub.test"
            with patch.dict(os.environ, {
                    "HUB_VERIFY_WORKER": "synthetic", "HUB_VERIFY_WRANGLER_CONFIG": "/dev/null",
                    "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic",
                    "HUB_VERIFY_ACCOUNT_ID": "synthetic", "HOME": tmp,
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin")}), \
                 patch.object(self.module, "ROOT", root):
                runner.start_tail()
                deadline = time.time() + 15
                while time.time() < deadline and not (runner.tail_events and runner.tail_broken):
                    time.sleep(0.05)
                urls = runner.tail_verify_urls()
                broken = runner.tail_broken
                # A stderr reconnect notice is a collection gap: never a complete window.
                with self.assertRaises(self.module.Blocked):
                    runner.await_tail_url("RUN-start")
                runner.tail.terminate()
                runner.tail.wait(timeout=10)
                runner.tail.stdout.close()
                runner.tail.stderr.close()
            self.assertEqual(urls, {"https://hub.test/healthz?verify=RUN-start"},
                             "multiline JSON is one incremental event with its full URL")
            self.assertTrue(broken, "any stderr warning marks the tail window incomplete")

    def test_tail_markers_are_exact_urls_not_prefixes(self):
        class Live:
            def poll(self): return None
            def is_alive(self): return True

        def event(query):
            return {"event": {"request": {"url": "https://hub.test/healthz?" + query}}}

        start_url = "https://hub.test/healthz?verify=RUN-start"
        end_url = "https://hub.test/healthz?verify=RUN-end"
        runner = self.module.Hub.__new__(self.module.Hub)
        runner.tail = Live()
        runner.tail_broken = False
        runner.tail_events = []
        runner.origin = "https://hub.test"
        self.assertEqual(runner.tail_verify_urls(), set())
        runner.tail_events = [event("verify=RUN-end")]
        self.assertEqual(runner.tail_verify_urls(), {end_url},
                         "an end marker never counts as the start marker")
        runner.tail_events = [event("verify=RUN-start")]
        self.assertEqual(runner.tail_verify_urls(), {start_url})
        runner.await_tail_url("RUN-start")

        def must_block(query):
            runner.tail_events = [event(query)]
            ticks = iter(range(0, 100000, 120))
            with patch.object(time, "monotonic", lambda: next(ticks)):
                with self.assertRaises(self.module.Blocked):
                    runner.await_tail_url("RUN-start")

        # Prefix or extra parameters are not the exact marker URL.
        must_block("verify=RUN-start-extra")
        must_block("verify=RUN-start&other=1")
        must_block("other=RUN-start")

    def test_tail_drain_flags_sampling_partial_and_non_object_frames(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        runner.tail_events = []
        runner.tail_broken = False
        runner.tail_pending = False
        decoder = json.JSONDecoder()
        left = runner._drain_events('{"event": {"request": ', decoder)
        self.assertEqual(runner.tail_events, [])
        self.assertTrue(runner.tail_pending, "a partial frame is pending, never an event")
        self.assertFalse(runner.tail_broken)
        runner._drain_events(left + '{"url": "u"}}}', decoder)
        self.assertEqual(runner.tail_events, [{"event": {"request": {"url": "u"}}}])
        self.assertFalse(runner.tail_pending)
        runner.tail_broken = False
        runner._drain_events(json.dumps({"samplingRate": 0.5}), decoder)
        self.assertTrue(runner.tail_broken, "a sampled stream cannot prove secret absence")
        runner.tail_broken = False
        runner._drain_events("[1, 2]", decoder)
        self.assertTrue(runner.tail_broken, "non-object frames never become events")
        runner.tail_broken = False
        runner._drain_events(json.dumps({"sampleRate": 1}), decoder)
        self.assertFalse(runner.tail_broken, "the unsampled control event stays complete")

    # -- helper boundaries -------------------------------------------------
    def test_rejected_response_checks_success_and_error_bodies(self):
        class Client:
            def __init__(self, result):
                self.result = result

            def capture(self, method, path, role, raw=None):
                return self.result

        sample = "synthetic-marker"
        self.module.check_rejected_response(
            Client((400, json.dumps({"code": "SECRET_REJECTED"}))), b"{}", sample)
        with self.assertRaises(self.module.Failed):
            self.module.check_rejected_response(
                Client((200, json.dumps({"intake_id": "accepted"}))), b"{}", sample)
        with self.assertRaises(self.module.Failed):
            self.module.check_rejected_response(Client((200, "ok")), b"{}", sample)
        with self.assertRaises(self.module.Failed):
            self.module.check_rejected_response(Client((400, "echo " + sample)), b"{}", sample)
        with self.assertRaises(self.module.Failed):
            self.module.check_rejected_response(
                Client((400, json.dumps({"echo": sample}))), b"{}", sample)

    def test_personal_listing_must_match_approved_resources(self):
        approved = {"name": "example", "files": [
            {"path": "SKILL.md", "sha256": "a" * 64, "bytes": 123},
            {"path": "notes.md", "sha256": "b" * 64, "bytes": 45}]}
        prefix = "skill://gisul/portwright/personal/example/"
        entry = {"uri": prefix + "SKILL.md",
                 "frontmatter": {"name": "example", "description": "A skill"},
                 "resources": [
                     {"uri": prefix + "SKILL.md",
                      "digest": "sha256:" + "a" * 64, "size": 123},
                     {"uri": prefix + "notes.md",
                      "digest": "sha256:" + "b" * 64, "size": 45}]}
        self.assertTrue(self.module.approved_personal_entry(entry, approved))
        entry["resources"][1]["digest"] = "sha256:" + "c" * 64
        self.assertFalse(self.module.approved_personal_entry(entry, approved))

    def test_visible_notes_uses_exact_note_revision_pairs(self):
        sync = {"notes": [{"note_id": "service/a", "revision": "sha256:" + "a" * 64},
                          {"note_id": "service/a", "revision": "sha256:" + "b" * 64},
                          {"note_id": "service/b", "revision": "sha256:" + "b" * 64}],
                "recalls": {"entries": [{"note_id": "service/a"}]}}
        visible = self.module.visible_notes(sync)
        self.assertEqual(visible, {("service/a", "sha256:" + "a" * 64),
                                   ("service/a", "sha256:" + "b" * 64),
                                   ("service/b", "sha256:" + "b" * 64)})
        # A recall entry alone must not hide a different revision of the same note.
        self.assertIn(("service/a", "sha256:" + "b" * 64), visible)
        for broken in ({"notes": {}}, {"notes": [{"note_id": 1, "revision": "x"}]},
                       {"notes": [{"note_id": "service/a"}]}):
            with self.subTest(broken=broken), self.assertRaises(self.module.Blocked):
                self.module.visible_notes(broken)

    def test_visible_notes_ignores_recall_projection_only_entries(self):
        revision = "sha256:" + "c" * 64
        sync = {"notes": [{"note_id": "service/a", "revision": revision}],
                "recalls": {"entries": [{"note_id": "service/a", "revision": revision}]}}
        self.assertEqual(self.module.visible_notes(sync), {("service/a", revision)})
        recalled_only = {"notes": [], "recalls": {"entries": [{"note_id": "service/a"}]}}
        self.assertEqual(self.module.visible_notes(recalled_only), set())

    def test_wrangler_env_is_allowlisted_and_disables_logging(self):
        with patch.dict(os.environ, {"WRANGLER_WRITE_LOGS": "true",
                                     "WRANGLER_SEND_METRICS": "true",
                                     "HUB_TEST_READ_TOKEN": "synthetic-secret",
                                     "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic-cf",
                                     "HUB_VERIFY_ACCOUNT_ID": "synthetic-account",
                                     "CLOUDFLARE_API_TOKEN": "stale", "HOME": "/tmp"}):
            env = self.module.wrangler_env()
        self.assertEqual(set(env), {"PATH", "HOME", "CLOUDFLARE_API_TOKEN",
                                    "CLOUDFLARE_ACCOUNT_ID", "WRANGLER_WRITE_LOGS",
                                    "WRANGLER_SEND_METRICS"})
        self.assertEqual(env["WRANGLER_WRITE_LOGS"], "false")
        self.assertEqual(env["WRANGLER_SEND_METRICS"], "false")
        self.assertEqual(env["CLOUDFLARE_API_TOKEN"], "synthetic-cf")
        self.assertNotIn("synthetic-secret", json.dumps(env))

    def test_d1_reader_refuses_writes_and_full_table_dumps(self):
        adapter = self.module.Hub.__new__(self.module.Hub)
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "HOME": "/tmp",
                                     "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic",
                                     "HUB_VERIFY_ACCOUNT_ID": "synthetic",
                                     "HUB_VERIFY_WRANGLER_CONFIG": "synthetic"}), \
             patch.object(self.module.subprocess, "run", side_effect=AssertionError("launched")):
            for sql in ("DELETE FROM intake", "SELECT * FROM intake",
                        "SELECT id FROM intake; DELETE FROM intake"):
                with self.subTest(sql=sql), self.assertRaises(self.module.Blocked):
                    adapter.d1(sql)

    def test_r2_listing_uses_official_pagination_and_slash_safe_keys(self):
        seen = []

        def opener(request, timeout=30):
            seen.append(request.full_url)
            if "cursor=next" in request.full_url:
                return _Response({"success": True,
                                  "result": [{"key": "note/service/two", "etag": "e2"}],
                                  "result_info": {"is_truncated": False, "cursor": None}})
            return _Response({"success": True,
                              "result": [{"key": "note/service/one", "etag": "e1"}],
                              "result_info": {"is_truncated": True, "cursor": "next"}})

        runner = self.module.Hub.__new__(self.module.Hub)
        with patch.dict(os.environ, {"HUB_VERIFY_BUCKET": "synthetic", "HUB_VERIFY_ACCOUNT_ID": "synthetic",
                                     "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic"}), \
             patch.object(self.module, "OPEN", opener):
            listing = runner.r2_listing()
        self.assertEqual(listing, {"note/service/one": "e1", "note/service/two": "e2"})
        self.assertIn("per_page=1000", seen[0])
        self.assertIn("cursor=next", seen[1])

    def test_r2_listing_blocks_unfinished_or_malformed_pagination(self):
        cases = [
            [{"success": True, "result": [{"key": "a", "etag": "e"}],
              "result_info": {"is_truncated": True, "cursor": "same"}},
             {"success": True, "result": [{"key": "a", "etag": "e"}],
              "result_info": {"is_truncated": True, "cursor": "same"}}],
            [{"success": True, "result": {"objects": [{"key": "a", "etag": "e"}]},
              "result_info": {"is_truncated": False}}],
            [{"success": True, "result": [{"key": "a"}],
              "result_info": {"is_truncated": False}}],
            [{"success": False, "result": [], "result_info": {"is_truncated": False}}],
            # No paging information on a full page cannot prove the listing ended.
            [{"success": True, "result": [{"key": f"k{index}", "etag": "e"} for index in range(1000)]}],
            # A cursor means more objects even when the page is short and the flag is absent.
            [{"success": True, "result": [{"key": "a", "etag": "e"}],
              "result_info": {"cursor": "next"}}],
            [{"success": True, "result": [{"key": "a", "etag": "e"}],
              "result_info": {"is_truncated": False, "cursor": "next"}}],
            [{"success": True, "result": [{"key": "a", "etag": "e"}], "result_info": {"is_truncated": "no"}}],
        ]
        for pages in cases:
            with self.subTest(pages=pages):
                stream = iter(pages)
                runner = self.module.Hub.__new__(self.module.Hub)
                with patch.dict(os.environ, {"HUB_VERIFY_BUCKET": "synthetic",
                                             "HUB_VERIFY_ACCOUNT_ID": "synthetic",
                                             "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic"}), \
                     patch.object(self.module, "OPEN",
                                  lambda request, timeout=30: _Response(next(stream))):
                    with self.assertRaises(self.module.Blocked):
                        runner.r2_listing()

    def test_r2_listing_accepts_short_final_page_without_result_info(self):
        # The live API omits result_info on a final page; the schema marks it optional.
        page = {"success": True, "result": [{"key": "recalls/current.json", "etag": "e1"}]}
        runner = self.module.Hub.__new__(self.module.Hub)
        with patch.dict(os.environ, {"HUB_VERIFY_BUCKET": "synthetic", "HUB_VERIFY_ACCOUNT_ID": "synthetic",
                                     "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic"}), \
             patch.object(self.module, "OPEN", lambda request, timeout=30: _Response(page)):
            self.assertEqual(runner.r2_listing(), {"recalls/current.json": "e1"})

    def test_r2_object_rejects_unreadable_or_invalid_bodies(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        with patch.dict(os.environ, {"HUB_VERIFY_BUCKET": "synthetic",
                                     "HUB_VERIFY_ACCOUNT_ID": "synthetic",
                                     "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic"}):
            with patch.object(self.module, "OPEN", side_effect=urllib.error.URLError("down")):
                with self.assertRaises(self.module.Blocked):
                    runner.r2_object("recalls/current.json")
            broken = _Response({"ok": True})
            broken._payload = b"<html>not json</html>"
            with patch.object(self.module, "OPEN", lambda request, timeout=30: broken):
                with self.assertRaises(self.module.Blocked):
                    runner.r2_object("recalls/current.json")

    def test_r2_object_keeps_slashes_and_decodes_json(self):
        seen = []

        def opener(request, timeout=30):
            seen.append(request.full_url)
            return _Response({"projection": {"entries": []}})

        runner = self.module.Hub.__new__(self.module.Hub)
        with patch.dict(os.environ, {"HUB_VERIFY_BUCKET": "synthetic", "HUB_VERIFY_ACCOUNT_ID": "synthetic",
                                     "HUB_VERIFY_CLOUDFLARE_TOKEN": "synthetic"}), \
             patch.object(self.module, "OPEN", opener):
            value = runner.r2_object("recalls/allocations/current.json")
        self.assertEqual(value, {"projection": {"entries": []}})
        self.assertIn("recalls/allocations/current.json", seen[0])
        self.assertNotIn("%2F", seen[0])

    def test_http_redirect_never_follows_to_another_origin(self):
        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/foreign" % target.server_port)
                self.end_headers()

            def log_message(self, *args):
                pass

        source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        for server in (source, target):
            Thread(target=server.serve_forever, daemon=True).start()
        try:
            request = urllib.request.Request("http://127.0.0.1:%d/mcp" % source.server_port,
                                             headers={"Authorization": "Bearer synthetic"})
            with self.assertRaises(self.module.Blocked):
                self.module.OPEN(request, timeout=3)
        finally:
            for server in (source, target):
                server.shutdown()
                server.server_close()

    def test_commons_revision_requires_git_content_not_http_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email",
                            "verifier@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name",
                            "Synthetic"], check=True)
            (root / "services").mkdir()
            (root / "services/example.md").write_text(
                '---\ngrade: trial\nrevision: "sha256:' + "a" * 64 + '"\n---\nSynthetic.\n')
            subprocess.run(["git", "-C", str(root), "add", "services/example.md"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "synthetic note"], check=True)
            subprocess.run(["git", "-C", str(root), "fetch", "-q", ".", "HEAD"], check=True)
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.commons = root
            self.assertIn("grade: trial", runner.expect_git_note("service/example",
                                                                 "sha256:" + "a" * 64, "trial"))
            with self.assertRaises(self.module.Failed):
                runner.expect_git_note("service/example", "sha256:" + "b" * 64, "trial")

    def test_recovery_manifest_holds_only_identifiers_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.hub = "public"
            runner.scenario = "lifecycle"
            runner.run_id = str(uuid.uuid4())
            runner.evidence_dir = Path(tmp)
            runner.created = ["00000000-0000-0000-0000-000000000001"]
            runner.test_tokens = ["a" * 64]
            runner.dynamic_tokens = {"a": "synthetic-private-value"}
            runner.budget_changed = False
            runner.unused_reservations = []
            runner.race_operation_ids = []
            runner.old_cap = 10000000
            runner.git_head = "b" * 40
            runner.save_state()
            record = json.loads((Path(tmp) / (runner.run_id + ".json")).read_text())
            self.assertNotIn("synthetic-private-value",
                             json.dumps(record))
            self.assertEqual(record["created"], runner.created)
            self.assertEqual(record["test_tokens"], runner.test_tokens)
            record["created"] = ["not-a-uuid"]
            (Path(tmp) / (runner.run_id + ".json")).write_text(json.dumps(record))
            recovered = self.module.Hub.__new__(self.module.Hub)
            recovered.hub, recovered.scenario = runner.hub, runner.scenario
            recovered.run_id, recovered.evidence_dir = runner.run_id, Path(tmp)
            with self.assertRaises(self.module.Blocked):
                recovered.load_state()

    def test_isolation_refuses_invalid_or_cross_kind_evidence(self):
        runner = self.module.Hub.__new__(self.module.Hub)
        runner.hub = "public"
        base = {"HUB_OTHER_ORIGIN": "https://other.test", "HUB_VERIFY_OTHER_AUDIENCE": "personal",
                "HUB_VERIFY_OTHER_OLD_PIN": "a" * 40, "HUB_VERIFY_OTHER_URI": "skill://gisul/other/x"}
        with patch.dict(os.environ, dict(base, HUB_VERIFY_OTHER_OLD_PIN="not-a-pin")):
            with self.assertRaises(self.module.Blocked):
                runner.scenario_isolation()
        with patch.dict(os.environ, dict(base, HUB_VERIFY_OTHER_AUDIENCE="public")):
            with self.assertRaises(self.module.Blocked):
                runner.scenario_isolation()
        runner.hub = "company"
        with patch.dict(os.environ, base):
            with self.assertRaises(self.module.Blocked):
                runner.scenario_isolation()

    def test_review_checkout_uses_the_fetched_commit_not_stale_local_main(self):
        def git(*args):
            run = subprocess.run(["git", *args], capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr.decode())
            return run.stdout
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, source, other = root / "bare.git", root / "commons", root / "other"
            git("init", "-q", "--bare", "-b", "main", str(bare))
            git("clone", "-q", str(bare), str(source))
            git("-C", str(source), "config", "user.email", "verifier@example.invalid")
            git("-C", str(source), "config", "user.name", "Synthetic")
            (source / "note.md").write_text("stale\n")
            git("-C", str(source), "add", "-A")
            git("-C", str(source), "commit", "-qm", "stale commit")
            git("-C", str(source), "push", "-q", "origin", "main")
            # The remote advances but the source worktree's local main does not.
            git("clone", "-q", str(bare), str(other))
            git("-C", str(other), "config", "user.email", "verifier@example.invalid")
            git("-C", str(other), "config", "user.name", "Synthetic")
            (other / "note.md").write_text("fetched\n")
            git("-C", str(other), "add", "-A")
            git("-C", str(other), "commit", "-qm", "fetched commit")
            git("-C", str(other), "push", "-q", "origin", "main")
            git("-C", str(source), "fetch", "-q", "origin", "main")
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.commons = source
            runner.review_checkout = None
            runner.temp_dirs = []
            checkout = runner._review_repo()
            try:
                for directory in runner.temp_dirs:
                    self.assertTrue(Path(directory).is_dir(), "review checkout is tracked for cleanup")
                self.assertEqual(Path(checkout, "note.md").read_text(), "fetched\n",
                                 "the reviewer clone must use the fetched commit, not stale main")
            finally:
                for directory in runner.temp_dirs:
                    shutil.rmtree(directory, ignore_errors=True)

    def test_teardown_removes_review_temp_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp, "review-clone")
            clone.mkdir()
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.tail = None
            runner.temp_dirs = [clone]
            runner.created = []
            runner.test_tokens = []
            runner.race_operation_ids = []
            runner.unused_reservations = []
            runner.budget_changed = False
            runner.teardown()
            self.assertFalse(clone.exists(), "teardown removes its own review checkouts")

    def test_evidence_directory_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp, "real")
            real.mkdir()
            link = Path(tmp, "link")
            link.symlink_to(real)
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.hub, runner.scenario, runner.run_id = "public", "budget", str(uuid.uuid4())
            runner.evidence_dir = link
            runner.created, runner.test_tokens = [], []
            runner.budget_changed, runner.unused_reservations = False, []
            runner.race_operation_ids, runner.old_cap, runner.git_head = [], None, None
            with self.assertRaises(self.module.Blocked):
                runner.save_state()

    # -- corpora -----------------------------------------------------------
    def test_corpus_matches_actual_screening_policy(self):
        sys.path.insert(0, str(ROOT / "lib"))
        from portwright.memory import SECRET_PATTERNS
        secrets = json.loads((ROOT / "tests/fixtures/hub/secrets-corpus.json").read_text())
        for case in secrets["cases"]:
            sample = self.module.assemble(case["parts"])
            with self.subTest(case=case["id"]):
                self.assertEqual(any(pattern.search(sample) for pattern in SECRET_PATTERNS),
                                 case["expect"] == "reject")

    def test_corpora_are_distinct_and_well_formed(self):
        secrets = json.loads((ROOT / "tests/fixtures/hub/secrets-corpus.json").read_text())
        injection = json.loads((ROOT / "tests/fixtures/hub/injection-corpus.json").read_text())
        self.assertEqual(secrets["schema_version"], 1)
        self.assertEqual(len({case["id"] for case in secrets["cases"]}), len(secrets["cases"]))
        positives = [case for case in secrets["cases"] if case["expect"] == "reject"]
        self.assertGreaterEqual(len(secrets["cases"]), 20)
        self.assertGreaterEqual(len(positives), 17)
        for case in secrets["cases"]:
            self.assertIsInstance(case["parts"], list)
        self.assertTrue(all(self.module.assemble(case["parts"]) for case in positives))
        self.assertEqual(injection["schema_version"], 1)
        self.assertTrue(injection.get("synthetic_evidence"))
        self.assertEqual(len({case["id"] for case in injection["cases"]}), len(injection["cases"]))
        kinds = {case["class"]: sum(other["class"] == case["class"] for other in injection["cases"])
                 for case in injection["cases"]}
        self.assertEqual(kinds, {"normal": 10, "injection": 10})
        for case in injection["cases"]:
            self.assertIsInstance(case["body"], str)
            self.assertTrue(case["doc_url"].startswith("https://"))
            self.assertIsInstance(case["success_evidence"], dict)

    def test_submitted_note_bodies_carry_current_utc_date(self):
        corpus = json.loads((ROOT / "tests/fixtures/hub/injection-corpus.json").read_text())
        captured = {}

        class FakeDate:
            def isoformat(self):
                return "2031-01-02"

        class FakeNow:
            def date(self):
                return FakeDate()

        class FakeDateTime:
            @staticmethod
            def now(tz):
                captured["tz"] = tz
                return FakeNow()

        runner = self.module.Hub.__new__(self.module.Hub)
        with patch.object(self.module, "datetime", FakeDateTime):
            body = runner.note(suffix="\nRecall control.\n")["body"]
            for case in corpus["cases"]:
                submitted = self.module.dated(case["body"])
                with self.subTest(case=case["id"]):
                    self.assertTrue(submitted.startswith("---\ndate: 2031-01-02\n"), case["id"])
        self.assertTrue(body.startswith("---\ndate: 2031-01-02\n"))
        self.assertIs(captured["tz"], datetime.timezone.utc)

    def test_injection_early_gate_or_missing_model_evidence_cannot_pass(self):
        for state, reason, receipts, failure in (
            ("held", "off_domain", [{"id": "receipt", "value": 10}], "injection-reason-mismatch"),
            ("held", "injection_suspected", [], "injection-gate-proof-missing"),
            # A gate ① identifier rejection has no model reservation and cannot pass.
            ("rejected", "identifier", [], "injection-gate-proof-missing"),
            ("published", None, [{"id": "receipt", "value": 10}], "injection-outcome-mismatch"),
        ):
            runner = self.module.Hub.__new__(self.module.Hub)
            runner.dynamic_tokens = {}
            runner.issue_token = lambda: "synthetic"
            runner.submit = lambda *args: {"intake_id": "00000000-0000-0000-0000-000000000001"}
            runner.drain = lambda *args, state=state, reason=reason: {"state": state, "reason_code": reason}
            runner.rows = lambda *args: receipts
            with self.subTest(reason=reason), self.assertRaisesRegex(self.module.Failed, failure):
                runner.scenario_injection()

    @unittest.skipUnless(shutil.which("node"), "Node is required for the actual H2 reader")
    def test_recall_directory_uses_actual_h2_pinned_response(self):
        fixture = ROOT / "hub/test/fixtures/release-v2"
        inventory = json.loads((fixture / "inventory.json").read_text())
        index = json.loads((fixture / "objects/note-index.json").read_text())
        target = next(note for note in index["notes"] if note["note_id"] == "service/candidate")
        # Native Node TypeScript: real H2 directFetch and reader, synthetic R2 bytes.
        source = """
import {readFile} from 'node:fs/promises';
import {directFetch} from './hub/src/direct.ts';
import {sha256} from './hub/src/r2-objects.ts';
const root = './hub/test/fixtures/release-v2/';
const text = await readFile(root+'inventory.json','utf8'), inventory=JSON.parse(text);
const identity={commit:inventory.commit,release:inventory.release,inventory_digest:await sha256(text)};
const objects=new Map();
for (const file of inventory.files) objects.set(`releases/${inventory.commit}/${file.path}`,
  await readFile(root+'objects/'+file.path));
objects.set(`releases/${inventory.commit}/inventory.json`,Buffer.from(text));
objects.set(`releases/${inventory.commit}/complete.json`,Buffer.from(JSON.stringify(identity)));
objects.set('current.json',Buffer.from(JSON.stringify({...identity,revision:1,sequence:1,
  high_water:{commit:identity.commit,sequence:1},previous:null,operation:'promote'})));
const recalls=JSON.parse(await readFile(root+'recalls.json','utf8'));
const index=JSON.parse(await readFile(root+'objects/note-index.json','utf8'));
const target=index.notes.find(n=>n.note_id==='service/candidate');
if (process.argv[1] === 'recalled') {
  recalls.entries.push({note_id:target.note_id,revision:target.revision}); recalls.sequence++;
}
objects.set('recalls/current.json',Buffer.from(JSON.stringify(recalls)));
const bucket={async get(key) {
  const value=objects.get(key);
  return value ? {size:value.length,etag:await sha256(value),json:async()=>JSON.parse(value),
    text:async()=>value.toString(),arrayBuffer:async()=>Uint8Array.from(value).buffer} : null;
}};
let raw=''; for await (const part of process.stdin) raw+=part;
const request=new Request('https://hub.test/mcp',{method:'POST',
  headers:{'content-type':'application/json'},body:raw});
const response=await directFetch(request,{HUB_AUDIENCE:'public',SKILLS_BUCKET:bucket},{scope:'read'});
console.log(JSON.stringify({status:response.status,body:await response.json()}));
"""
        recalled = False
        def rpc(method, params, role):
            request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            run = subprocess.run(["node", "--experimental-transform-types", "--input-type=module", "-e", source,
                                  "recalled" if recalled else "visible"],
                                 cwd=ROOT, input=json.dumps(request), capture_output=True, text=True,
                                 timeout=30, check=True)
            response = json.loads(run.stdout)
            return response["status"], response["body"]
        runner = self.module.Hub.__new__(self.module.Hub)
        runner.rpc = rpc
        remaining = runner.check_directory(target["uri"], inventory["commit"], recalled=False)
        recalled = True
        runner.check_directory(target["uri"], inventory["commit"], recalled=True, remaining=remaining)
        _, bad = rpc("resources/directory/read", {"uri": target["uri"].rsplit("/", 1)[0] + "/",
            "_meta": {"io.gisul/commit": inventory["commit"], "io.portwright/include_trial": True}}, "read")
        self.assertEqual(bad["error"]["message"], "INVALID_PARAMS")

    def test_wire_real_h4_cli_preflight_mcp_and_recall(self):
        result, run = self.wire("h4-probe")
        self.assertNotIn("harness_error", result, (result, run.stderr))
        self.assertTrue(result["probe"]["trial_stable_recall"])

    def test_wire_full_idempotency_scenario_and_duplicate_review_ack(self):
        for mode, expected in (("success", "IDEMPOTENCY OK"),
                               ("duplicate_ack", "FAIL idempotency duplicate-ack")):
            result, run = self.wire("idempotency", mode=mode)
            with self.subTest(mode=mode):
                self.assertNotIn("harness_error", result, (result, run.stderr))
                self.assertEqual(result["message"], expected, (result, run.stderr))
                self.assertEqual(result["code"], int(mode != "success"))
                self.assertTrue(result["summary"]["overlap_observed"])
                self.assertTrue(result["summary"]["pending_intake_overlap"])
                self.assertTrue(result["summary"]["pending_review_overlap"])

    # -- wire --------------------------------------------------------------
    def test_wire_budget_reaches_literal_only_after_teardown(self):
        # Synthetic wire only: the harness proves the verifier's own acceptance path.
        result, run = self.wire("budget")
        self.assertNotIn("harness_error", result, result)
        self.assertEqual(result["message"], self.module.LITERALS["budget"], (result, run.stderr))
        self.assertEqual(result["code"], 0)
        summary = result["summary"]
        self.assertIsNone(summary["override"], "budget override must be restored to null")
        self.assertEqual(summary["unsettled"], 0, "every synthetic reservation is settled")
        self.assertEqual(summary["recalls"], 1, "the reported revision is recalled")
        self.assertEqual(set(summary["states"]), {"published", "rejected"},
                         "base note published, pending intake rejected by teardown")
        self.assertGreaterEqual(summary["token_revoked"], 1, "issued tokens are revoked")
        self.assertIn("/admin/budget", [path for _, path in result["requests"]])
        self.assertIn("/sync", [path for _, path in result["requests"]])

    def test_wire_budget_rejects_a_400_reservation_race_without_a_literal(self):
        result, _ = self.wire("budget", mode="race_400")
        self.assertNotIn("harness_error", result, result)
        self.assertEqual(result["message"], "FAIL budget reservation-race-incorrect")
        self.assertEqual(result["code"], 1)

    def test_wire_budget_rejects_a_5xx_reservation_race_without_a_literal(self):
        result, _ = self.wire("budget", mode="race_500")
        self.assertNotIn("harness_error", result, result)
        self.assertEqual(result["message"], "FAIL budget reservation-race-incorrect")
        self.assertEqual(result["code"], 1)

    def test_wire_budget_without_d1_evidence_is_blocked_without_a_literal(self):
        result, _ = self.wire("budget", mode="d1_missing")
        self.assertNotIn("harness_error", result, result)
        self.assertTrue(result["message"].startswith("BLOCKED budget "), result["message"])
        self.assertIn("d1", result["message"])
        self.assertEqual(result["code"], 1)

    def test_wire_budget_teardown_failure_suppresses_the_literal(self):
        result, _ = self.wire("budget", mode="teardown_fail")
        self.assertNotIn("harness_error", result, result)
        self.assertEqual(result["message"], "FAIL budget teardown")
        self.assertEqual(result["code"], 1)

    def test_wire_capture_reads_error_bodies_and_echo_check_fails(self):
        result, run = self.wire("capture-probe")
        self.assertNotIn("harness_error", result, (result, run.stderr))
        probe = result["probe"]
        self.assertEqual(probe["code"], 400)
        self.assertTrue(probe["echoed"], "capture must surface the HTTPError body in memory")
        self.assertTrue(probe["clean_ok"], "a bounded 400 without the sample passes")
        self.assertTrue(probe["raised_on_echo"], "an echoed sample fails the rejection check")
        self.assertFalse(probe["accepted_2xx"], "a 200 response is never a secret refusal")

    def test_wire_idempotency_ack_and_overlapping_dispatches(self):
        result, run = self.wire("idempotency-probe")
        self.assertNotIn("harness_error", result, (result, run.stderr))
        probe = result["probe"]
        self.assertGreaterEqual(probe["runs"], 2, "both overlapping dispatches were observed")
        self.assertTrue(probe["overlap_observed"])
        self.assertEqual(probe["ack_found"], 1, "review ack is found by note_id/revision, not intake_id")
        self.assertEqual(probe["ack_absent"], 0)
        self.assertEqual(probe["initial_state"], "pending")
        self.assertRegex(probe["commit_after"], r"^[0-9a-f]{40}$",
                         "the published commit becomes the release identity")
        self.assertTrue(probe["intake_stable"], "state/published_commit unchanged across repeats")
        self.assertEqual(probe["short"], "workflow-incomplete",
                         "one completed run must not satisfy a two-run wait")


if __name__ == "__main__":
    unittest.main()
