"""Shared secret screening corpus; all credential-like strings are synthetic and assembled."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from portwright.memory import SECRET_PATTERNS  # noqa: E402


class SecretPatternsTest(unittest.TestCase):
    def test_known_families_and_assignments_are_blocked(self):
        samples = [
            "-----BEGIN " + "RSA PRIVATE KEY-----",
            "sk_live" + "-" + "a" * 12,
            "xoxb" + "-" + "a" * 12,
            "api_key: " + "a" * 12,
            "Authorization: Bearer " + "a" * 12,
            "AKIA" + "A" * 16,
            "npm_" + "a" * 22,
            "AIza" + "a" * 35,
            "password=" + "a" * 6,
            "glpat-" + "a" * 24,
            "pypi-" + "a" * 24,
            "eyJ" + "a" * 12 + "." + "b" * 9 + "." + "c" * 9,
            "password=" + "a" * 9,
            "AccountKey=" + "a" * 18,
            "https://user:" + "a" * 12 + "@example.invalid",
            "SOME_API_TOKEN=" + "a" * 12,
            'export FOO_SECRET="' + "a" * 12 + '"',
        ]
        samples += [prefix + "_" + "a" * 20 for prefix in ("ghp", "gho", "ghu", "ghs", "ghr", "github_pat")]
        for text in samples:
            with self.subTest(kind=text.split("=")[0][:18]):
                self.assertTrue(any(pattern.search(text) for pattern in SECRET_PATTERNS))

    def test_placeholders_and_prose_are_not_blocked(self):
        for text in (
            "TOKEN=<your-token>", "export FOO_SECRET=${MY_SECRET}",
            "SOME_API_TOKEN=[REDACTED]", "PASSWORD=<your-password>",
            "A token is used for authentication, not as a credential.",
        ):
            with self.subTest(kind=text.split("=")[0][:18]):
                self.assertFalse(any(pattern.search(text) for pattern in SECRET_PATTERNS))
    def test_ellipsis_in_assignment_value_is_a_placeholder(self):
        for ellipsis in ("." * 3, "\u2026"):
            for value in ("lin_api_" + ellipsis, "lin_api_" + "a" * 9 + ellipsis + "b" * 8):
                for name in ("SOME_API_TOKEN", "TOKEN", "PASSWORD", "AccountKey"):
                    with self.subTest(name=name, ellipsis=ellipsis == "\u2026", length=len(value)):
                        self.assertFalse(any(pattern.search(f'{name}="{value}"') for pattern in SECRET_PATTERNS))

    def test_content_home_cannot_override_packaged_secret_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "install" / "secret-patterns.json"
            override.parent.mkdir()
            override.write_text('{"patterns": []}', encoding="utf-8")
            script = (
                "from portwright.memory import SECRET_PATTERNS; "
                "print(any(p.search('SOME_API_TOKEN=' + 'a' * 12) for p in SECRET_PATTERNS))"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                env={"PORTWRIGHT_HOME": directory, "PYTHONPATH": str(ROOT / "lib")},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "True")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_patterns_compile_in_javascript(self):
        definitions = [
            json.loads((ROOT / "install" / name).read_text())
            for name in ("secret-patterns.json", "identifier-policy.json")
        ]
        script = "for (const p of JSON.parse(process.argv[1]).patterns) new RegExp(p.regex, p.flags || '');"
        for policy in definitions:
            result = subprocess.run(["node", "-e", script, json.dumps(policy)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
