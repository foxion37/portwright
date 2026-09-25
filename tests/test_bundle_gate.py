from __future__ import annotations

import importlib.util
import sys
import unittest
import json
import tempfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('bundle_gate_under_test', ROOT / 'scripts/jev_bundle_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class PersonalGateTests(unittest.TestCase):
    def setUp(self):
        self.approved = {'sample': {'name': 'sample', 'files': [
            {'path': 'SKILL.md', 'sha256': 'a' * 64, 'bytes': 20},
            {'path': 'references/guide.md', 'sha256': 'b' * 64, 'bytes': 30},
        ]}}
        self.entry = {'path': 'personal/sample/SKILL.md', 'personal': True, 'files': [
            {'path': 'personal/sample/SKILL.md', 'sha256': 'a' * 64, 'size': 20},
            {'path': 'personal/sample/references/guide.md', 'sha256': 'b' * 64, 'size': 30},
        ]}

    def test_approval_covers_the_complete_package(self):
        self.assertTrue(gate.personal_approved(self.entry, self.approved))

    def test_changed_supporting_file_requires_new_approval(self):
        self.entry['files'][1]['sha256'] = 'c' * 64
        self.assertFalse(gate.personal_approved(self.entry, self.approved))

    def test_added_or_missing_resources_are_not_implicitly_approved(self):
        self.entry['files'].pop()
        self.assertFalse(gate.personal_approved(self.entry, self.approved))

    def test_source_name_alone_does_not_grant_publication(self):
        self.entry['path'] = 'sample/SKILL.md'
        self.assertFalse(gate.personal_approved(self.entry, self.approved))

    def test_missing_or_relabelled_approved_package_stops_before_any_model_use(self):
        for inventory in (
            {'skills': {}},
            {'skills': {'sample': {**self.entry, 'path': 'sample/SKILL.md', 'personal': False}}},
        ):
            with self.subTest(inventory=inventory), tempfile.TemporaryDirectory() as directory:
                approval = Path(directory) / 'approval.json'
                approval.write_text(json.dumps({'skills': list(self.approved.values())}))
                with patch.object(gate, 'PERSONAL_APPROVAL', approval), patch.object(gate, 'ALLOW_FILE', Path(directory) / 'absent'), patch.object(gate, 'validated_inventory', return_value=inventory), patch.object(gate.JevClient, 'from_env', side_effect=AssertionError('model path reached')):
                    with self.assertRaises(SystemExit):
                        gate.main([directory])

    def test_public_identifier_is_refused_before_model_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "private host " + ".".join(("100", "64", "0", "1"))
            path = root / "public" / "SKILL.md"
            path.parent.mkdir()
            path.write_text(text)
            inventory = {"skills": {"public": {"path": "public/SKILL.md",
                                                "files": [{"path": "public/SKILL.md"}]}}}
            with patch.object(gate, "PERSONAL_APPROVAL", root / "absent"), \
                    patch.object(gate, "validated_inventory", return_value=inventory), \
                    patch.object(gate.JevClient, "from_env") as client:
                client.return_value.ask.side_effect = AssertionError("identifier reached model")
                self.assertEqual(gate.main([directory]), 1)
                client.return_value.ask.assert_not_called()
