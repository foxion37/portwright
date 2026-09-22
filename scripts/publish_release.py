#!/usr/bin/env python3
"""Publish built skill bundles to a gisul Worker as an immutable release (G3/P4).

Follows gisul's publication contract (docs/deployment.md in changeroa/gisul): build inventory
schema v1 and release.json, upload inventory.json first, then every object, then POST
/admin/verify and /admin/promote with the current pointer's ETag and a monotonic sequence.

The builder's inventory.json is the manifest: every file in the output directory must be
listed there with matching sha256 and size, and every listed file must exist. Unlisted or
tampered files refuse the release. Files outside a skill root (release.json,
watch-sources.json) ship digest-bound without a resource URI.

Environment: GISUL_PUBLISH_ORIGIN (https://<worker> only, no credentials, path, query, or
fragment), GISUL_PUBLISH_TOKEN (bearer, never printed), optional GISUL_RELEASE_SEQUENCE
(defaults to GITHUB_RUN_NUMBER), optional GISUL_SOURCE (URI source id, default
"portwright"), optional GISUL_YAML_MODULE (directory whose node_modules provides the
`yaml` package; defaults to the sibling gisul checkout), GISUL_DRY_RUN=1 to only write
the inventory locally. Redirects are never followed, error bodies are never echoed,
mutations are never retried automatically, and an uncertain promote response is
resolved by re-reading /admin/current before reporting.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = os.environ.get("GISUL_SOURCE", "portwright")
LOCAL_FILES = {"inventory.json", "inventory.gisul.json", ".portwright-build"}
COMMIT_RE = re.compile(r"[a-f0-9]{40}")
SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # refuse every redirect; it surfaces as an HTTPError with the 3xx code


urlopen = urllib.request.build_opener(_NoRedirect()).open  # module-level for fake transports in tests



def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()

_NODE_YAML: tuple[str, str] | None = None
_NODE_YAML_MISSING = False


def _node_yaml(block: str) -> dict | None:
    """Parse frontmatter with the same YAML semantics the gisul Worker uses (the `yaml`
    npm package), via node or bun. Returns None when no parser is installed; invalid
    YAML is a hard failure. Build-time only: the Portwright runtime stays stdlib."""
    global _NODE_YAML, _NODE_YAML_MISSING
    if _NODE_YAML_MISSING:
        return None
    script = (
        "const{createRequire}=require('module');"
        "const{parse}=createRequire(process.argv[1])('yaml');"
        "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>process.stdout.write(JSON.stringify(parse(s))));"
    )
    pairs = [_NODE_YAML] if _NODE_YAML else [
        (runtime, str(Path(base) / "noop.js"))
        for runtime in (shutil.which("node"), shutil.which("bun")) if runtime
        for base in (os.environ.get("GISUL_YAML_MODULE"), str(ROOT.parent / "gisul" / "worker"), str(ROOT.parent / "gisul")) if base
    ]
    for runtime, anchor in pairs:
        try:
            run = subprocess.run([runtime, "-e", script, anchor], input=block, capture_output=True, text=True, timeout=30)
        except OSError:
            continue
        if run.returncode == 0:
            _NODE_YAML = (runtime, anchor)
            return json.loads(run.stdout)
        if "Cannot find module" in run.stderr or "MODULE_NOT_FOUND" in run.stderr:
            if _NODE_YAML:
                break
            continue
        raise SystemExit(f"SKILL.md has invalid YAML frontmatter: {run.stderr.strip().splitlines()[-1][:160] if run.stderr.strip() else 'parse error'}")
    _NODE_YAML_MISSING = True
    return None


def _yaml_scalar(value: str, lineno: int) -> object:
    if value[:1] == '"':
        try:
            return json.loads(value)
        except ValueError:
            raise SystemExit(f"SKILL.md frontmatter line {lineno}: bad double-quoted scalar")
    if value[:1] == "'":
        if len(value) >= 2 and value[-1:] == "'":
            return value[1:-1].replace("''", "'")
        raise SystemExit(f"SKILL.md frontmatter line {lineno}: bad single-quoted scalar")
    if value[:1] in "|>&*!%@`[{" or value == "-" or value.startswith("- "):
        raise SystemExit(f"SKILL.md frontmatter line {lineno}: unsupported YAML metadata; install node with the gisul worker yaml package or set GISUL_YAML_MODULE")
    low = value.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"0x[0-9a-fA-F]+", value):
        return int(value, 16)
    if re.fullmatch(r"0o[0-7]+", value):
        return int(value, 8)
    if re.fullmatch(r"[-+]?(\.\d+|\d+(\.\d*)?)([eE][-+]?\d+)?", value):
        return float(value)
    if low in (".inf", "+.inf"):
        return float("inf")
    if low == "-.inf":
        return float("-inf")
    if low == ".nan":
        return float("nan")
    return value


def _subset_yaml(block: str) -> dict:
    """Stdlib fallback: YAML 1.2 core-schema flat 'key: scalar' lines only. Folded,
    nested, or otherwise richer metadata is refused explicitly, never guessed."""
    data: dict[str, object] = {}
    for lineno, raw in enumerate(block.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw != raw.lstrip():
            raise SystemExit(f"SKILL.md frontmatter line {lineno}: nested or folded metadata needs the gisul YAML parser (node + yaml)")
        key, sep, value = raw.partition(":")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise SystemExit(f"SKILL.md frontmatter line {lineno}: unsupported YAML metadata")
        value = value.strip()
        if value and value[:1] not in "\"'":
            value = re.split(r"\s+#", value)[0].strip()
        if not value:
            raise SystemExit(f"SKILL.md frontmatter line {lineno}: empty or block value needs the gisul YAML parser (node + yaml)")
        data[key] = _yaml_scalar(value, lineno)
    return data


def frontmatter(text: str) -> dict:
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)", text)
    if not match:
        raise SystemExit("SKILL.md without frontmatter")
    data = _node_yaml(match.group(1))
    if data is None:
        data = _subset_yaml(match.group(1))
    if not isinstance(data, dict) or not data.get("name") or not data.get("description"):
        raise SystemExit("SKILL.md frontmatter needs name and description")
    return data


def load_manifest(out: Path) -> dict:
    """Read and structurally validate the builder's inventory.json."""
    path = out / "inventory.json"
    if not path.is_file():
        raise SystemExit(f"missing builder inventory: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"unreadable builder inventory: {error}")
    if not isinstance(manifest, dict) or not COMMIT_RE.fullmatch(str(manifest.get("commit", ""))):
        raise SystemExit("builder inventory lacks a 40-hex commit")
    if not isinstance(manifest.get("skills"), dict) or not isinstance(manifest.get("files"), list):
        raise SystemExit("builder inventory lacks skills/files")
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("sha256", ""))) or not isinstance(entry.get("size"), int):
            raise SystemExit("builder inventory has a malformed file entry")
        parts = Path(entry["path"]).parts
        if any(part in {".", ".."} or not SEGMENT_RE.fullmatch(part) for part in parts):
            raise SystemExit(f"builder inventory has a non-canonical path: {entry['path']}")
    flat = {entry["path"]: entry for entry in manifest["files"]}
    seen_paths: set[str] = set()
    for name, skill in manifest["skills"].items():
        if not isinstance(skill, dict) or not isinstance(skill.get("path"), str) or not skill["path"].endswith("/SKILL.md"):
            raise SystemExit(f"builder inventory has a malformed skill entry: {name}")
        if skill["path"] in seen_paths or skill["path"] not in flat:
            raise SystemExit(f"builder inventory skill {name} has a duplicate or unlisted path")
        seen_paths.add(skill["path"])
        for entry in skill.get("files", []):
            want = flat.get(entry.get("path") if isinstance(entry, dict) else None)
            if want is None or want["sha256"] != entry.get("sha256") or want["size"] != entry.get("size"):
                raise SystemExit(f"builder inventory skill {name} lists a file outside the manifest")
        root_prefix = skill["path"].removesuffix("SKILL.md")
        resources = skill.get("files")
        if not isinstance(resources, list):
            raise SystemExit(f"builder inventory skill {name} lacks its complete resource list")
        actual = {entry["path"]: (entry["sha256"], entry["size"]) for entry in resources}
        expected = {path: (entry["sha256"], entry["size"]) for path, entry in flat.items() if path.startswith(root_prefix)}
        if len(actual) != len(resources) or actual != expected:
            raise SystemExit(f"builder inventory skill {name} resource set differs from its package")
    return manifest


def validated_inventory(out: Path) -> dict:
    """Shared read-only gate entry point: parse the builder manifest and verify the
    output tree matches it exactly — listed file set, sha256, and size per file; no
    symlinks, no unlisted files, no missing files. Returns the builder inventory
    dict. Raises SystemExit on any mismatch. No network, no environment required."""
    manifest = load_manifest(out)
    verify_output(out, manifest)
    return manifest


def verify_output(out: Path, manifest: dict) -> dict[str, bytes]:
    """The output tree must equal the inventoried bytes exactly: no unlisted files,
    no missing files, no tampered content."""
    expected = {entry["path"]: (entry["sha256"], entry["size"]) for entry in manifest["files"]}
    if len(expected) != len(manifest["files"]):
        raise SystemExit("builder inventory lists a path twice")
    objects: dict[str, bytes] = {}
    for path in sorted(p for p in out.rglob("*") if p.is_file() or p.is_symlink()):
        relative = path.relative_to(out).as_posix()
        if relative in LOCAL_FILES:
            continue
        if path.is_symlink():
            raise SystemExit(f"refusing symlink in bundle output: {relative}")
        want = expected.pop(relative, None)
        if want is None:
            raise SystemExit(f"unlisted file in bundle output: {relative}")
        data = path.read_bytes()
        if len(data) != want[1] or hashlib.sha256(data).hexdigest() != want[0]:
            raise SystemExit(f"bundle output differs from builder inventory: {relative}")
        objects[relative] = data
    if expected:
        raise SystemExit(f"inventoried file missing from bundle output: {sorted(expected)[0]}")
    return objects


def build_inventory(manifest: dict, objects: dict[str, bytes], commit: str) -> tuple[dict, dict[str, bytes]]:
    skills: list[dict] = []
    files: list[dict] = []
    roots = sorted({entry["path"][: -len("SKILL.md")] for entry in manifest["skills"].values()})
    for root in roots:
        name = root.rstrip("/").rsplit("/", 1)[-1]
        resources = []
        for relative in sorted(path for path in objects if path.startswith(root)):
            data = objects[relative]
            uri = f"skill://gisul/{SOURCE}/{relative}"
            entry = {"uri": uri, "digest": sha(data), "size": len(data)}
            resources.append(entry)
            files.append({"path": relative, "digest": entry["digest"], "size": entry["size"], "uri": uri})
        skill_uri = f"skill://gisul/{SOURCE}/{root}SKILL.md"
        if skill_uri not in {r["uri"] for r in resources}:
            raise SystemExit(f"{root}: missing SKILL.md")
        text = objects[f"{root}SKILL.md"].decode("utf-8")
        meta = frontmatter(text)
        if meta["name"] != name:
            raise SystemExit(f"{root}: frontmatter name differs from directory")
        skills.append({"uri": skill_uri, "frontmatter": meta, "resources": resources})
    for relative in sorted(path for path in objects if "/" not in path):
        data = objects[relative]
        files.append({"path": relative, "digest": sha(data), "size": len(data)})
    for relative in objects:
        if "/" in relative and not any(relative.startswith(root) for root in roots):
            raise SystemExit(f"bundle file outside every skill root: {relative}")
        if sum(relative.startswith(root) for root in roots) > 1:
            raise SystemExit(f"bundle file under nested skill roots: {relative}")
    release = f"{commit[:12]}-{len(skills)}skills"
    manifest_digests = [
        {"uri": s["uri"], "manifest_digest": sha(json.dumps(
            sorted(({"uri": r["uri"], "digest": r["digest"], "size": r["size"]} for r in s["resources"]), key=lambda r: r["uri"]),
            separators=(",", ":"), ensure_ascii=False).encode("utf-8"))}
        for s in skills
    ]
    release_bytes = json.dumps({"commit": commit, "release": release, "skills": manifest_digests}, separators=(",", ":")).encode("utf-8")
    objects["release.json"] = release_bytes
    files.append({"path": "release.json", "digest": sha(release_bytes), "size": len(release_bytes)})
    inventory = {"schema_version": 1, "commit": commit, "release": release, "skills": skills, "files": files, "aliases": {}}
    return inventory, objects


def clean_origin(raw: str) -> str:
    """Only a bare https://host[:port] origin: no credentials, path, query, or fragment."""
    try:
        url = urlsplit(raw.strip())
        port = url.port
    except ValueError:
        raise SystemExit("GISUL_PUBLISH_ORIGIN is not a valid URL")
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise SystemExit("GISUL_PUBLISH_ORIGIN must be https://host[:port] with no credentials, path, query, or fragment")
    return f"https://{url.hostname}:{port}" if port else f"https://{url.hostname}"


def request(method: str, url: str, token: str, body: bytes | None = None, content_type: str = "application/json", timeout: int = 180) -> tuple[int, dict, dict]:
    # Verification digests every object in the release, so it scales with the catalog, not the request.
    req = urllib.request.Request(url, data=body, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": content_type, "User-Agent": "portwright-publish/2.0"})
    try:
        with urlopen(req, timeout=timeout) as response:
            payload = response.read()
            try:
                detail = json.loads(payload) if payload else {}
            except ValueError:
                detail = {}
            return response.status, dict(response.headers), detail
    except urllib.error.HTTPError as error:
        status, headers = error.code, dict(error.headers or {})
        error.close()
        return status, headers, {}
    except (urllib.error.URLError, OSError) as error:
        return 0, {}, {"error": type(error).__name__}


def current_identity(origin: str, token: str) -> tuple[int, dict | None]:
    status, headers, body = request("GET", f"{origin}/admin/current", token)
    if status != 200:
        return status, None
    etag = body.get("etag") or headers.get("ETag") or headers.get("etag")
    current = body.get("current")
    if isinstance(current, dict):
        return status, {**current, "etag": etag}
    return status, {"etag": etag}


def main(argv: list[str]) -> int:
    out = Path(argv[0] if argv else ROOT / "build" / "skill-bundles").resolve()
    manifest = load_manifest(out)
    commit = manifest["commit"]
    objects = verify_output(out, manifest)
    inventory, objects = build_inventory(manifest, objects, commit)
    inventory_bytes = json.dumps(inventory, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    identity = {"commit": commit, "release": inventory["release"], "inventory_digest": sha(inventory_bytes)}
    (out / "inventory.gisul.json").write_bytes(inventory_bytes)
    print(json.dumps({"release": identity["release"], "skills": len(inventory["skills"]), "objects": len(objects) + 1}))
    if os.environ.get("GISUL_DRY_RUN"):
        return 0
    token = os.environ.get("GISUL_PUBLISH_TOKEN", "")
    if not token:
        print("GISUL_PUBLISH_TOKEN is required", file=sys.stderr)
        return 1
    origin = clean_origin(os.environ.get("GISUL_PUBLISH_ORIGIN", ""))
    sequence = int(os.environ.get("GISUL_RELEASE_SEQUENCE") or os.environ.get("GITHUB_RUN_NUMBER") or 0)
    if sequence < 1:
        print("a positive GISUL_RELEASE_SEQUENCE or GITHUB_RUN_NUMBER is required", file=sys.stderr)
        return 1
    status, current = current_identity(origin, token)
    if status not in (200, 404):
        print(f"admin/current failed: HTTP {status}", file=sys.stderr)
        return 1
    expected_etag = (current or {}).get("etag") if status == 200 else None
    uploads = [("inventory.json", inventory_bytes)] + sorted(objects.items())
    for relative, data in uploads:
        status, _, _ = request("PUT", f"{origin}/admin/releases/{commit}/{relative}", token, data, "application/octet-stream")
        if status not in (200, 201):
            print(f"upload {relative} failed: HTTP {status}", file=sys.stderr)
            return 1
    body = json.dumps({**identity, "expected_etag": expected_etag, "sequence": sequence}).encode("utf-8")
    status, _, _ = request("POST", f"{origin}/admin/verify", token, body)
    if status != 200:
        print(f"verify failed: HTTP {status}", file=sys.stderr)
        return 1
    print("verify: ok")
    status, _, _ = request("POST", f"{origin}/admin/promote", token, body)
    if status != 200:
        # The response is uncertain: the promote may still have landed. Re-read the
        # pointer and report the true state instead of retrying the mutation.
        read_status, current = current_identity(origin, token)
        if read_status == 200 and (current or {}).get("inventory_digest") == identity["inventory_digest"]:
            print(json.dumps({"promoted": identity, "confirmed_via": "admin/current"}))
            return 0
        print(f"promote failed: HTTP {status}; current pointer: {json.dumps(current)}", file=sys.stderr)
        return 1
    print("promote: ok")
    print(json.dumps({"promoted": identity}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
