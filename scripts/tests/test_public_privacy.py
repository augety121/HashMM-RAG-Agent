import importlib.util
import io
from pathlib import Path
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('privacy', Path(__file__).parents[1] / 'check_public_privacy.py')
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


class PrivacyTests(unittest.TestCase):
    def test_detects_credentials_without_returning_value(self):
        value = b'sb_' + b'secret_' + b'x' * 32
        result = privacy.scan('config.txt', value)
        self.assertEqual(result, [('config.txt', 'supabase-key')])
        self.assertNotIn(value.decode(), repr(result))

    def test_examples_and_copyright_are_allowed(self):
        self.assertEqual(privacy.scan('.env.example', b'KEY=YOUR_SUPABASE_PUBLISHABLE_KEY\nCopyright author@example.com'), [])

    def test_nested_archive_is_checked(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, 'w') as archive:
            archive.writestr('.env', 'PASSWORD=example')
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, 'w') as archive:
            archive.writestr('nested.zip', inner.getvalue())
        self.assertIn(('bundle.zip!nested.zip!.env', 'runtime-env'), privacy.scan('bundle.zip', outer.getvalue()))

    def test_unreadable_archive_fails_closed(self):
        self.assertEqual(privacy.scan('bundle.zip', b'broken'), [('bundle.zip', 'unreadable-archive')])

    def test_signing_identity_is_rejected(self):
        self.assertEqual(privacy.scan('release.jks', b''), [('release.jks', 'private-config-file')])
