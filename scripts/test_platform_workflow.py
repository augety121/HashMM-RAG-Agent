"""Synthetic privacy regressions: no private repository or credential is used."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/private-platform-build.yml'
DOCUMENT = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
STEPS = DOCUMENT['jobs']['build']['steps']
ENCRYPT = next(step['run'] for step in STEPS if step.get('id') == 'sealed')


class WorkflowPrivacyTests(unittest.TestCase):
    def test_all_python_steps_compile(self):
        for step in STEPS:
            if step.get('shell') == 'python':
                compile(step['run'], '<workflow>', 'exec')

    def test_only_encrypted_attachment_is_uploaded(self):
        uploads = [s for s in STEPS if s.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]['with']['path'], '${{ runner.temp }}/private-platform.tar.age')
        self.assertIn("steps.sealed.outcome == 'success'", uploads[0]['if'])

    def execute_encryption(self, success):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'private-platform.log').write_text('synthetic confidential failure', encoding='utf-8')
            def encrypt(argv, **kwargs):
                # Simulate age writing partial output even when it fails.
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
                self.assertEqual((root / 'private-platform.tar.age').exists(), success)
                self.assertTrue((root / 'private-platform.log').exists())
            finally:
                os.chdir(old)

    def test_encryption_success_removes_plain_archive(self):
        self.execute_encryption(True)

    def test_encryption_failure_removes_partial_ciphertext_and_plain_archive(self):
        self.execute_encryption(False)


if __name__ == '__main__':
    unittest.main()
