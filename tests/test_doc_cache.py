from __future__ import annotations

import hashlib
import ipaddress
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from portwright import doc_cache
from portwright.doc_cache import _FetchFailed, load_document, refresh_document


URL = "https://docs.example.com/guide"
OTHER_URL = "https://docs.example.com/other"
PUBLIC = [ipaddress.ip_address("93.184.216.34")]


def evidence_path(home: Path, service_id: str = "acme") -> Path:
    return home / "services" / "_private" / "_evidence" / f"{service_id}.json"


class DocCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        (self.home / "services").mkdir()
        # DNS is patched separately so _fetch stays the only network seam.
        resolver = patch.object(doc_cache, "_resolve_addresses", return_value=PUBLIC)
        self.addCleanup(resolver.stop)
        resolver.start()

    def fetch(self, text: str = "docs body"):
        return patch.object(doc_cache, "_fetch", return_value=text)

    def test_first_fetch_caches_evidence_and_reports_changed_none(self) -> None:
        with self.fetch("alpha docs") as fetch:
            result = refresh_document(self.home, "acme", URL)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["status"], "fetched")
        self.assertIsNone(result["changed"])
        self.assertNotIn("text", result)
        self.assertNotIn("error", result)
        record = json.loads(evidence_path(self.home).read_text(encoding="utf-8"))
        self.assertEqual(record["source_url"], URL)
        self.assertEqual(record["sha256"], hashlib.sha256(b"alpha docs").hexdigest())
        self.assertEqual(record["text"], "alpha docs")
        loaded = load_document(self.home, "acme", URL)
        self.assertEqual(loaded["text"], "alpha docs")
        self.assertEqual(loaded["sha256"], result["sha256"])

    def test_same_content_unchanged_and_new_content_changed(self) -> None:
        with self.fetch("same"):
            first = refresh_document(self.home, "acme", URL)
            second = refresh_document(self.home, "acme", URL)
        self.assertIsNone(first["changed"])
        self.assertFalse(second["changed"])
        with self.fetch("different"):
            third = refresh_document(self.home, "acme", URL)
        self.assertTrue(third["changed"])

    def test_load_requires_matching_url(self) -> None:
        with self.fetch():
            refresh_document(self.home, "acme", URL)
        self.assertIsNone(load_document(self.home, "acme", OTHER_URL))
        self.assertIsNotNone(load_document(self.home, "acme", URL))

    def test_load_rejects_tampered_digest_and_text(self) -> None:
        with self.fetch("original"):
            refresh_document(self.home, "acme", URL)
        path = evidence_path(self.home)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["text"] = "tampered"
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertIsNone(load_document(self.home, "acme", URL))
        record["sha256"] = hashlib.sha256(b"tampered").hexdigest()
        record["fetched_at"] = "not-a-date"
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertIsNone(load_document(self.home, "acme", URL))

    def test_load_rejects_malformed_cache_file(self) -> None:
        path = evidence_path(self.home)
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(load_document(self.home, "acme", URL))

    def test_invalid_urls_fail_without_writing(self) -> None:
        # Real _fetch: validation raises before any socket use.
        cases = {
            "http://docs.example.com/x": "invalid-url",
            "https://user:pw@docs.example.com/x": "invalid-url",
            "https://docs.example.com/x?a=1": "invalid-url",
            "https://docs.example.com/x#frag": "invalid-url",
            "https://localhost/x": "url-not-public",
            "https://127.0.0.1/x": "url-not-public",
            "https://[fd00::1]/x": "url-not-public",
            "https://10.0.0.1/x": "url-not-public",
        }
        for url, category in cases.items():
            with self.subTest(url=url):
                result = refresh_document(self.home, "acme", url)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"], category)
            self.assertIsNone(result["changed"])
        self.assertFalse(evidence_path(self.home).exists())

    def test_private_dns_resolution_refused(self) -> None:
        with patch.object(
            doc_cache, "_resolve_addresses", return_value=[ipaddress.ip_address("192.168.1.1")]
        ):
            result = refresh_document(self.home, "acme", URL)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "url-not-public")

    def test_fetch_failure_preserves_prior_evidence(self) -> None:
        with self.fetch("good"):
            refresh_document(self.home, "acme", URL)
        before = evidence_path(self.home).read_bytes()
        with patch.object(doc_cache, "_fetch", side_effect=_FetchFailed("network")):
            result = refresh_document(self.home, "acme", URL)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "network")
        self.assertEqual(evidence_path(self.home).read_bytes(), before)
        self.assertEqual(load_document(self.home, "acme", URL)["text"], "good")

    def test_redirect_is_refused(self) -> None:
        redirect = urllib.error.HTTPError(URL, 302, "Found", {}, None)
        with patch.object(doc_cache._OPENER, "open", side_effect=redirect):
            result = refresh_document(self.home, "acme", URL)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "redirect")

    def test_symlinked_evidence_directory_refused(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            (self.home / "services" / "_private").symlink_to(outside, target_is_directory=True)
            with self.fetch(), self.assertRaises(RuntimeError):
                refresh_document(self.home, "acme", URL)
            self.assertIsNone(load_document(self.home, "acme", URL))

    def test_symlinked_cache_file_refused(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "acme.json"
            target.write_text("{}", encoding="utf-8")
            path = evidence_path(self.home)
            path.parent.mkdir(parents=True)
            path.symlink_to(target)
            with self.fetch(), self.assertRaises(RuntimeError):
                refresh_document(self.home, "acme", URL)
            self.assertIsNone(load_document(self.home, "acme", URL))

    def test_invalid_service_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            refresh_document(self.home, "Bad_ID", URL)
        self.assertIsNone(load_document(self.home, "../escape", URL))


if __name__ == "__main__":
    unittest.main()
