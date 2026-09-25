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


HTML = (
    "<!doctype html><html><head><title>Guide</title>"
    "<style>body{color:red}</style><script>var x=1;</script></head>"
    "<body><h1>Install</h1><p>Run <code>tool --now</code> &amp; read <a href='/x'>docs</a>.</p>"
    "<script>steal()</script></body></html>"
)


class FetchDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        (self.home / "services").mkdir()
        resolver = patch.object(doc_cache, "_resolve_addresses", return_value=PUBLIC)
        self.addCleanup(resolver.stop)
        resolver.start()

    def fetch(self, text: str = "docs body"):
        return patch.object(doc_cache, "_fetch", return_value=text)

    def test_host_allowed_is_exact_and_idna_normalized(self) -> None:
        hosts = ["docs.example.com", "xn--bcher-kva.example"]
        self.assertTrue(doc_cache.host_allowed(URL, hosts))
        self.assertTrue(doc_cache.host_allowed("https://docs.example.com:443/x", hosts))
        self.assertTrue(doc_cache.host_allowed("https://xn--bcher-kva.example/x", hosts))
        self.assertTrue(doc_cache.host_allowed("https://bücher.example/x", hosts))
        self.assertFalse(doc_cache.host_allowed("https://sub.docs.example.com/x", hosts))
        self.assertFalse(doc_cache.host_allowed("https://notdocs.example.com/x", hosts))
        self.assertFalse(doc_cache.host_allowed("https://docs.example.com.evil.test/x", hosts))
        self.assertFalse(doc_cache.host_allowed(URL, []))
        self.assertFalse(doc_cache.host_allowed(URL, None))
        self.assertFalse(doc_cache.host_allowed(URL, ["DOCS.example.com "]))
        self.assertFalse(doc_cache.host_allowed("not a url", hosts))

    def test_path_prefixes_respect_segment_boundaries(self) -> None:
        hosts = ["docs.example.com"]
        prefixes = ["/docs/v1"]
        self.assertTrue(doc_cache.host_allowed("https://docs.example.com/docs/v1/x", hosts, prefixes))
        self.assertTrue(doc_cache.host_allowed("https://docs.example.com/docs/v1", hosts, prefixes))
        self.assertTrue(doc_cache.host_allowed("https://docs.example.com/docs/v1/", hosts, prefixes))
        self.assertTrue(doc_cache.host_allowed("https://docs.example.com/docs//v1/x", hosts, prefixes))
        self.assertFalse(doc_cache.host_allowed("https://docs.example.com/docs/v1/../v1/x", hosts, prefixes))
        self.assertFalse(doc_cache.host_allowed("https://docs.example.com/docs/v10/x", hosts, prefixes))
        self.assertFalse(doc_cache.host_allowed("https://docs.example.com/other/v1/x", hosts, prefixes))
        self.assertTrue(doc_cache.host_allowed("https://docs.example.com/other", hosts))

    def test_path_prefix_escapes_and_traversal_fail_closed(self) -> None:
        hosts = ["docs.example.com"]
        prefixes = ["/docs/v1"]
        for url in (
            "https://docs.example.com/docs/%2e%2e/user",
            "https://docs.example.com/docs/v1/../user",
            "https://docs.example.com/%2e%2e/docs/v1/x",
            "https://docs.example.com/docs/v1/%2Fuser",
            "https://docs.example.com/docs/v1\\..\\user",
        ):
            with self.subTest(url=url):
                self.assertFalse(doc_cache.host_allowed(url, hosts, prefixes))

    def test_invalid_prefix_entries_fail_closed(self) -> None:
        hosts = ["docs.example.com"]
        for prefixes in (["docs/v1"], ["%2e"], ["/"], ["/docs/%2e"], [None], [7], ["/docs/v1", None]):
            with self.subTest(prefixes=prefixes):
                self.assertFalse(doc_cache.host_allowed("https://docs.example.com/docs/v1/x", hosts, prefixes))

    def test_safe_url_path_normalizes_only_harmless_segments(self) -> None:
        self.assertEqual(doc_cache.safe_url_path("/docs/v1/x"), "/docs/v1/x")
        self.assertEqual(doc_cache.safe_url_path(""), "/")
        self.assertEqual(doc_cache.safe_url_path("/docs//v1/x"), "/docs/v1/x")
        self.assertEqual(doc_cache.safe_url_path("/docs/./v1/"), "/docs/v1")
        for path in ("/docs/%2e%2e/x", "/docs/../x", "/a\\b", "/a\x00b"):
            with self.subTest(path=path):
                self.assertIsNone(doc_cache.safe_url_path(path))

    def test_off_domain_never_fetches(self) -> None:
        with self.fetch() as fetch:
            with self.assertRaises(doc_cache._FetchFailed) as caught:
                doc_cache.fetch_document(URL, ["other.example.com"])
        self.assertEqual(caught.exception.category, "off_domain")
        fetch.assert_not_called()
        with self.fetch() as fetch:
            with self.assertRaises(doc_cache._FetchFailed):
                doc_cache.fetch_document(URL, [])
        fetch.assert_not_called()

    def test_allowed_host_returns_markdown_unchanged(self) -> None:
        with self.fetch("plain <not html> body") as fetch:
            text = doc_cache.fetch_document(URL, ["docs.example.com"])
        self.assertEqual(text, "plain <not html> body")
        fetch.assert_called_once_with(URL)

    def test_allowed_host_extracts_html_text_without_script_or_style(self) -> None:
        with self.fetch(HTML):
            text = doc_cache.fetch_document(URL, ["docs.example.com"])
        self.assertIn("Install", text)
        self.assertIn("Run tool --now & read docs.", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("var x=1", text)
        self.assertNotIn("steal()", text)
        self.assertNotIn("<h1>", text)

    def test_none_allowlist_is_the_local_cache_path(self) -> None:
        with self.fetch("text"):
            self.assertEqual(doc_cache.fetch_document(URL, None), "text")

    def test_xml_prolog_and_leading_whitespace_still_extract(self) -> None:
        document = '<?xml version="1.0" encoding="utf-8"?>\n<html><body>Hello <b>there</b></body></html>'
        with self.fetch(document):
            self.assertEqual(doc_cache.fetch_document(URL, ["docs.example.com"]), "Hello there")

    def test_markdown_quoting_html_tags_is_not_parsed(self) -> None:
        markdown = "Use `<html>` and `</html>` in the template.\n\nAnother line.\n"
        with self.fetch(markdown):
            self.assertEqual(doc_cache.fetch_document(URL, ["docs.example.com"]), markdown)

    def test_refresh_document_extracts_html_text(self) -> None:
        with self.fetch(HTML):
            result = refresh_document(self.home, "acme", URL)
        self.assertEqual(result["status"], "fetched")
        cached = load_document(self.home, "acme", URL)
        self.assertIn("Install", cached["text"])
        self.assertNotIn("steal()", cached["text"])

    def test_port_must_be_https_default(self) -> None:
        for url in ("https://docs.example.com:8443/x", "https://docs.example.com:80/x", "http://docs.example.com/x"):
            with self.subTest(url=url):
                with self.assertRaises(doc_cache._FetchFailed) as caught:
                    doc_cache._validate_url(url)
                self.assertEqual(caught.exception.category, "invalid-url")
        doc_cache._validate_url("https://docs.example.com:443/x")
        doc_cache._validate_url(URL)

    def test_non_utf8_body_is_refused(self) -> None:
        class RawResponse:
            def __init__(self, payload: bytes):
                self.payload = payload

            def read(self, limit: int) -> bytes:
                return self.payload

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> bool:
                return False

        with patch.object(doc_cache._OPENER, "open", return_value=RawResponse(b"ok\xff\xfe\x00end")):
            with self.assertRaises(doc_cache._FetchFailed) as caught:
                doc_cache._fetch(URL)
        self.assertEqual(caught.exception.category, "not-utf8")
        with patch.object(doc_cache._OPENER, "open", return_value=RawResponse(b"ok\xff\xfe\x00end")):
            result = refresh_document(self.home, "acme", URL)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "not-utf8")
        self.assertFalse(evidence_path(self.home).exists())
        with patch.object(doc_cache._OPENER, "open", return_value=RawResponse("día".encode("utf-8"))):
            self.assertEqual(doc_cache._fetch(URL), "día")


if __name__ == "__main__":
    unittest.main()
