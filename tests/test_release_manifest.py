"""Test provenance guard and dependency lock without touching build artifacts."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_manifest', Path(__file__).resolve().parents[1] / 'scripts' / 'release_manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


class ReleaseManifestTests(unittest.TestCase):
    @patch.object(manifest.importlib.metadata, 'version', return_value='test-builder')
    def test_dirty_source_refused(self, _builder):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(sys, 'argv', ['manifest', '--app-dir', temp]), patch.object(manifest, 'git', return_value='modified'):
                with self.assertRaisesRegex(SystemExit, 'uncommitted'):
                    manifest.main()
            self.assertFalse((Path(temp) / 'build-info.json').exists())

    @patch.object(manifest.importlib.metadata, 'version', return_value='test-builder')
    def test_hashes_and_source_identity(self, _builder):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'sample.txt').write_bytes(b'known release data')
            def git(*args):
                return '' if args[0] == 'status' else 'abcd1234'
            with patch.object(sys, 'argv', ['manifest', '--app-dir', temp]), patch.object(manifest, 'git', side_effect=git):
                manifest.main()
            result = json.loads((root / 'release-manifest.json').read_text())
            self.assertEqual(result['source_commit'], 'abcd1234')
            self.assertEqual(result['files']['sample.txt']['sha256'], hashlib.sha256(b'known release data').hexdigest())
            self.assertIn('build-info.json', result['files'])
            self.assertNotIn('release-manifest.json', result['files'])

    def test_pillow_is_in_runtime_dependency_closure(self):
        self.assertIn('pillow', manifest.runtime_dependencies())


if __name__ == '__main__':
    unittest.main()
