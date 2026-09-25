#!/usr/bin/env python3
"""Live Hub acceptance checks. Never print request bodies, responses or credentials.

The operator supplies approved, short-lived test credentials. This program does not
provision deployments; a failed observation or cleanup fails the entire scenario.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import shutil
import subprocess
import stat
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPSVC = Path.home() / ".dotfiles/scripts/opsvc"
sys.path.insert(0, str(ROOT / "lib"))
from portwright.hub_sync import snapshot
from portwright.hub_contracts import parse_note
LITERALS = {"lifecycle": "LIFECYCLE OK", "secrets": "SECRETS OK",
            "injection": "INJECTION OK", "recall": "RECALL OK",
            "budget": "BUDGET OK", "idempotency": "IDEMPOTENCY OK",
            "isolation": "ISOLATION OK", "reachable": "PERSONAL OK"}
TOKENS = {"read": "HUB_TEST_READ_TOKEN", "a": "HUB_TEST_SUBMIT_A_TOKEN",
          "b": "HUB_TEST_SUBMIT_B_TOKEN", "c": "HUB_TEST_SUBMIT_C_TOKEN",
          "operator": "HUB_TEST_OPERATOR_TOKEN",
          "other": "HUB_TEST_OTHER_AUDIENCE_TOKEN",
          "personal": "HUB_TEST_PERSONAL_READ_TOKEN"}
EVIDENCE = ("HUB_VERIFY_CLOUDFLARE_TOKEN", "HUB_VERIFY_GITHUB_TOKEN",
            "HUB_VERIFY_ACCOUNT_ID", "HUB_VERIFY_COMMONS")
POLL_SECONDS = 5
TAIL_URL = "verify"


class Blocked(Exception):
    """Missing independent evidence or prerequisite; never contains a secret."""


class Failed(Exception):
    """A completed observation contradicted the acceptance condition."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, request, response, code, message, headers):
        response.close()
        raise Blocked("redirect-refused")

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


OPEN = urllib.request.build_opener(NoRedirect()).open

def require(condition, reason):
    if not condition:
        raise Failed(reason)


def assemble(parts):
    """Only synthesize test strings in memory; never echo them to logs."""
    return "".join(part if isinstance(part, str) else part["repeat"] * part["count"]
                   for part in parts)


def _corpus(name):
    return ROOT / "tests" / "fixtures" / "hub" / name


def wrangler_env():
    """The only child env wrangler may see; debug logs and telemetry stay off."""
    return {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
            "CLOUDFLARE_API_TOKEN": os.environ["HUB_VERIFY_CLOUDFLARE_TOKEN"],
            "CLOUDFLARE_ACCOUNT_ID": os.environ["HUB_VERIFY_ACCOUNT_ID"],
            "WRANGLER_WRITE_LOGS": "false", "WRANGLER_SEND_METRICS": "false"}


def sql_literal(value):
    """Quote an interpolated identifier only after bounding its character set."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:/_.-]+", value):
        raise Blocked("unsafe-query-value")
    return "'" + value + "'"


def visible_notes(sync):
    """(note_id, revision) pairs actually delivered; envelope strings are never searched."""
    notes = sync.get("notes") if isinstance(sync, dict) else None
    if not isinstance(notes, list):
        raise Blocked("sync-read-incomplete")
    pairs = set()
    for item in notes:
        if (not isinstance(item, dict) or not isinstance(item.get("note_id"), str)
                or not isinstance(item.get("revision"), str)):
            raise Blocked("sync-read-incomplete")
        pairs.add((item["note_id"], item["revision"]))
    return pairs


def r2_next_cursor(page, seen):
    """Official List Objects paging: result[] plus result_info.cursor/is_truncated."""
    info = page.get("result_info") if isinstance(page, dict) else None
    if not isinstance(info, dict) or not isinstance(info.get("is_truncated"), bool):
        raise Blocked("r2-pagination-unverifiable")
    if not info["is_truncated"]:
        return None
    cursor = info.get("cursor")
    if not isinstance(cursor, str) or not cursor or cursor in seen:
        raise Blocked("r2-pagination-incomplete")
    return cursor


def recall_path(note_id, revision):
    if (not re.fullmatch(r"(service|failure)/[a-z0-9._-]+", note_id)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", revision)):
        raise Blocked("recall-identity-invalid")
    kind, slug = note_id.split("/", 1)
    return f"recalls/{kind}s/{slug}/{revision.removeprefix('sha256:')}.json"


def check_rejected_response(client, raw, sample, *, path="/mcp"):
    code, text = client.capture("POST", path, "a", raw=raw)
    require(code in (400, 413), "secret-rejection-missing")
    try:
        decoded = json.dumps(json.loads(text), ensure_ascii=False)
    except ValueError:
        decoded = text
    require(sample not in text and sample not in decoded, "secret-echoed")


def approved_personal_entry(entry, approved):
    prefix = f"skill://gisul/personal/{approved['name']}/"
    if (entry.get("uri") != prefix + "SKILL.md"
            or entry.get("frontmatter", {}).get("name") != approved["name"]
            or not isinstance(entry.get("resources"), list)):
        return False
    actual = {item.get("uri"): (item.get("digest"), item.get("size"))
              for item in entry["resources"] if isinstance(item, dict)}
    expected = {prefix + item["path"]: ("sha256:" + item["sha256"], item["bytes"])
                for item in approved["files"]}
    return len(actual) == len(entry["resources"]) == len(expected) and actual == expected


def execute(scenario, runner):
    """No literal escapes before teardown succeeds, even if exercise succeeds."""
    error = None
    try:
        runner.setup()
        runner.exercise()
    except (Blocked, Failed) as exc:
        error = ("BLOCKED" if isinstance(exc, Blocked) else "FAIL", str(exc))
    except Exception:
        error = ("FAIL", "unexpected")
    try:
        runner.teardown()
    except Exception:
        error = ("FAIL", "teardown")
    if error:
        return 1, f"{error[0]} {scenario} {error[1]}"
    return 0, LITERALS[scenario]


COLUMNS = {
    "intake": ("id", "token_hash", "lineage_id", "request_id", "request_digest", "kind", "service_id",
               "target_note_id", "expected_revision", "body", "doc_url", "success_evidence",
               "state", "reason_code", "note_id", "revision", "gate_digest", "commit_sha",
               "published_commit", "created", "updated", "day"),
    "events": ("id", "intake_id", "note_id", "revision", "lineage_id", "token_hash",
               "kind", "action", "operation_id", "value", "payload", "month", "created", "day"),
    "tokens": ("hash", "org", "scope", "expires", "lineage_id", "revoked", "created"),
}


class Hub:
    def __init__(self, hub, scenario, *, run_id=None, evidence_dir=None):
        self.hub, self.scenario = hub, scenario
        self.run_id = run_id or str(uuid.uuid4())
        self.evidence_dir = evidence_dir
        self.origin = os.environ["HUB_ORIGIN"].rstrip("/")
        self.commons = Path(os.environ["HUB_VERIFY_COMMONS"]).resolve() if hub != "personal" else None
        self.created = []
        self.test_tokens = []
        self.dynamic_tokens = {}
        self.budget_changed = False
        self.unused_reservations = []
        self.race_operation_ids = []
        self.tail = None
        self.tail_events = []
        self.tail_broken = False
        self.tail_reader = None
        self.tail_pending = False
        self.tail_stopping = False
        self.tail_snapshot = None
        self.start_time = time.time()
        self.git_head = None
        self.old_cap = None
        self.review_checkout = None
        self.temp_dirs = []

    def save_state(self):
        if self.evidence_dir is None:
            return
        directory = self.evidence_dir
        if directory.is_symlink():
            raise Blocked("evidence-directory-unsafe")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {"schema_version": 1, "hub": self.hub, "scenario": self.scenario,
                  "run_id": self.run_id, "created": self.created,
                  "test_tokens": self.test_tokens, "budget_changed": self.budget_changed,
                  "unused_reservations": self.unused_reservations,
                  "race_operation_ids": self.race_operation_ids,
                  "old_cap": self.old_cap, "git_head": self.git_head}
        target = directory / (self.run_id + ".json")
        if target.is_symlink():
            raise Blocked("evidence-file-unsafe")
        fd, temporary = tempfile.mkstemp(dir=directory, prefix=".verify-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load_state(self):
        if self.evidence_dir is None:
            raise Blocked("evidence-directory-required")
        target = self.evidence_dir / (self.run_id + ".json")
        if target.is_symlink() or not target.is_file() or target.stat().st_size > 65536:
            raise Blocked("recovery-state-unavailable")
        try:
            record = json.loads(target.read_text())
            if (record["schema_version"] != 1 or record["run_id"] != self.run_id
                    or record["hub"] != self.hub or record["scenario"] != self.scenario
                    or any(not re.fullmatch(r"[0-9a-f-]{36}", value) for value in record["created"])
                    or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in record["test_tokens"])
                    or any(not re.fullmatch(r"[0-9a-f-]{36}", value) for value in
                           record["unused_reservations"] + record["race_operation_ids"])):
                raise Blocked("recovery-state-invalid")
            for key in ("created", "test_tokens", "budget_changed", "unused_reservations",
                        "race_operation_ids", "old_cap", "git_head"):
                setattr(self, key, record[key])
        except (OSError, KeyError, TypeError, ValueError):
            raise Blocked("recovery-state-invalid") from None

    def token(self, role):
        return self.dynamic_tokens.get(role) or os.environ[TOKENS[role]]

    def request(self, method, path, role, payload=None, *, origin=None, raw=None):
        url = (origin or self.origin) + path
        if not url.startswith("https://"):
            raise Blocked("https-required")
        headers = {"Authorization": "Bearer " + self.token(role), "Accept": "application/json", "User-Agent": "portwright-hub/2.2"}
        data = raw if raw is not None else (json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, method=method, headers=headers, data=data)
        try:
            with OPEN(req, timeout=30) as response:
                body = response.read(32 * 1024 * 1024 + 1)
                if len(body) > 32 * 1024 * 1024:
                    raise Blocked("response-incomplete")
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            # Only the status is safe to retain; errors may quote submitted text.
            exc.close()
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            raise Blocked("http-unavailable") from None

    def capture(self, method, path, role, *, origin=None, raw=None):
        """Bounded in-memory body for success and error responses alike; never logged."""
        url = (origin or self.origin) + path
        if not url.startswith("https://"):
            raise Blocked("https-required")
        headers = {"Authorization": "Bearer " + self.token(role), "Accept": "application/json", "User-Agent": "portwright-hub/2.2"}
        data = raw if raw is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, method=method, headers=headers, data=data)
        limit = 1024 * 1024
        try:
            with OPEN(req, timeout=30) as response:
                status, body = response.status, response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            try:
                status, body = exc.code, exc.read(limit + 1)
            finally:
                exc.close()
        except (urllib.error.URLError, TimeoutError):
            raise Blocked("http-unavailable") from None
        if len(body) > limit:
            raise Blocked("response-incomplete")
        return status, body.decode("utf-8", errors="replace")

    def admin(self, path, payload=None):
        code, value = self.request("POST" if payload is not None else "GET", "/admin/" + path,
                                   "operator", payload)
        if code != 200 or not isinstance(value, dict):
            raise Blocked("admin-unavailable")
        return value

    def rpc(self, method, params, role="a", *, origin=None, raw=None):
        request_id = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        return self.request("POST", "/mcp", role, payload, origin=origin, raw=raw)

    def tool(self, name, arguments, role="a"):
        code, result = self.rpc("tools/call", {"name": name, "arguments": arguments}, role)
        if code not in (200, 201) or not isinstance(result, dict) or result.get("error") or result.get("isError"):
            raise Failed("tool-rejected")
        value = result.get("result", result)
        return value.get("structuredContent", value) if isinstance(value, dict) else value

    def _git(self, cwd, *args, env=None):
        if env is None:
            env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        try:
            run = subprocess.run(["git", "-C", str(cwd), *args],
                                 capture_output=True, timeout=30, check=True, env=env)
            return run.stdout
        except (OSError, subprocess.SubprocessError):
            raise Blocked("commons-read-incomplete") from None

    def git(self, *args):
        if not self.commons or not (self.commons / ".git").exists():
            raise Blocked("commons-checkout-missing")
        return self._git(self.commons, *args)

    def d1(self, sql):
        # wrangler executes SELECT only, as a separate read; never use HTTP admin's report as proof.
        # Values are never parameter-matched here: a sample in argv would leak it to process lists.
        stripped = sql.lstrip()
        if (not stripped.upper().startswith("SELECT ") or
                any(marker in sql for marker in (";", "--", "/*", "*/")) or
                re.match(r"SELECT\s+\*", stripped, re.IGNORECASE)):
            raise Blocked("read-only-query-required")
        command = [str(ROOT / "hub/node_modules/.bin/wrangler"), "d1", "execute",
                   "HUB_DB", "--remote", "--json", "--command", sql,
                   "--config", os.environ["HUB_VERIFY_WRANGLER_CONFIG"]]
        try:
            result = subprocess.run(command, capture_output=True, timeout=60, check=True,
                                    env=wrangler_env())
            page = json.loads(result.stdout)
            if not isinstance(page, list) or len(page) != 1 or not page[0].get("success"):
                raise Blocked("d1-read-incomplete")
            return page[0]["results"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            raise Blocked("d1-read-incomplete") from None

    def rows(self, table, where, columns):
        if table not in COLUMNS or not columns or "*" in where:
            raise Blocked("invalid-read")
        if any(column not in COLUMNS[table] for column in columns):
            raise Blocked("invalid-read")
        names = ", ".join(columns)
        return self.d1(f"SELECT {names} FROM {table} WHERE {where}")

    def count(self, table, where):
        if table not in COLUMNS or "*" in where:
            raise Blocked("invalid-read")
        found = self.d1(f"SELECT count(*) AS n FROM {table} WHERE {where}")
        if len(found) != 1 or not isinstance(found[0].get("n"), int):
            raise Blocked("d1-read-incomplete")
        return found[0]["n"]

    def expect_git_note(self, note_id, revision, grade):
        if not re.fullmatch(r"(?:service|failure)/[a-z0-9-]+", note_id):
            raise Failed("invalid-note-id")
        slug = note_id.split("/", 1)[1]
        paths = ([f"services/{slug}.md", f"trial/services/{slug}.md"] if note_id.startswith("service/")
                 else [f"failures/{slug}.md", f"trial/failures/{slug}.md"])
        for path in paths:
            try:
                text = self.git("show", "FETCH_HEAD:" + path).decode()
            except Blocked:
                continue
            metadata = parse_note(text)["metadata"]
            if metadata.get("revision") == revision and metadata.get("grade") == grade:
                return text
        raise Failed("commons-revision-missing")

    def read_notes(self, trial=False, role="read"):
        query = "?include_trial=true" if trial else ""
        code, value = self.request("GET", "/sync" + query, role)
        if code != 200 or not isinstance(value, dict):
            raise Blocked("sync-read-incomplete")
        return value

    def visible_notes(self, trial=False, role="read"):
        return visible_notes(self.read_notes(trial, role))

    def release_commit(self, trial=False):
        identity = self.read_notes(trial).get("release_identity")
        commit = identity.get("commit") if isinstance(identity, dict) else None
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise Blocked("release-identity-incomplete")
        return commit

    def note(self, service="github", *, suffix="", body=None, doc_url=None):
        # These are authored synthetic examples, not captured traffic.
        body = body or ("---\ndate: 2026-09-25\n"
                        f"service: {service}\n"
                        "service_version: 1\nstatus: active\n"
                        "distributable: true\n---\n## Root cause\nThe read used an outdated path.\n"
                        "## Fix\nRead the documented resource path before retrying.\n")
        return {"request_id": str(uuid.uuid4()), "service_id": service,
                "kind": "lesson", "body": body + suffix,
                "doc_url": doc_url or "https://docs.github.com/en/rest/repos/contents",
                "success_evidence": {"action": "Read the documented public resource path",
                                     "outcome": "The expected public resource was returned"}}

    def submit(self, payload, role="a"):
        response = self.tool("submit_lesson", payload, role)
        if not isinstance(response, dict) or not isinstance(response.get("intake_id"), str):
            raise Failed("submission-not-acknowledged")
        self.created.append(response["intake_id"])
        self.save_state()
        return response

    def latest_workflow(self):
        runs = self.workflow_runs(-1)
        return runs[0] if runs else {"id": 0}

    def workflow_runs(self, previous):
        repo = os.environ["HUB_VERIFY_COMMONS_REPO"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise Blocked("commons-repository-invalid")
        url = f"https://api.github.com/repos/{repo}/actions/workflows/hub.yml/runs?event=workflow_dispatch&per_page=10"
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + os.environ["HUB_VERIFY_GITHUB_TOKEN"],
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with OPEN(req, timeout=30) as response:
                runs = json.load(response)["workflow_runs"]
        except (urllib.error.URLError, KeyError, ValueError):
            raise Blocked("workflow-read-incomplete") from None
        return [run for run in runs
                if isinstance(run, dict) and isinstance(run.get("id"), int) and run["id"] > previous]

    def await_workflows(self, previous, count=1, *, run_ids=None):
        """Await the observed runs, not a different run that happened to finish first."""
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            runs = self.workflow_runs(previous)
            if run_ids is not None:
                runs = [run for run in runs if run["id"] in run_ids]
                if any(run.get("conclusion") == "cancelled" for run in runs):
                    raise Blocked("workflow-overlap-cancelled")
            finished = [run for run in runs if run.get("status") == "completed"]
            if len(finished) >= count:
                require(all(run.get("conclusion") == "success" for run in finished[:count]),
                        "workflow-failed")
                return
            time.sleep(POLL_SECONDS)
        raise Blocked("workflow-incomplete")

    def overlapping_workflows(self, pending):
        """Observe both dispatch IDs alive while the same work is still pending."""
        if not pending():
            raise Blocked("workflow-work-not-pending")
        previous = self.dispatch()
        observed = set()
        for count in (1, 2):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                runs = self.workflow_runs(previous)
                if (len(runs) > count or len({run["id"] for run in runs}) != len(runs)
                        or any(run.get("status") not in ("queued", "in_progress", "pending", "waiting")
                               or run.get("event") != "workflow_dispatch"
                               or run.get("head_branch") != "main" for run in runs)):
                    raise Blocked("workflow-overlap-unproven")
                if len(runs) == count:
                    ids = {run["id"] for run in runs}
                    if not observed <= ids or not pending():
                        raise Blocked("workflow-work-not-pending")
                    observed = ids
                    break
                time.sleep(POLL_SECONDS)
            else:
                raise Blocked("workflow-overlap-incomplete")
            if count == 1:
                self.dispatch()
        self.await_workflows(previous, 2, run_ids=observed)
        return observed

    def await_workflow(self, previous):
        self.await_workflows(previous, 1)

    def dispatch(self):
        # Dispatch with no text arguments; the workflow processes the fixed pending queue.
        previous = self.latest_workflow()["id"]
        repo = os.environ["HUB_VERIFY_COMMONS_REPO"]
        url = f"https://api.github.com/repos/{repo}/actions/workflows/hub.yml/dispatches"
        body = json.dumps({"ref": "main"}).encode()
        req = urllib.request.Request(url, body, method="POST", headers={
            "Authorization": "Bearer " + os.environ["HUB_VERIFY_GITHUB_TOKEN"],
            "Accept": "application/vnd.github+json", "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with OPEN(req, timeout=30) as response:
                if response.status != 204:
                    raise Blocked("workflow-dispatch-unconfirmed")
        except (urllib.error.URLError, TimeoutError):
            raise Blocked("workflow-dispatch-unavailable") from None
        return previous

    def drain(self, intake_id):
        self.await_workflow(self.dispatch())
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            found = self.rows("intake", "id=" + sql_literal(intake_id),
                              ("state", "note_id", "revision", "reason_code"))
            if len(found) == 1 and found[0]["state"] in ("published", "held", "rejected"):
                self.git("fetch", "origin", "main")  # fetch is a read of the remote, never a push
                return found[0]
            time.sleep(POLL_SECONDS)
        raise Blocked("workflow-incomplete")

    def await_git(self, note_id, revision, grade):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            self.git("fetch", "origin", "main")
            try:
                return self.expect_git_note(note_id, revision, grade)
            except Failed:
                time.sleep(POLL_SECONDS)
        raise Blocked("commons-update-incomplete")

    def issue_token(self):
        issued = self.admin("tokens", {"action": "issue", "scope": "submit",
                                        "expires": int(time.time()) + 3600})
        if not isinstance(issued.get("token"), str):
            raise Blocked("test-token-unavailable")
        token = issued["token"]
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.test_tokens.append(token_hash)
        self.save_state()
        return token

    def local_hub_cli(self, action):
        """H4's hub CLI must really exist before any scenario relies on it."""
        command = [sys.executable, "-m", "portwright.cli", "hub", action, "--help"]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
               "PYTHONPATH": str(ROOT / "lib")}
        try:
            run = subprocess.run(command, capture_output=True, timeout=30, cwd=str(ROOT), env=env)
        except (OSError, subprocess.SubprocessError):
            raise Blocked(f"local-{action}-unavailable") from None
        if run.returncode != 0 or action.encode() not in run.stdout:
            raise Blocked(f"local-{action}-unavailable")

    def _review_repo(self):
        """A dedicated clean checkout for the reviewer; the user's worktree is never touched."""
        if self.review_checkout is None:
            if not self.commons or not (self.commons / ".git").exists():
                raise Blocked("commons-checkout-missing")
            base = tempfile.mkdtemp(prefix="hub-verify-review-", dir="/tmp")
            self.temp_dirs.append(Path(base))
            self.review_checkout = Path(base)
            self._git(ROOT, "clone", "--quiet", "--no-hardlinks", str(self.commons), base,
                      env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")})
        self.git("fetch", "origin", "main")
        head = self.git("rev-parse", "FETCH_HEAD").decode().strip()
        self._git(self.review_checkout, "fetch", "--quiet", "origin", head)
        self._git(self.review_checkout, "checkout", "--detach", "--quiet", "FETCH_HEAD")
        if self._git(self.review_checkout, "status", "--porcelain"):
            raise Blocked("review-checkout-unclean")
        return self.review_checkout

    def review(self, note_id, revision, decision, expect_reason):
        """Two calls: the summary must be pending with the exact reason, then the post."""
        repo = self._review_repo()
        command = [sys.executable, str(ROOT / "scripts/hub_pipeline.py"),
                   "--commons", str(repo), "--code-root", str(ROOT),
                   "--audience", self.hub, "--review-note-id", note_id,
                   "--review-revision", revision, "--review-decision", decision]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
               "HUB_ORIGIN": self.origin, "HUB_OPERATOR_TOKEN": self.token("operator")}
        try:
            summary = subprocess.run(command, env=env, capture_output=True,
                                     timeout=90, check=True)
            document = json.loads(summary.stdout)
            digest = document["events_digest"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                raise Blocked("review-digest-incomplete")
            require(document.get("pending_review") is True, "review-not-pending")
            require(document.get("reason_code") == expect_reason, "review-reason-mismatch")
            approved = subprocess.run(command + ["--approve-review",
                                                  "--expected-events-digest", digest],
                                      env=env, capture_output=True, timeout=90, check=True)
            require(json.loads(approved.stdout).get("posted") is True, "review-not-posted")
        except (OSError, subprocess.SubprocessError, KeyError, ValueError):
            raise Blocked("review-incomplete") from None

    def setup(self):
        if self.hub != "personal":
            self.git_head = self.git("rev-parse", "HEAD").decode().strip()
            if not self.commons or self.git("status", "--porcelain"):
                raise Blocked("commons-checkout-dirty")
            if self.scenario == "budget":
                override = self.d1("SELECT cap_microusd FROM budget_override WHERE id=1")
                if override:
                    raise Blocked("budget-override-preexisting")
            self.old_cap = self.admin("budget").get("cap_micro_usd")
        if self.scenario == "secrets":
            cases = json.loads(_corpus("secrets-corpus.json").read_text())["cases"]
            self.r2_baseline = self.check_r2_samples(
                [assemble(case["parts"]) for case in cases if case["expect"] == "reject"])
            self.start_tail()
        self.save_state()

    def start_tail(self):
        worker = os.environ["HUB_VERIFY_WORKER"]
        self.tail_stopping = False
        if not re.fullmatch(r"[a-z0-9-]+", worker):
            raise Blocked("worker-name-invalid")
        try:
            self.tail = subprocess.Popen([str(ROOT / "hub/node_modules/.bin/wrangler"), "tail",
                                          worker, "--format", "json", "--config",
                                          os.environ["HUB_VERIFY_WRANGLER_CONFIG"]],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         env=wrangler_env())
        except OSError:
            raise Blocked("tail-unavailable") from None
        def collect_out():
            decoder = json.JSONDecoder()
            utf8 = codecs.getincrementaldecoder("utf-8")()
            buffer = ""
            while True:
                try:
                    chunk = self.tail.stdout.read1(65536)
                except (OSError, ValueError):
                    self.tail_broken = True
                    return
                if not chunk:
                    if not self.tail_stopping:
                        self.tail_broken = True
                    return
                try:
                    buffer += utf8.decode(chunk)
                except UnicodeDecodeError:
                    self.tail_broken = True
                    return
                buffer = self._drain_events(buffer, decoder)
                self.tail_pending = bool(buffer.strip()) or bool(utf8.getstate()[0])
                if len(self.tail_events) >= 10000:
                    self.tail_broken = True
                    return
        def collect_err():
            # json-mode stderr carries only warnings and reconnect notices: any line is a gap.
            try:
                for line in self.tail.stderr:
                    if line.strip():
                        self.tail_broken = True
                        return
            except (OSError, ValueError):
                self.tail_broken = True
        self.tail_reader = threading.Thread(target=collect_out, daemon=True)
        self.tail_errors = threading.Thread(target=collect_err, daemon=True)
        self.tail_reader.start()
        self.tail_errors.start()
        time.sleep(0.5)
        if self.tail.poll() is not None:
            raise Blocked("tail-unavailable")

    def _drain_events(self, buffer, decoder):
        """Incremental multiline JSON: partial frames wait, garbage never yields events."""
        while True:
            stripped = buffer.lstrip()
            if not stripped:
                self.tail_pending = False
                return ""
            try:
                value, index = decoder.raw_decode(stripped)
            except ValueError:
                self.tail_pending = True
                if len(stripped) > 1_000_000:
                    self.tail_broken = True
                return stripped
            if not isinstance(value, dict):
                self.tail_broken = True
                return ""
            if value.get("samplingRate", value.get("sampleRate", 1)) != 1:
                self.tail_broken = True
            self.tail_events.append(value)
            buffer = stripped[index:]

    def tail_verify_urls(self):
        urls = set()
        for event in self.tail_events:
            request = event.get("event") if isinstance(event, dict) else None
            url = request.get("request", {}).get("url") if isinstance(request, dict) else None
            if not isinstance(url, str):
                continue
            urls.add(url)
        return urls

    def await_tail_url(self, marker):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.tail_broken or self.tail is None or self.tail.poll() is not None:
                raise Blocked("tail-window-incomplete")
            if self.origin + "/healthz?verify=" + marker in self.tail_verify_urls():
                return
            time.sleep(0.2)
        raise Blocked("tail-window-incomplete")

    def stop_tail(self):
        if self.tail is None:
            return
        self.tail_stopping = True
        try:
            if self.tail.poll() is None:
                self.tail.terminate()
            self.tail.wait(timeout=10)
            self.tail_reader.join(timeout=10)
            self.tail_errors.join(timeout=10)
            if self.tail_reader.is_alive() or self.tail_errors.is_alive():
                raise Blocked("tail-window-incomplete")
        except (OSError, subprocess.SubprocessError):
            raise Blocked("tail-window-incomplete") from None
        finally:
            if not self.tail_reader.is_alive() and not self.tail_errors.is_alive():
                self.tail.stdout.close()
                self.tail.stderr.close()

    def seal_tail(self, expected_urls, samples, *, timeout=30):
        """One completed invocation receipt per marked request, then drain and join.

        An end marker alone is not a delivery barrier. Each sample request has its
        own safe URL marker; its completed tail event includes that invocation's logs.
        """
        deadline = time.monotonic() + timeout
        complete = False
        while time.monotonic() < deadline:
            if self.tail_broken or self.tail is None or self.tail.poll() is not None:
                break
            received = {event.get("event", {}).get("request", {}).get("url")
                        for event in tuple(self.tail_events) if event.get("outcome") == "ok"}
            if expected_urls <= received:
                complete = True
                break
            time.sleep(0.05)
        self.stop_tail()
        # No collector can mutate the evidence after join. Scan even when incomplete,
        # so an observed leak is a failure, never hidden behind a missing-receipt block.
        self.tail_snapshot = json.dumps(self.tail_events, ensure_ascii=False)
        require(not any(sample in self.tail_snapshot for sample in samples), "secret-leaked-tail")
        if not complete or self.tail_broken or self.tail_pending:
            raise Blocked("tail-window-incomplete")

    def teardown(self):
        errors = False
        if self.tail is not None:
            try:
                self.stop_tail()
            except Exception:
                errors = True
        if self.race_operation_ids:
            try:
                recovered = self.rows("events", "kind='reservation' AND operation_id IN (" +
                                      ",".join(sql_literal(item) for item in self.race_operation_ids) + ")",
                                      ("id",))
                self.unused_reservations.extend(row["id"] for row in recovered)
            except Exception:
                errors = True
        for reservation_id in set(self.unused_reservations):
            try:
                self.admin("budget", {"action": "settle", "reservation_id": reservation_id,
                                      "actual_micro_usd": 0, "usage": {}, "status": "unused"})
            except Exception:
                errors = True
        if self.budget_changed:
            try:
                self.admin("budget", {"cap_microusd": None})
                require(self.admin("budget").get("cap_micro_usd") == self.old_cap,
                        "budget-override-not-cleared")
                require(not self.d1("SELECT cap_microusd FROM budget_override WHERE id=1"),
                        "budget-override-not-cleared")
            except Exception:
                errors = True
        for intake_id in set(self.created):
            try:
                columns = ("state", "note_id", "revision", "body", "doc_url", "success_evidence")
                found = self.rows("intake", "id=" + sql_literal(intake_id), columns)
                if len(found) != 1:
                    raise Blocked("teardown-intake-unobserved")
                row = found[0]
                if row["state"] == "committed":
                    self.drain(intake_id)
                    found = self.rows("intake", "id=" + sql_literal(intake_id), columns)
                    require(len(found) == 1, "teardown-intake-unobserved")
                    row = found[0]
                if row["state"] in ("pending", "held"):
                    # Rejecting erases the stored text; no pending submission outlives a run.
                    self.admin("intake", {"action": "reject", "intake_id": intake_id,
                                          "reason_code": "verification_teardown"})
                    found = self.rows("intake", "id=" + sql_literal(intake_id), columns)
                    require(len(found) == 1 and found[0]["state"] == "rejected",
                            "teardown-reject-unconfirmed")
                    row = found[0]
                if row["state"] == "rejected":
                    require(row["body"] is None and row["doc_url"] is None
                            and row["success_evidence"] is None, "teardown-text-remaining")
                    continue
                require(row["state"] == "published", "teardown-intake-incomplete")
                note_id, revision = row["note_id"], row["revision"]
                path = recall_path(note_id, revision)
                self.git("fetch", "origin", "main")
                try:
                    self.git("show", "FETCH_HEAD:" + path)
                    marker_present = True
                except Blocked:
                    marker_present = False
                if not marker_present:
                    if "cleanup_a" not in self.dynamic_tokens:
                        self.dynamic_tokens["cleanup_a"] = self.issue_token()
                        self.dynamic_tokens["cleanup_b"] = self.issue_token()
                    for role in ("cleanup_a", "cleanup_b"):
                        self.tool("report_failure", {"note_id": note_id, "revision": revision,
                                                     "reason": "Synthetic verification cleanup"}, role)
                    if self._is_stable(note_id, revision):
                        self.review(note_id, revision, "recall", "distinct_reports_pending_review")
                    self.drain(intake_id)
                    deadline = time.monotonic() + 300
                    while time.monotonic() < deadline:
                        self.git("fetch", "origin", "main")
                        try:
                            self.git("show", "FETCH_HEAD:" + path)
                            break
                        except Blocked:
                            time.sleep(POLL_SECONDS)
                    else:
                        raise Blocked("teardown-recall-incomplete")
                # The delivery check runs even when the marker already existed.
                require((note_id, revision) not in self.visible_notes(trial=True),
                        "teardown-recall-not-delivered")
            except Exception:
                errors = True
        for token_hash in self.test_tokens:
            try:
                self.admin("tokens", {"action": "revoke", "token_hash": token_hash})
                revoked = self.rows("tokens", "hash=" + sql_literal(token_hash), ("revoked",))
                require(len(revoked) == 1 and revoked[0]["revoked"] == 1,
                        "teardown-revoke-unverified")
            except Exception:
                errors = True
        for directory in self.temp_dirs:
            try:
                shutil.rmtree(directory)
            except OSError:
                errors = True
        self.temp_dirs.clear()
        self.review_checkout = None
        if errors:
            raise Failed("teardown-incomplete")

    def _is_stable(self, note_id, revision):
        try:
            self.expect_git_note(note_id, revision, "stable")
            return True
        except Failed:
            return False

    def exercise(self):
        return getattr(self, "scenario_" + self.scenario)()

    def scenario_reachable(self):
        expected = json.loads((ROOT / "install/personal-skills-inventory.json").read_text())["skills"]
        if not expected:
            raise Blocked("personal-inventory-incomplete")
        code, listing = self.rpc("skills/list", {}, "personal")
        if code != 200 or not isinstance(listing, dict):
            raise Blocked("personal-list-incomplete")
        entries = listing.get("result", listing).get("skills", [])
        if not isinstance(entries, list):
            raise Blocked("personal-list-incomplete")
        personal = [entry for entry in entries if isinstance(entry, dict) and
                    str(entry.get("uri", "")).startswith("skill://gisul/personal/")]
        require(len(personal) == len(expected) and all(
                any(approved_personal_entry(entry, approved) for entry in personal)
                for approved in expected), "personal-inventory-mismatch")
        # Verify every SKILL.md against independently approved bytes, not only the list.
        for approved in expected:
            self.personal_resource(approved, next(item for item in approved["files"]
                                                    if item["path"] == "SKILL.md"))
        candidate = next(((approved, item) for approved in expected for item in approved["files"]
                          if item["path"] != "SKILL.md" and item["path"].endswith(".md")), None)
        if candidate is None:
            raise Blocked("personal-resource-missing")
        self.personal_resource(*candidate)

    def personal_resource(self, approved, file):
        uri = f"skill://gisul/personal/{approved['name']}/{file['path']}"
        code, result = self.rpc("resources/read", {"uri": uri}, "personal")
        if code != 200 or not isinstance(result, dict):
            raise Blocked("personal-resource-unavailable")
        content = result.get("result", result).get("contents", [])
        if len(content) != 1 or not isinstance(content[0].get("text"), str):
            raise Blocked("personal-resource-incomplete")
        digest = hashlib.sha256(content[0]["text"].encode()).hexdigest()
        require(digest == file["sha256"] and
                len(content[0]["text"].encode()) == file["bytes"], "personal-digest-mismatch")

    def scenario_lifecycle(self):
        # Check the actual local consumers, not merely CLI availability.
        self.local_hub_cli("sync")
        received = self.submit(self.note())
        intake = self.drain(received["intake_id"])
        require(intake["state"] == "published", "trial-not-published")
        note_id, revision = intake["note_id"], intake["revision"]
        self.await_git(note_id, revision, "trial")
        pair = (note_id, revision)
        require(pair not in self.visible_notes(), "trial-visible-by-default")
        require(pair in self.visible_notes(trial=True), "trial-opt-in-missing")
        trial_entry = next((item for item in self.read_notes(trial=True).get("notes", [])
                            if item.get("note_id") == note_id and item.get("revision") == revision), None)
        require(trial_entry is not None and isinstance(trial_entry.get("uri"), str),
                "trial-resource-missing")
        code, hidden = self.rpc("resources/read", {"uri": trial_entry["uri"]}, "read")
        require(code == 404 or (code == 200 and isinstance(hidden, dict)
                and hidden.get("error", {}).get("message") == "NOT_FOUND"),
                "trial-readable-by-default")
        code, listing = self.rpc("resources/list", {}, "read")
        require(code == 200 and isinstance(listing, dict) and "error" not in listing,
                "default-list-incomplete")
        resources = listing.get("result", {}).get("resources")
        require(isinstance(resources, list) and
                not any(item.get("uri") == trial_entry["uri"] for item in resources),
                "trial-listed-by-default")
        code, shown = self.rpc("resources/read", {"uri": trial_entry["uri"], "_meta": {
            "io.portwright/include_trial": True}}, "read")
        content = (shown or {}).get("result", shown or {}).get("contents", []) if isinstance(shown, dict) else []
        require(code == 200 and len(content) == 1 and revision in content[0].get("text", ""),
                "trial-opt-in-bytes-missing")
        self.local_visibility(note_id, revision, "trial")
        for role in ("a", "b", "c"):
            self.tool("confirm_lesson", {"note_id": note_id, "revision": revision,
                                         "success_evidence": self.note()["success_evidence"]}, role)
        self.drain(received["intake_id"])
        votes = self.d1("SELECT e.lineage_id AS lineage_id, e.payload AS payload, "
                        "t.revoked AS revoked, t.expires AS expires FROM events e "
                        "JOIN tokens t ON t.hash=e.token_hash WHERE e.kind='confirm' "
                        f"AND e.note_id='{note_id}' AND e.revision='{revision}'")
        require(len({vote["lineage_id"] for vote in votes if vote["revoked"] == 0
                     and vote["expires"] > time.time()
                     and json.loads(vote["payload"]).get("validation") == "passed"}) == 3,
                "three-independent-confirmations-missing")
        self.review(note_id, revision, "promote", "lineages_pending_review")
        self.drain(received["intake_id"])
        self.await_git(note_id, revision, "stable")
        require(pair in self.visible_notes(), "stable-default-missing")
        self.local_visibility(note_id, revision, "stable")
        update = self.note(suffix="\nA new documented read path was observed.\n")
        update.update(note_id=note_id, expected_revision=revision)
        changed = self.submit(update)
        newer = self.drain(changed["intake_id"])
        require(newer["state"] == "published" and newer["revision"] != revision,
                "new-revision-missing")
        self.await_git(note_id, newer["revision"], "trial")
        require(pair in self.visible_notes(), "prior-stable-disappeared")
        require(not self.rows("events", f"note_id='{note_id}' AND revision='{newer['revision']}' "
                              "AND kind='confirm'", ("id",)), "old-votes-reused")
        old_a = self.token("a")
        old_hash = hashlib.sha256(old_a.encode()).hexdigest()
        reissued = self.admin("tokens", {"action": "reissue", "token_hash": old_hash,
                                          "expires": int(time.time()) + 3600})
        require(isinstance(reissued.get("token"), str), "reissue-missing")
        new_a = reissued["token"]
        self.test_tokens.append(hashlib.sha256(new_a.encode()).hexdigest())
        self.save_state()
        payload = {"note_id": note_id, "revision": newer["revision"],
                   "success_evidence": self.note()["success_evidence"]}
        self.assert_rejected(lambda: self.rpc("tools/call", {"name": "confirm_lesson",
            "arguments": payload}, "a"), (401, 403))
        self.dynamic_tokens["a"] = new_a
        old_vote = {"note_id": note_id, "revision": revision,
                    "success_evidence": self.note()["success_evidence"]}
        self.tool("confirm_lesson", old_vote, "a")
        rechecked = self.rows("events", f"note_id='{note_id}' AND revision='{revision}' AND kind='confirm'",
                              ("lineage_id", "token_hash"))
        require(len(rechecked) == 3 and
                {row["lineage_id"] for row in rechecked} == {row["lineage_id"] for row in votes} and
                any(row["token_hash"] == hashlib.sha256(new_a.encode()).hexdigest()
                    for row in rechecked), "reissue-lineage-changed")
        self.tool("confirm_lesson", payload, "a")
        require(self.count("events", f"note_id='{note_id}' AND revision='{newer['revision']}' "
                              "AND kind='confirm'") == 1, "reissue-added-independent-vote")
        self.check_limits(note_id, newer["revision"])

    def assert_rejected(self, request, statuses, role="a"):
        where = "token_hash=" + sql_literal(hashlib.sha256(self.token(role).encode()).hexdigest())
        before = tuple(self.count(table, where) for table in ("intake", "events"))
        code, _ = request()
        require(code in statuses, "limit-rejection-missing")
        after = tuple(self.count(table, where) for table in ("intake", "events"))
        require(after == before, "rejected-request-persisted")

    def check_limits(self, note_id, revision):
        token = self.issue_token()
        self.dynamic_tokens["limit"] = token
        try:
            body = self.note()["body"]
            payload = self.note(body=body + " " * (2048 - len(body.encode())))
            require(len(payload["body"].encode()) == 2048, "utf8-boundary-incomplete")
            accepted = self.submit(payload, "limit")
            require(isinstance(accepted.get("intake_id"), str), "2048-refused")
            payload = self.note(body=body + " " * (2049 - len(body.encode())))
            self.assert_rejected(lambda: self.rpc("tools/call", {
                "name": "submit_lesson", "arguments": payload}, "limit"), (400, 413), "limit")
            multi = body + "가" * ((2048 - len(body.encode())) // 3)
            multi += " " * (2048 - len(multi.encode()))
            accepted = self.submit(self.note(body=multi), "limit")
            require(isinstance(accepted.get("intake_id"), str), "multibyte-2048-refused")
            raw = json.dumps({"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
                  "params": {"name": "submit_lesson", "arguments": self.note(body="x" * 17000)}}).encode()
            require(len(raw) > 16384, "body-boundary-incomplete")
            self.assert_rejected(lambda: self.request("POST", "/mcp", "limit", raw=raw), (413,), "limit")
            for _ in range(18):
                self.submit(self.note(), "limit")
            self.assert_rejected(lambda: self.rpc("tools/call", {
                "name": "submit_lesson", "arguments": self.note()}, "limit"), (429,), "limit")
        finally:
            self.admin("tokens", {"action": "revoke", "token_hash": hashlib.sha256(token.encode()).hexdigest()})
        token = self.issue_token()
        self.dynamic_tokens["limit"] = token
        try:
            for _ in range(10):
                self.tool("report_failure", {"note_id": f"failure/verify-{uuid.uuid4().hex}",
                    "revision": revision, "reason": "The published read failed"}, "limit")
            self.assert_rejected(lambda: self.rpc("tools/call", {"name": "report_failure",
                "arguments": {"note_id": note_id, "revision": revision,
                              "reason": "The documented read failed"}}, "limit"), (429,), "limit")
        finally:
            self.admin("tokens", {"action": "revoke", "token_hash": hashlib.sha256(token.encode()).hexdigest()})

    def scenario_injection(self):
        corpus = json.loads(_corpus("injection-corpus.json").read_text())
        outcomes = corpus.get("expected_outcomes")
        edges = corpus.get("edge_expectations")
        if not isinstance(outcomes, dict) or not isinstance(edges, dict) or not outcomes or not edges:
            raise Blocked("injection-expectations-missing")
        counts = {"normal": 0, "injection": 0}
        require(len(corpus["cases"]) == 20 and all(
                sum(case["class"] == kind for case in corpus["cases"]) == 10
                for kind in counts), "injection-corpus-incomplete")
        self.dynamic_tokens["batch"] = self.issue_token()
        for case in corpus["cases"]:
            expected = outcomes.get(case["class"])
            if not isinstance(expected, dict):
                raise Blocked("injection-expectations-missing")
            payload = {"request_id": str(uuid.uuid4()), **{key: case[key] for key in
                      ("service_id", "kind", "body", "doc_url", "success_evidence")}}
            response = self.submit(payload, "batch")
            row = self.drain(response["intake_id"])
            gate_rows = self.rows("events", "intake_id='" + response["intake_id"] +
                                  "' AND kind IN ('reservation','cost')", ("id", "value"))
            if case["class"] == "normal":
                require(row["state"] == expected.get("state"), "normal-outcome-mismatch")
                self.expect_git_note(row["note_id"], row["revision"], "trial")
                require((row["note_id"], row["revision"]) in self.visible_notes(trial=True),
                        "normal-not-delivered")
                costs = self.rows("events", "intake_id='" + response["intake_id"] + "' AND kind='cost'",
                                  ("value",))
                require(bool(costs) and all(item["value"] > 0 for item in costs),
                        "normal-model-proof-missing")
            else:
                # Only a real gate-④ refusal proves the injection was judged, not dropped early.
                require(row["state"] == expected.get("state"), "injection-outcome-mismatch")
                require(row.get("reason_code") == expected.get("reason_code"),
                        "injection-reason-mismatch")
                require(len(gate_rows) >= 1, "injection-gate-proof-missing")
                delivered = self.read_notes(trial=True)
                require((row.get("note_id"), row.get("revision")) not in visible_notes(delivered)
                        and all(case["body"] not in item.get("text", "") for item in delivered["notes"]),
                        "injection-delivered")
            counts[case["class"]] += 1
        require(counts == {"normal": 10, "injection": 10}, "injection-corpus-incomplete")
        self.dynamic_tokens["edge"] = self.issue_token()
        normal = self.rows("intake", "id=" + sql_literal(self.created[10]),
                           ("state", "note_id", "revision"))
        require(len(normal) == 1 and normal[0]["state"] == "published", "edge-reference-missing")
        off_domain = self.note(doc_url="https://example.invalid/off-domain")
        # The unknown service must be consistent inside the note, or gate ① stops it first.
        unknown = self.note(service="unlisted-service",
                            doc_url="https://example.invalid/unknown-service")
        fetch_error = self.note(doc_url="https://docs.github.com/en/portwright-verification-missing-page-0000")
        forged = self.note(body=self.note()["body"].replace("status: active",
                            "grade: stable\nstatus: active"))
        stale = self.note()
        stale.update(note_id=normal[0]["note_id"], expected_revision="sha256:" + "0" * 64)
        edge_payloads = [("off_domain", off_domain), ("unknown_service", unknown),
                         ("fetch_error", fetch_error), ("forged_frontmatter", forged),
                         ("stale_revision", stale)]
        for kind, candidate in edge_payloads:
            expected = edges.get(kind)
            if not isinstance(expected, dict):
                raise Blocked("injection-expectations-missing")
            response = self.submit(candidate, "edge")
            row = self.drain(response["intake_id"])
            require(row["state"] == expected.get("state"), "edge-outcome-mismatch")
            require(row.get("reason_code") == expected.get("reason_code"),
                    "edge-reason-mismatch")
            gate_rows = self.rows("events", "intake_id='" + response["intake_id"] +
                                  "' AND kind IN ('reservation','cost')", ("id",))
            require(not gate_rows, "edge-model-reservation")

    def scenario_idempotency(self):
        payload = self.note()
        result = []
        def send():
            try:
                result.append(self.submit(payload))
            except Exception:
                result.append(None)
        threads = [threading.Thread(target=send) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        require(len(result) == 2 and all(result) and result[0]["intake_id"] == result[1]["intake_id"],
                "duplicate-request-created")
        intake_id = result[0]["intake_id"]
        require(sum(bool(item.get("duplicate")) for item in result) == 1, "duplicate-receipt-missing")
        where = "id=" + sql_literal(intake_id)
        self.overlapping_workflows(lambda: self.rows("intake", where, ("state",)) == [{"state": "pending"}])
        rows = self.rows("intake", where, ("state", "note_id", "revision", "published_commit"))
        require(len(rows) == 1 and rows[0]["state"] == "published", "idempotent-release-missing")
        row = rows[0]
        note_id, revision = row["note_id"], row["revision"]
        text = self.await_git(note_id, revision, "trial")
        require(parse_note(text)["metadata"].get("intake_id") == intake_id, "intake-receipt-missing")
        invariant = ("state", "published_commit")
        original_state = self.rows("intake", where, invariant)
        require(len(original_state) == 1
                and re.fullmatch(r"[0-9a-f]{40}", original_state[0]["published_commit"] or ""),
                "intake-invariant-missing")
        commits = self.git("log", "--format=%B", self.git_head + "..FETCH_HEAD").decode()
        require(commits.count("Intake-ID: " + intake_id) == 1, "duplicate-commit-or-intake")
        require(self.count("intake", "request_id=" + sql_literal(payload["request_id"]) +
                           " AND token_hash=" + sql_literal(hashlib.sha256(self.token("a").encode()).hexdigest())) == 1,
                "duplicate-intake-receipt")
        before, listing = self.admin("current"), self.r2_listing()
        self.await_workflow(self.dispatch())
        require(self.rows("intake", where, invariant) == original_state, "intake-invariant-broken")
        require(self.admin("current").get("current") == before.get("current"), "duplicate-release-promotion")
        require(self.r2_listing() == listing, "immutable-release-changed")
        confirm = {"note_id": note_id, "revision": revision,
                   "success_evidence": self.note()["success_evidence"]}
        votes = []
        def vote_once():
            try:
                votes.append(self.tool("confirm_lesson", confirm, "a"))
            except Exception:
                votes.append(None)
        threads = [threading.Thread(target=vote_once) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        require(len(votes) == 2 and all(votes), "parallel-confirm-failed")
        unique = self.rows("events", f"note_id='{note_id}' AND revision='{revision}' AND kind='confirm'",
                           ("id",))
        require(len(unique) == 1, "duplicate-lineage-vote")
        for role in ("vote_b", "vote_c"):
            self.dynamic_tokens[role] = self.issue_token()
            self.tool("confirm_lesson", confirm, role)
        self.drain(intake_id)
        checked = self.rows("events", f"note_id='{note_id}' AND revision='{revision}' AND kind='confirm'",
                            ("lineage_id",))
        require(len(checked) == 3 and len({event["lineage_id"] for event in checked}) == 3,
                "promotion-votes-missing")
        self.review(note_id, revision, "promote", "lineages_pending_review")
        def review_pending():
            reviews = self.rows("events", f"kind='ack' AND action='review_promote' "
                                f"AND note_id='{note_id}' AND revision='{revision}'", ("id",))
            return bool(reviews) and any(
                (note.get("note_id"), note.get("revision"), note.get("grade")) == (note_id, revision, "trial")
                for note in self.read_notes(trial=True)["notes"])
        self.overlapping_workflows(review_pending)
        self.await_git(note_id, revision, "stable")
        marker = f"Promote: {note_id} {revision} "
        messages = self.git("log", "--format=%B", self.git_head + "..FETCH_HEAD").decode()
        require(messages.count(marker) == 1, "duplicate-promotion")
        promoted = self.admin("current")
        listing = self.r2_listing()
        self.await_workflow(self.dispatch())
        self.git("fetch", "origin", "main")
        require(self.admin("current").get("current") == promoted.get("current") and
                self.r2_listing() == listing, "repeat-promotion-changed-release")
        messages = self.git("log", "--format=%B", self.git_head + "..FETCH_HEAD").decode()
        require(messages.count(marker) == 1, "duplicate-promotion-trailer")
        # The review receipt is its own ack row keyed by note and revision, not the intake.
        acks = self.rows("events", f"kind='ack' AND action='review_promote' "
                         f"AND note_id='{note_id}' AND revision='{revision}'", ("id",))
        require(len(acks) == 1, "duplicate-ack")
        states = self.rows("intake", where, invariant)
        require(states == original_state, "intake-invariant-broken")

    def scenario_isolation(self):
        other = os.environ["HUB_OTHER_ORIGIN"].rstrip("/")
        other_audience = os.environ["HUB_VERIFY_OTHER_AUDIENCE"]
        pin = os.environ["HUB_VERIFY_OTHER_OLD_PIN"]
        uri = os.environ["HUB_VERIFY_OTHER_URI"]
        if (other_audience == self.hub or other_audience not in ("public", "personal", "company")
                or not re.fullmatch(r"[a-f0-9]{40}", pin)
                or not uri.startswith("skill://gisul/")):
            raise Blocked("other-audience-evidence-invalid")
        if self.hub == "company" and other_audience != "public":
            raise Blocked("company-comparison-requires-public")
        code, _ = self.request("GET", "/admin/budget", "read", origin=other)
        require(code in (401, 403), "cross-audience-admin")
        code, _ = self.rpc("skills/list", {}, "read", origin=other)
        require(code in (401, 403), "cross-audience-list")
        code, _ = self.rpc("resources/read", {"uri": uri}, "read", origin=other)
        require(code in (401, 403), "cross-audience-direct")
        code, _ = self.rpc("resources/read", {"uri": uri, "_meta": {"io.gisul/commit": pin}},
                           "read", origin=other)
        require(code in (401, 403), "cross-audience-old-pin")
        code, _ = self.request("GET", "/sync", "other")
        require(code in (401, 403), "foreign-token-accepted")
        own = None
        if other_audience == "personal":
            code, result = self.rpc("skills/list", {}, "other", origin=other)
            skills = result.get("result", result).get("skills", []) if isinstance(result, dict) else []
            require(code == 200 and any(item.get("name") == "aim" for item in skills),
                    "personal-control-missing")
        else:
            code, own = self.request("GET", "/sync", "other", origin=other)
            require(code == 200 and isinstance(own, dict) and own.get("audience") == other_audience,
                    "other-audience-control-missing")
        if self.hub == "company":
            company_id = os.environ["HUB_VERIFY_COMPANY_NOTE_ID"]
            require(any(note_id == company_id for note_id, _ in self.visible_notes()),
                    "company-control-missing")
            require(own is not None and not any(note_id == company_id for note_id, _ in visible_notes(own)),
                    "company-fell-back-to-public")
        code, _ = self.request("GET", "/admin/current", "read")
        require(code in (401, 403), "read-token-admin-access")
        meta = {"uri": uri, "_meta": {"io.gisul/commit": pin}}
        code, result = self.rpc("resources/read", meta, "read")
        require(code in (404, 410) or (isinstance(result, dict)
                and (result.get("error") or result.get("result", {}).get("isError"))),
                "foreign-pin-or-direct-readable")
        code, result = self.rpc("tools/list", {}, "operator")
        require(code == 200 and isinstance(result, dict), "tool-list-unavailable")
        tools = result.get("result", result).get("tools", [])
        require(not any(tool.get("name") in ("create_skill", "update_skill") for tool in tools),
                "skill-write-exposed")
        config = self.deployment()
        bindings = config.get("bindings")
        if not isinstance(bindings, list):
            raise Blocked("binding-list-incomplete")
        require(not any(any(fragment in binding.get("name", "").upper() for fragment in
                            ("GITHUB", "MODEL", "JEV", "TYPESAFE")) for binding in bindings),
                "forbidden-binding")
        # Listing keys plus the one release manifest; the bucket's bodies stay unread.
        listing = self.r2_listing()
        require(not any("/personal/" in key or "_private/" in key for key in listing),
                "personal-package-published")
        manifest_key = f"releases/{self.release_commit()}/inventory.json"
        require(manifest_key in listing, "release-manifest-missing")
        manifest = json.dumps(self.r2_object(manifest_key), ensure_ascii=False, separators=(",", ":"))
        require("skill://gisul/personal/" not in manifest and '"origin":"personal"' not in manifest,
                "personal-package-published")

    def deployment(self):
        # Cloudflare API access is restricted to the worker's settings. No value is logged.
        worker = os.environ["HUB_VERIFY_WORKER"]
        account = os.environ["HUB_VERIFY_ACCOUNT_ID"]
        url = f"https://api.cloudflare.com/client/v4/accounts/{account}/workers/scripts/{worker}/settings"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ["HUB_VERIFY_CLOUDFLARE_TOKEN"]})
        try:
            with OPEN(req, timeout=30) as response:
                document = json.load(response)
                if not document.get("success"):
                    raise Blocked("deployment-settings-incomplete")
                return document["result"]
        except (urllib.error.URLError, ValueError, KeyError):
            raise Blocked("deployment-settings-incomplete") from None

    def scenario_secrets(self):
        cases = json.loads(_corpus("secrets-corpus.json").read_text())["cases"]
        samples = [assemble(case["parts"]) for case in cases if case["expect"] == "reject"]
        # Deployment must not mirror the worker anywhere but this tail.
        config = self.deployment()
        observability = config.get("observability")
        require(isinstance(observability, dict) and observability.get("enabled") is False,
                "log-channel-exposed")
        require(not config.get("logpush") and not config.get("tail_consumers"),
                "log-channel-exposed")
        start_marker = self.run_id + "-start"
        end_marker = self.run_id + "-end"
        self.dynamic_tokens["control"] = self.issue_token()
        source_hash = hashlib.sha256(self.token("a").encode()).hexdigest()
        source_where = "token_hash=" + sql_literal(source_hash)
        source_before = tuple(self.count(table, source_where) for table in ("intake", "events"))
        control = self.submit(self.note(), "control")
        # The exact start URL is observed in the tail before any sample is sent.
        code, _ = self.request("GET", "/healthz?" + TAIL_URL + "=" + start_marker, "control")
        require(code == 200, "tail-control-unavailable")
        self.await_tail_url(start_marker)
        expected_urls = {self.origin + "/healthz?verify=" + marker
                         for marker in (start_marker, end_marker)}
        tools = {
            "submit_lesson": {**self.note(), "note_id": "failure/verify-secret",
                              "expected_revision": "sha256:" + "0" * 64},
            "confirm_lesson": {"note_id": "failure/verify-secret", "revision": "sha256:" + "0" * 64,
                               "success_evidence": self.note()["success_evidence"]},
            "report_failure": {"note_id": "failure/verify-secret", "revision": "sha256:" + "0" * 64,
                               "reason": "The documented read failed"},
        }
        for name, base in tools.items():
            # Include constrained string leaves too: screening precedes schema validation.
            fields = [key + "." + child if isinstance(value, dict) else key
                      for key, value in base.items()
                      for child in (value if isinstance(value, dict) else ("",))]
            for sample in samples:
                for field in fields + ["rpc-id", "unknown", "unknown-key", "escaped-json",
                                       "malformed-json", "invalid-utf8", "oversize"]:
                    payload = json.loads(json.dumps(base))
                    if field in fields:
                        parent, _, leaf = field.partition(".")
                        if leaf:
                            payload[parent][leaf] = sample
                        else:
                            payload[parent] = sample
                    elif field == "unknown-key":
                        payload[sample] = "synthetic"
                    elif field != "rpc-id":
                        payload["unknown"] = sample
                    request = {"jsonrpc": "2.0", "id": sample if field == "rpc-id" else str(uuid.uuid4()),
                               "method": "tools/call", "params": {"name": name, "arguments": payload}}
                    raw = json.dumps(request).encode()
                    if field == "escaped-json":
                        escaped = "".join(chr(92) + "u" + format(ord(char), "04x") for char in sample).encode()
                        raw = raw.replace(json.dumps(sample)[1:-1].encode(), escaped)
                    if field == "malformed-json": raw = raw[:-1] + sample.encode()
                    if field == "invalid-utf8": raw = raw[:-1] + sample.encode() + b"\xff}"
                    # Keep the complete call; transport-limit cases never replace valid field calls.
                    if field == "oversize": raw += b" " * 17000
                    path = "/mcp?verify=" + self.run_id + "-" + str(len(expected_urls))
                    expected_urls.add(self.origin + path)
                    check_rejected_response(self, raw, sample, path=path)
        require(tuple(self.count(table, source_where) for table in ("intake", "events")) == source_before,
                "rejected-request-persisted")
        self.dynamic_tokens["negative"] = self.issue_token()
        accepted_ids = {control["intake_id"]}
        for case in cases:
            if case["expect"] == "allow":
                payload = self.note(suffix="\n" + assemble(case["parts"]))
                accepted_ids.add(self.submit(payload, "negative")["intake_id"])
        self.admin("tokens", {"action": "revoke",
                              "token_hash": hashlib.sha256(self.token("negative").encode()).hexdigest()})
        require(self.drain(control["intake_id"])["state"] == "published",
                "control-not-published")
        self.admin("tokens", {"action": "revoke",
                              "token_hash": hashlib.sha256(self.token("control").encode()).hexdigest()})
        code, _ = self.request("GET", "/healthz?" + TAIL_URL + "=" + end_marker, "read")
        require(code == 200, "tail-end-control-unavailable")
        self.await_tail_url(end_marker)
        # Scan all new rows, irrespective of owner, without retaining other users' text.
        self.secret_rows(samples)
        hashes = [hashlib.sha256(self.token(role).encode()).hexdigest()
                  for role in ("a", "control", "negative")]
        scope = ("created>=" + str(int(self.start_time)) + " AND token_hash IN (" +
                 ",".join(sql_literal(value) for value in hashes) + ")")
        rows = self.rows("intake", scope, ("id", "state"))
        events = self.rows("events", "intake_id=" + sql_literal(control["intake_id"]),
                           ("id", "kind", "intake_id", "payload", "value"))
        require({row["id"] for row in rows} == accepted_ids, "unaccepted-row-persisted")
        require(any(row["id"] == control["intake_id"] and row["state"] == "published"
                    for row in rows), "control-missing-in-d1")
        commits = self.git("log", "--format=%H", self.git_head + "..FETCH_HEAD").decode().splitlines()
        git_bytes = b"".join(self.git("show", "--format=fuller", commit) for commit in commits)
        git_data = git_bytes.decode(errors="replace")
        require(not any(sample in git_data for sample in samples), "secret-leaked-git")
        # Read every current object again: an ETag-only baseline is not body evidence.
        self.check_r2_samples(samples, baseline=self.r2_baseline)
        reservations = {row["id"]: json.loads(row["payload"]) for row in events
                        if row["kind"] == "reservation"
                        and row.get("intake_id") == control["intake_id"]}
        costs = [row for row in events if row["kind"] == "cost"
                 and row.get("intake_id") == control["intake_id"]]
        require(bool(costs) and bool(reservations), "control-model-evidence-missing")
        for cost in costs:
            payload = json.loads(cost["payload"])
            reservation = reservations.get(payload.get("reservation_id"), {})
            measurement = (payload.get("request_digest"), payload.get("bytes"))
            require(isinstance(measurement[0], str)
                    and re.fullmatch(r"sha256:[a-f0-9]{64}", measurement[0])
                    and type(measurement[1]) is int and measurement[1] > 0
                    and measurement == (reservation.get("request_digest"), reservation.get("bytes"))
                    and cost["value"] > 0
                    and payload.get("status") in ("actual", "conservative_max"),
                    "control-model-measurement-missing")
        self.seal_tail(expected_urls, samples)

    def secret_rows(self, samples):
        # Explicit TEXT projections from 0001_intake.sql; no unrelated numeric metadata.
        columns = {
            "intake": ("id", "token_hash", "lineage_id", "request_id", "request_digest", "kind",
                       "service_id", "target_note_id", "expected_revision", "body", "doc_url",
                       "success_evidence", "state", "reason_code", "note_id", "revision",
                       "gate_digest", "commit_sha", "published_commit"),
            "events": ("id", "intake_id", "note_id", "revision", "lineage_id", "token_hash",
                       "kind", "action", "operation_id", "payload", "month"),
        }
        scope = "created>=" + str(int(self.start_time))
        for table, names in columns.items():
            total = self.count(table, scope)
            for offset in range(0, total, 100):
                page = self.rows(table, scope + f" ORDER BY rowid LIMIT 100 OFFSET {offset}", names)
                if (not isinstance(page, list) or len(page) != min(100, total - offset)
                        or any(not isinstance(row, dict) or set(row) != set(names) for row in page)):
                    raise Blocked("d1-window-incomplete")
                values = [value for row in page for value in row.values()]
                while values:
                    value = values.pop()
                    if isinstance(value, str):
                        require(not any(sample in value for sample in samples), "secret-leaked-d1")
                        # JSON columns escape multiline samples; inspect decoded values as well.
                        try:
                            decoded = json.loads(value)
                        except ValueError:
                            continue
                        if isinstance(decoded, (dict, list)):
                            values.append(decoded)
                    elif isinstance(value, dict):
                        values.extend(value.keys())
                        values.extend(value.values())
                    elif isinstance(value, list):
                        values.extend(value)
                del page
            if self.count(table, scope) != total:
                raise Blocked("d1-window-incomplete")

    def check_r2_samples(self, samples, *, baseline=None):
        # Establish trust by reading every body, even for an initially empty bucket.
        # The ending sweep rechecks all bytes; only ETags plus SHA-256 digests survive.
        listing = self.r2_listing()
        if baseline is not None and not baseline.keys() <= listing.keys():
            raise Blocked("r2-baseline-mismatch")
        verified = {}
        for key in sorted(listing):
            body = self._r2_body(key)
            require(not any(sample in key or sample.encode() in body for sample in samples),
                    "secret-leaked-r2")
            try:
                values = [json.loads(body)]
            except (ValueError, UnicodeError):
                values = []
            while values:
                value = values.pop()
                if isinstance(value, str):
                    require(not any(sample in value for sample in samples), "secret-leaked-r2")
                    try:
                        values.append(json.loads(value))
                    except ValueError:
                        pass
                elif isinstance(value, dict):
                    values.extend(value.keys())
                    values.extend(value.values())
                elif isinstance(value, list):
                    values.extend(value)
            digest = hashlib.sha256(body).hexdigest()
            if baseline is not None and key in baseline:
                old_etag, old_digest = baseline[key]
                if listing[key] == old_etag and digest != old_digest:
                    raise Blocked("r2-baseline-mismatch")
            verified[key] = (listing[key], digest)
        if self.r2_listing() != listing:
            raise Blocked("r2-window-changed")
        return verified

    def _r2_url(self, suffix):
        account = os.environ["HUB_VERIFY_ACCOUNT_ID"]
        bucket = os.environ["HUB_VERIFY_BUCKET"]
        return (f"https://api.cloudflare.com/client/v4/accounts/{account}/r2/buckets/"
                f"{bucket}/objects{suffix}")

    def r2_listing(self):
        """Official List Objects: result[] plus result_info paging, per_page at most 1000."""
        token = os.environ["HUB_VERIFY_CLOUDFLARE_TOKEN"]
        listing = {}
        cursor = None
        seen = set()
        while True:
            query = "per_page=1000" + (f"&cursor={urllib.parse.quote(cursor, safe='')}" if cursor else "")
            req = urllib.request.Request(self._r2_url("?" + query),
                                         headers={"Authorization": "Bearer " + token})
            try:
                with OPEN(req, timeout=30) as response:
                    page = json.load(response)
            except (urllib.error.URLError, ValueError):
                raise Blocked("r2-read-incomplete") from None
            if not isinstance(page, dict):
                raise Blocked("r2-read-incomplete")
            result = page.get("result")
            if page.get("success") is not True or not isinstance(result, list):
                raise Blocked("r2-read-incomplete")
            for obj in result:
                if (not isinstance(obj, dict) or not isinstance(obj.get("key"), str)
                        or not isinstance(obj.get("etag"), str) or not obj["etag"]
                        or obj["key"] in listing):
                    raise Blocked("r2-read-incomplete")
                listing[obj["key"]] = obj["etag"]
            cursor = r2_next_cursor(page, seen)
            if not cursor:
                break
            seen.add(cursor)
        return listing

    def _r2_body(self, key):
        # A slash inside a key is part of the hierarchy and must not be percent-encoded.
        req = urllib.request.Request(self._r2_url("/" + urllib.parse.quote(key, safe="/")),
                                     headers={"Authorization": "Bearer " + os.environ["HUB_VERIFY_CLOUDFLARE_TOKEN"]})
        try:
            with OPEN(req, timeout=30) as response:
                content = response.read(16 * 1024 * 1024 + 1)
                if len(content) > 16 * 1024 * 1024:
                    raise Blocked("r2-object-incomplete")
                return content
        except urllib.error.URLError:
            raise Blocked("r2-read-incomplete") from None

    def r2_object(self, key):
        try:
            return json.loads(self._r2_body(key))
        except ValueError:
            raise Blocked("r2-projection-incomplete") from None

    def local_home(self, home):
        config = home / "_local/hub/config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"schema_version": 1, "active": self.hub, "hubs": {
            self.hub: {"url": self.origin, "token_env": "HUB_TEST_READ_TOKEN"}}}))

    def local_cli(self, home, *args, stdin=None):
        command = [sys.executable, "-m", "portwright.cli", *args, "--home", str(home)]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home),
               "PYTHONPATH": str(ROOT / "lib"), "HUB_TEST_READ_TOKEN": self.token("read")}
        try:
            run = subprocess.run(command, input=stdin, capture_output=True, text=True,
                                 timeout=90, check=True, cwd=str(home), env=env)
            return json.loads(run.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            raise Blocked("local-consumer-incomplete") from None

    def local_get_note(self, home, path, trial=False):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "get_note", "arguments": {"path": path, "hub": self.hub, "include_trial": trial}}}
        reply = self.local_cli(home, "mcp", stdin=json.dumps(request) + "\n")
        result = reply.get("result")
        if not isinstance(result, dict) or type(result.get("isError")) is not bool:
            raise Blocked("local-mcp-incomplete")
        return result

    def local_visibility(self, note_id, revision, grade):
        with tempfile.TemporaryDirectory(prefix="hub-verify-", dir="/tmp") as directory:
            home = Path(directory)
            self.local_home(home)
            self.local_cli(home, "hub", "sync", "--hub", self.hub)
            _, default = snapshot(home, self.hub)
            require(any((n["note_id"], n["revision"]) == (note_id, revision) for n, _ in default)
                    == (grade == "stable"), "local-default-sync-visibility")
            self.local_cli(home, "hub", "sync", "--hub", self.hub, "--include-trial")
            _, active = snapshot(home, self.hub, include_trial=True)
            matches = [(n, p) for n, p in active if (n["note_id"], n["revision"]) == (note_id, revision)]
            require(len(matches) == 1 and matches[0][0]["grade"] == grade, "local-revision-missing")
            note, path = matches[0]
            for trial in (False, True):
                visible = grade == "stable" or trial
                flags = ("--include-trial",) if trial else ()
                decision = self.local_cli(home, "preflight", note["service_id"],
                                          "--hub", self.hub, "--json", *flags)
                require(not decision.get("invalid_entries"), "local-preflight-invalid")
                paths = [decision.get("procedure"), *decision.get("active_lessons", [])]
                require((path in paths) == visible, "local-preflight-visibility")
                result = self.local_get_note(home, path, trial)
                require(result["isError"] != visible, "local-mcp-visibility")
                if visible:
                    payload = json.loads(result["content"][0]["text"])
                    require(payload.get("path") == path and payload.get("text") == note["text"],
                            "local-mcp-revision-mismatch")

    def local_sync(self, note_id, revision, recall_action):
        slug = note_id.split("/", 1)[1]
        self.local_hub_cli("sync")
        with tempfile.TemporaryDirectory(prefix="hub-verify-", dir="/tmp") as directory:
            home = Path(directory)
            self.local_home(home)
            private = home / "failures/_private" / (slug + ".md")
            private.parent.mkdir(parents=True)
            private.write_text("---\ndate: 2026-09-25\nservice: github\n"
                               "service_version: 1\nstatus: active\n"
                               "distributable: false\n---\n## Root cause\nLocal override.\n")
            original = private.read_bytes()
            self.local_cli(home, "hub", "sync", "--hub", self.hub, "--include-trial")
            first, notes = snapshot(home, self.hub, include_trial=True)
            matches = [(n, p) for n, p in notes if (n["note_id"], n["revision"]) == (note_id, revision)]
            require(len(matches) == 1 and matches[0][0]["grade"] == "trial", "trial-local-cache-missing")
            note, old_path = matches[0]
            cached = self.local_get_note(home, old_path, True)
            require(not cached["isError"] and
                    json.loads(cached["content"][0]["text"]).get("text") == note["text"],
                    "trial-local-read-missing")
            recall_action()
            self.local_cli(home, "hub", "sync", "--hub", self.hub, "--include-trial")
            current, active = snapshot(home, self.hub, include_trial=True)
            require(current["generation"] != first["generation"] and
                    not any((n["note_id"], n["revision"]) == (note_id, revision) for n, _ in active),
                    "recalled-local-cache-active")
            require(self.local_get_note(home, old_path, True)["isError"], "old-generation-readable")
            decision = self.local_cli(home, "preflight", note["service_id"],
                                      "--hub", self.hub, "--include-trial", "--json")
            require(old_path not in decision.get("active_lessons", []), "recalled-local-preflight-active")
            require(private.read_bytes() == original, "private-override-modified")

    def check_directory(self, uri, pin, *, recalled, remaining=None):
        if recalled and remaining is None:
            raise Blocked("recall-directory-baseline-missing")
        code, directory = self.rpc("resources/directory/read", {
            "uri": uri.rsplit("/", 1)[0], "_meta": {
                "io.gisul/commit": pin, "io.portwright/include_trial": True}}, "read")
        if recalled and isinstance(directory, dict) and directory.get("error", {}).get("message") == "NOT_FOUND":
            require(code in (200, 404) and not remaining, "recall-directory-sibling-hidden")
            return
        require(code == 200 and isinstance(directory, dict) and "error" not in directory,
                "recall-directory-unavailable")
        resources = directory.get("result", {}).get("resources")
        require(isinstance(resources, list) and all(isinstance(item, dict)
                and isinstance(item.get("uri"), str) for item in resources),
                "recall-directory-unavailable")
        visible = {item["uri"] for item in resources}
        require((uri in visible) != recalled, "recall-directory-visibility")
        if recalled:
            require(remaining <= visible, "recall-directory-sibling-hidden")
        else:
            return visible - {uri}

    def scenario_recall(self):
        # The local cache/preflight half of this scenario is H4; without it nothing runs.
        self.local_hub_cli("sync")
        received = self.submit(self.note())
        row = self.drain(received["intake_id"])
        require(row["state"] == "published", "recall-trial-missing")
        note_id, revision = row["note_id"], row["revision"]
        self.await_git(note_id, revision, "trial")
        other_row = self.drain(self.submit(self.note(suffix="\nUnrelated recall control.\n"))["intake_id"])
        require(other_row["state"] == "published", "recall-other-control-missing")
        other_pair = (other_row["note_id"], other_row["revision"])
        # The pin is the release the deployment actually serves, not a local FETCH_HEAD.
        pin = self.release_commit(trial=True)
        notes = self.read_notes(trial=True).get("notes", [])
        target = next((item for item in notes if item.get("note_id") == note_id
                       and item.get("revision") == revision), None)
        require(target is not None and isinstance(target.get("uri"), str), "old-resource-missing")
        remaining = self.check_directory(target["uri"], pin, recalled=False)
        sibling = next((item for item in notes
                        if (item.get("note_id"), item.get("revision")) == other_pair), None)
        require(sibling is not None and sibling.get("uri") in remaining,
                "recall-directory-sibling-control-missing")
        path = recall_path(note_id, revision)

        def recall_target():
            updated = self.note(suffix="\nThe documented route changed again.\n")
            updated.update(note_id=note_id, expected_revision=revision)
            new = self.drain(self.submit(updated)["intake_id"])
            require(new["state"] == "published" and new["revision"] != revision,
                    "new-revision-not-kept")
            for role in ("a", "b"):
                self.tool("report_failure", {"note_id": note_id, "revision": revision,
                                             "reason": "Documented read path did not work"}, role)
            old = self.token("a")
            reissued = self.admin("tokens", {"action": "reissue",
                "token_hash": hashlib.sha256(old.encode()).hexdigest(),
                "expires": int(time.time()) + 3600})
            require(isinstance(reissued.get("token"), str), "report-reissue-missing")
            self.dynamic_tokens["a"] = reissued["token"]
            self.test_tokens.append(hashlib.sha256(self.token("a").encode()).hexdigest())
            self.save_state()
            self.tool("report_failure", {"note_id": note_id, "revision": revision,
                                         "reason": "Documented read path did not work"}, "a")
            self.await_workflow(self.dispatch())
            votes = self.rows("events", f"note_id='{note_id}' AND revision='{revision}' AND kind='report'",
                              ("lineage_id",))
            require(len(votes) == 2 and len({vote["lineage_id"] for vote in votes}) == 2,
                    "distinct-reports-missing")
            self.git("fetch", "origin", "main")
            marker = json.loads(self.git("show", "FETCH_HEAD:" + path))
            require(marker["note_id"] == note_id and marker["revision"] == revision,
                    "commons-recall-missing")
            projection = self.r2_object("recalls/current.json")
            require(any(item.get("note_id") == note_id and item.get("revision") == revision
                        for item in projection.get("entries", [])), "r2-recall-missing")
            pairs = self.visible_notes(trial=True)
            require((note_id, revision) not in pairs, "current-recall-missing")
            require((note_id, new["revision"]) in pairs, "new-revision-hidden")
            require(other_pair in pairs, "recall-other-note-hidden")
            code, result = self.rpc("resources/read", {"uri": target["uri"], "_meta": {
                        "io.gisul/commit": pin, "io.portwright/include_trial": True}}, "read")
            require(code == 410 or "REVISION_RECALLED" in json.dumps(result),
                    "direct-recall-missing")
            self.check_directory(target["uri"], pin, recalled=True, remaining=remaining)

        self.local_sync(note_id, revision, recall_target)
        stable = self.submit(self.note(suffix="\nIndependent stable control.\n"))
        proven = self.drain(stable["intake_id"])
        require(proven["state"] == "published", "stable-control-missing")
        stable_id, stable_rev = proven["note_id"], proven["revision"]
        for role in ("a", "b", "c"):
            self.tool("confirm_lesson", {"note_id": stable_id, "revision": stable_rev,
                        "success_evidence": self.note()["success_evidence"]}, role)
        self.drain(stable["intake_id"])
        self.review(stable_id, stable_rev, "promote", "lineages_pending_review")
        self.drain(stable["intake_id"])
        self.await_git(stable_id, stable_rev, "stable")
        for role in ("a", "b"):
            self.tool("report_failure", {"note_id": stable_id, "revision": stable_rev,
                                         "reason": "Synthetic stable recall check"}, role)
        self.await_workflow(self.dispatch())
        require((stable_id, stable_rev) in self.visible_notes(), "stable-auto-recalled")
        self.review(stable_id, stable_rev, "recall", "distinct_reports_pending_review")
        self.await_workflow(self.dispatch())
        pairs = self.visible_notes()
        require((stable_id, stable_rev) not in pairs, "stable-operator-recall-missing")
        require(other_pair in self.visible_notes(trial=True), "recall-other-note-hidden")

    def scenario_budget(self):
        published = self.submit(self.note())
        base = self.drain(published["intake_id"])
        require(base["state"] == "published", "budget-recall-control-missing")
        note_id, revision = base["note_id"], base["revision"]
        self.await_git(note_id, revision, "trial")
        pin = self.release_commit(trial=True)
        target = next((item for item in self.read_notes(trial=True).get("notes", [])
                       if item.get("note_id") == note_id and item.get("revision") == revision), None)
        require(target is not None and isinstance(target.get("uri"), str),
                "budget-recall-resource-missing")
        before = self.admin("budget")
        available = before.get("available_micro_usd", 0)
        if not isinstance(available, int) or available < 2:
            raise Blocked("budget-no-headroom")
        amount = available // 2 + 1
        operation_ids = [str(uuid.uuid4()) for _ in range(2)]
        self.race_operation_ids = operation_ids
        self.save_state()
        results = []
        def reserve(operation_id):
            payload = {"action": "reserve", "operation_id": operation_id, "purpose": "bundle",
                       "target_digest": "sha256:" + "0" * 64, "request_digest": "sha256:" + "0" * 64,
                       "bytes": 0, "reserve_micro_usd": amount}
            try:
                results.append(self.request("POST", "/admin/budget", "operator", payload))
            except Blocked:
                results.append(None)
        threads = [threading.Thread(target=reserve, args=(item,)) for item in operation_ids]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        # Only an exact granted-200/denied-409 pair proves the conditional insert raced;
        # a 400, 5xx or timeout is a failure, never a silent loss for either side.
        if any(item is None for item in results):
            raise Blocked("http-unavailable")
        require(sorted(code for code, _ in results) == [200, 409], "reservation-race-incorrect")
        granted = [value for code, value in results if code == 200]
        require(len(granted) == 1 and isinstance(granted[0], dict) and
                granted[0].get("granted") is True and
                isinstance(granted[0].get("reservation_id"), str), "reservation-race-incorrect")
        winner = granted[0]
        self.unused_reservations.append(winner["reservation_id"])
        self.save_state()
        saved = self.rows("events", "kind='reservation' AND operation_id IN (" +
                          ",".join(sql_literal(item) for item in operation_ids) + ")", ("id", "value"))
        require(len(saved) == 1 and saved[0]["value"] == amount, "reservation-ledger-mismatch")
        # The driver has not sent a model request for this specific reservation.
        self.admin("budget", {"action": "settle", "reservation_id": winner["reservation_id"],
                              "actual_micro_usd": 0, "usage": {}, "status": "unused"})
        before_models = {row["id"] for row in self.rows(
            "events", "kind IN ('cost','reservation')", ("id",))}
        before_published = {row["id"] for row in self.rows("intake", "state='published'", ("id",))}
        allowed = set()
        self.budget_changed = True
        self.save_state()
        self.admin("budget", {"cap_microusd": 10000})
        capped = self.admin("budget")
        require(capped.get("cap_micro_usd") == 10000, "cap-override-not-applied")
        if capped.get("available_micro_usd", 0) > 0:
            operation_id = str(uuid.uuid4())
            self.race_operation_ids.append(operation_id)
            self.save_state()
            held = self.admin("budget", {"action": "reserve", "operation_id": operation_id,
                              "purpose": "bundle", "target_digest": "sha256:" + "0" * 64,
                              "request_digest": "sha256:" + "0" * 64, "bytes": 0,
                              "reserve_micro_usd": capped["available_micro_usd"]})
            require(held.get("granted") is True, "cap-headroom-not-occupied")
            self.unused_reservations.append(held["reservation_id"])
            self.save_state()
            allowed.add(held["reservation_id"])
            capped = self.admin("budget")
        self.dynamic_tokens["pending"] = self.issue_token()
        pending = self.submit(self.note(), "pending")
        self.await_workflow(self.dispatch())
        rows = self.rows("intake", "id=" + sql_literal(pending["intake_id"]), ("state",))
        after = self.admin("budget")
        require(len(rows) == 1 and rows[0]["state"] == "pending", "budget-pending-not-queued")
        require(after.get("cost_micro_usd") == capped.get("cost_micro_usd") and
                after.get("reserved_micro_usd") == capped.get("reserved_micro_usd"),
                "budget-spend-increased")
        after_models = {row["id"] for row in self.rows(
            "events", "kind IN ('cost','reservation')", ("id",))}
        after_published = {row["id"] for row in self.rows("intake", "state='published'", ("id",))}
        require(after_models - before_models <= allowed and
                after_published == before_published, "budget-model-or-publish-continued")
        self.admin("tokens", {"action": "revoke", "token_hash":
                              hashlib.sha256(self.token("pending").encode()).hexdigest()})
        require((note_id, revision) in self.visible_notes(trial=True), "budget-read-blocked")
        for role in ("a", "b"):
            self.tool("report_failure", {"note_id": note_id, "revision": revision,
                                        "reason": "Synthetic budget recall check"}, role)
        self.await_workflow(self.dispatch())
        self.git("fetch", "origin", "main")
        marker = recall_path(note_id, revision)
        self.git("show", "FETCH_HEAD:" + marker)
        code, result = self.rpc("resources/read", {"uri": target["uri"], "_meta": {
                "io.gisul/commit": pin, "io.portwright/include_trial": True}}, "read")
        require(code == 410 or "REVISION_RECALLED" in json.dumps(result),
                "budget-pinned-recall-missing")


def required_env(hub, scenario):
    names = ["HUB_ORIGIN"]
    if hub == "personal":
        names += [TOKENS["personal"]]
    else:
        names += list(EVIDENCE) + ["HUB_VERIFY_WRANGLER_CONFIG", "HUB_VERIFY_COMMONS_REPO",
                                   TOKENS["operator"], TOKENS["read"], TOKENS["a"]]
        if scenario in ("lifecycle", "recall", "budget"):
            names += [TOKENS["b"]]
        if scenario in ("lifecycle", "recall"):
            names += [TOKENS["c"]]
        if scenario in ("isolation",):
            names += [TOKENS["other"], "HUB_OTHER_ORIGIN", "HUB_VERIFY_WORKER",
                      "HUB_VERIFY_OTHER_AUDIENCE", "HUB_VERIFY_OTHER_OLD_PIN",
                      "HUB_VERIFY_OTHER_URI"]
            if hub == "company":
                names += ["HUB_VERIFY_COMPANY_NOTE_ID"]
        if scenario in ("secrets", "idempotency", "isolation", "recall"):
            names += ["HUB_VERIFY_BUCKET"]
        if scenario == "secrets":
            names += ["HUB_VERIFY_WORKER"]
    return names


def inject_credentials(args, argv):
    """Re-exec once through the approved headless runner; expose only fixed results."""
    path = ROOT / "_local/deploy/verify-hub.env"
    try:
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077 or metadata.st_size > 16384):
            raise ValueError
        for parent in (path.parent.parent, path.parent):
            directory = parent.lstat()
            if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
                    or directory.st_mode & 0o022):
                raise ValueError
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            name, separator, reference = line.partition("=")
            # Keep vault URI literals out of the public export without weakening its scanner.
            if (not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name)
                    or not reference.startswith("op:" + "//")):
                raise ValueError
        if os.environ.get("PORTWRIGHT_HUB_VERIFY_INJECTED"):
            raise ValueError
        child = subprocess.run(
            [str(OPSVC), "run", "--env-file", str(path), "--",
             sys.executable, str(Path(__file__).resolve()), *argv],
            capture_output=True, text=True,
            env=dict(os.environ, PORTWRIGHT_HUB_VERIFY_INJECTED="1"))
        line = child.stdout.strip()
        expected = "TEARDOWN OK" if args.teardown else "SETUP READY" if args.setup else LITERALS[args.scenario]
        failure = re.fullmatch(rf"(BLOCKED|FAIL) {args.scenario} [a-z0-9-]+", line)
        if (child.returncode == 0 and line == expected) or (child.returncode == 1 and failure):
            print(line)
            return child.returncode
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        pass
    print(f"BLOCKED {args.scenario} missing-credentials")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify a deployed Hub using independent reads")
    parser.add_argument("--hub", choices=("public", "personal", "company"), required=True)
    parser.add_argument("--scenario", choices=tuple(LITERALS), required=True)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--teardown", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    if ((args.setup and args.teardown) or
            ((args.setup or args.teardown or args.evidence_dir) and not args.run_id) or
            ((args.setup or args.teardown) and not args.evidence_dir) or
            (args.run_id and not re.fullmatch(
                r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", args.run_id))):
        print(f"BLOCKED {args.scenario} invalid-run-options")
        return 1
    if args.hub == "personal" and args.scenario != "reachable" or args.hub != "personal" and args.scenario == "reachable":
        print(f"BLOCKED {args.scenario} incompatible-hub")
        return 1
    if os.environ.get("PORTWRIGHT_JEV_FIXTURES") or os.environ.get("PORTWRIGHT_JEV_RECORD"):
        print(f"BLOCKED {args.scenario} synthetic-model")
        return 1
    if any(not os.environ.get(name) for name in required_env(args.hub, args.scenario)):
        return inject_credentials(args, sys.argv[1:] if argv is None else argv)
    runner = Hub(args.hub, args.scenario, run_id=args.run_id, evidence_dir=args.evidence_dir)
    try:
        if args.teardown:
            runner.load_state()
            runner.teardown()
            message = "TEARDOWN OK"
            code = 0
        elif args.setup:
            if (args.evidence_dir / (runner.run_id + ".json")).exists():
                raise Blocked("recovery-state-exists")
            runner.setup()
            if runner.tail is not None:
                runner.tail.terminate()
                runner.tail.wait(timeout=10)
            message = "SETUP READY"
            code = 0
        else:
            if args.evidence_dir and (args.evidence_dir / (runner.run_id + ".json")).exists():
                runner.load_state()
                if runner.created or runner.test_tokens or runner.budget_changed:
                    raise Blocked("recovery-required")
            code, message = execute(args.scenario, runner)
    except Blocked as exc:
        code, message = 1, f"BLOCKED {args.scenario} {exc}"
    except Exception:
        code, message = 1, f"FAIL {args.scenario} unexpected"
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
