"""Synthetic privacy regressions: no private repository or credential is used."""
import os
import io
import json
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import patch

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/private-platform-build.yml'
DOCUMENT = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
STEPS = DOCUMENT['jobs']['build']['steps']
ENCRYPT = next(step['run'] for step in STEPS if step.get('id') == 'sealed')
ADMIT = next(step['run'] for step in STEPS if step.get('id') == 'admission')
REPORT = next(step['run'] for step in STEPS if step.get('name') == 'Report candidate status privately')


class WorkflowPrivacyTests(unittest.TestCase):
    def admission(self, state, merged, status='ahead', wrong_head=False):
        sha = 'a' * 40
        pr = {'state': state, 'merged_at': '2026-01-01' if merged else None, 'merge_commit_sha': 'c' * 40,
              'head': {'sha': 'b' * 40 if wrong_head else sha, 'repo': {'full_name': 'augety121/hashmm'}}}
        responses = [io.BytesIO(json.dumps(pr).encode()), io.BytesIO(json.dumps({'status': status, 'base_commit': {'sha': sha}}).encode())]
        with patch.dict(os.environ, {'SOURCE_SHA': sha, 'PR_NUMBER': '7', 'READ_TOKEN': 'synthetic', 'RECIPIENT': 'age1' + 'a' * 58, 'ACTIONS_STEP_DEBUG': 'false'}), patch('urllib.request.urlopen', side_effect=responses):
            exec(compile(ADMIT, '<admit>', 'exec'), {})

    def test_exact_open_head_is_admitted(self):
        self.admission('open', False)

    def test_changed_open_head_is_rejected(self):
        with self.assertRaises(SystemExit): self.admission('open', False, wrong_head=True)

    def test_merged_ancestor_is_admitted(self):
        self.admission('closed', True)

    def test_unmerged_closed_pr_is_rejected(self):
        with self.assertRaises(SystemExit): self.admission('closed', False)

    def test_merged_commit_removed_from_main_is_rejected(self):
        with self.assertRaises(SystemExit): self.admission('closed', True, status='diverged')

    def test_upload_failure_cannot_report_success(self):
        with patch.dict(os.environ, {'BUILD_OUTCOME': 'success', 'SEAL_OUTCOME': 'success',
                'DELIVERY_OUTCOME': 'failure', 'DIAGNOSTIC_OUTCOME': 'success', 'TARGET': 'ios', 'ARCH': 'arm64', 'STATUS_TOKEN': 'synthetic', 'SOURCE_SHA': 'a'*40}), patch('urllib.request.urlopen', return_value=io.BytesIO(b'{}')) as call:
            exec(compile(REPORT, '<report>', 'exec'), {})
            self.assertEqual(json.loads(call.call_args.args[0].data)['state'], 'failure')

    def test_all_python_steps_compile(self):
        for step in STEPS:
            if step.get('shell') == 'python':
                compile(step['run'], '<workflow>', 'exec')

    def test_only_encrypted_attachment_is_uploaded(self):
        uploads = [s for s in STEPS if s.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertEqual(len(uploads), 2)
        self.assertEqual({step['with']['path'] for step in uploads},
                         {'${{ runner.temp }}/private-platform.tar.age', '${{ runner.temp }}/private-diagnostics.tar.age'})
        for step in uploads: self.assertIn("steps.sealed.outcome == 'success'", step['if'])

    def execute_encryption(self, success):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'private-platform.log').write_text('synthetic confidential failure', encoding='utf-8')
            candidate = root / '.delivery/run/macos/candidate.dmg'
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b'synthetic candidate')
            (candidate.parent / 'unpacked-intermediate.bin').write_bytes(b'not a deliverable')
            (root / '.delivery/run/build-0.log').write_text('synthetic diagnostic')
            (root / '.delivery/run/receipt.json').write_text(json.dumps({'targets': [
                {'status': 'built_not_installed', 'target': 'macos',
                 'artifacts': [{'path': '.delivery/run/macos/candidate.dmg'}]}]}))
            sealed_members = {}
            def encrypt(argv, **kwargs):
                # Simulate age writing partial output even when it fails.
                with tarfile.open(argv[-1]) as archive:
                    sealed_members[Path(argv[-1]).name] = archive.getnames()
                Path(argv[argv.index('-o') + 1]).write_bytes(b'ciphertext' if success else b'partial')
                return subprocess.CompletedProcess(argv, 0 if success else 1)
            old = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {'RUNNER_TEMP': directory, 'RECIPIENT': 'synthetic-public-recipient'}), patch('subprocess.run', side_effect=encrypt):
                    if success:
                        exec(compile(ENCRYPT, '<encryption>', 'exec'), {})
                    else:
                        with self.assertRaises(SystemExit):
                            exec(compile(ENCRYPT, '<encryption>', 'exec'), {})
                self.assertFalse((root / 'private-platform.tar').exists())
                self.assertFalse((root / 'private-diagnostics.tar').exists())
                self.assertEqual((root / 'private-platform.tar.age').exists(), success)
                self.assertEqual((root / 'private-diagnostics.tar.age').exists(), success)
                self.assertTrue((root / 'private-platform.log').exists())
                if success:
                    self.assertIn('delivery/run/macos/candidate.dmg', sealed_members['private-platform.tar'])
                    self.assertNotIn('delivery/run/macos/candidate.dmg', sealed_members['private-diagnostics.tar'])
                    for members in sealed_members.values():
                        self.assertIn('delivery/run/receipt.json', members)
                        self.assertNotIn('delivery/run/macos/unpacked-intermediate.bin', members)
            finally:
                os.chdir(old)

    def test_encryption_success_removes_plain_archive(self):
        self.execute_encryption(True)

    def test_encryption_failure_removes_partial_ciphertext_and_plain_archive(self):
        self.execute_encryption(False)


if __name__ == '__main__':
    unittest.main()
