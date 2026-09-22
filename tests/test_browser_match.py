from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._v2 import FIXTURES, run_cli

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from portwright.browser import select_menu  # noqa: E402
from portwright.jev import JevClient  # noqa: E402


CANDIDATES = [
    {"ref": "e1", "role": "link", "name": "Overview", "url": "https://console.example/overview"},
    {"ref": "e2", "role": "link", "name": "Billing", "url": "https://console.example/billing"},
    {"ref": "e3", "role": "link", "name": "API Keys", "url": "https://console.example/settings/api-keys"},
]


class BrowserMenuSelectionTests(unittest.TestCase):
    """The browser extracts candidates; portwright only selects, and refuses when unsure."""

    def test_synthetic_choice_selects_the_target_menu_and_value_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(json.dumps(CANDIDATES), encoding="utf-8")
            run = run_cli("browser", "select", "--candidates", str(path), "--goal", "create an API key", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        selection = json.loads(run.stdout)
        self.assertTrue(selection["accepted"])
        self.assertEqual(selection["candidate_ref"], "e3")
        self.assertEqual(selection["value_location"], "https://console.example/settings/api-keys")
        self.assertEqual(selection["source"], "fixture")

    def test_exact_name_match_is_deterministic_without_a_judgment(self) -> None:
        selection = select_menu(CANDIDATES, "billing", jev=JevClient(fixtures=Path("/nonexistent")), fixture_id="unused")
        self.assertTrue(selection.accepted)
        self.assertEqual(selection.candidate_ref, "e2")
        self.assertEqual(selection.source, "exact-name")

    def test_no_candidates_is_refused(self) -> None:
        selection = select_menu([], "anything", jev=JevClient(fixtures=FIXTURES), fixture_id="browser-select")
        self.assertFalse(selection.accepted)
        self.assertEqual(selection.reject_reason, "no candidates")

    def test_close_top_two_is_refused_even_with_ok_confidence(self) -> None:
        two = [CANDIDATES[0], CANDIDATES[1]]
        selection = select_menu(two, "account settings", jev=JevClient(fixtures=FIXTURES), fixture_id="browser-select-close")
        self.assertFalse(selection.accepted)
        self.assertIn("top-2 margin 0.02", selection.reject_reason)
        self.assertEqual(selection.candidate_ref, "e1")
        self.assertIsNone(selection.value_location)

    def test_model_choosing_none_is_refused(self) -> None:
        selection = select_menu(CANDIDATES[:1], "delete account", jev=JevClient(fixtures=FIXTURES), fixture_id="browser-select-none")
        self.assertFalse(selection.accepted)
        self.assertEqual(selection.reject_reason, "model chose none")

    def test_missing_fixture_or_credential_is_refused_secret_safe(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            selection = select_menu(CANDIDATES, "api key page", jev=JevClient(fixtures=Path(empty)), fixture_id="browser-select")
        self.assertFalse(selection.accepted)
        self.assertIn("fixture missing", selection.reject_reason)
        selection = select_menu(CANDIDATES, "api key page", jev=JevClient(api_key=None), fixture_id="browser-select")
        self.assertFalse(selection.accepted)
        self.assertEqual(selection.reject_reason, "judgment unavailable: credential missing")

    def test_cli_refusal_exits_nonzero_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as empty:
            path = Path(directory) / "candidates.json"
            path.write_text(json.dumps(CANDIDATES), encoding="utf-8")
            run = run_cli("browser", "select", "--candidates", str(path), "--goal", "api key page", fixtures=Path(empty))
        self.assertEqual(run.returncode, 1)
        self.assertIn("REFUSE:", run.stdout)


if __name__ == "__main__":
    unittest.main()
