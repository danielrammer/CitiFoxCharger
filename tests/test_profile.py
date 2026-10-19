import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from citifoxcharger.profile import default_data_dir
from citifoxcharger import release_info,__version__
from types import SimpleNamespace
import json


class ReleaseTests(unittest.TestCase):
    def test_git_identity_is_scoped_to_program_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.git').mkdir()
            with patch('subprocess.run',side_effect=[SimpleNamespace(stdout=str(root)),SimpleNamespace(stdout='a'*40+'\n2026-10-19\n')]):
                value=release_info(root)
            self.assertEqual(value,{'version':__version__,'git_hash':'a'*8,'date':'2026-10-19'})

    def test_zip_identity_and_invalid_metadata_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'citifoxcharger').mkdir()
            path=root/'citifoxcharger/build-info.json'
            path.write_text(json.dumps({'version':__version__,'git_hash':'b'*40,'date':'2026-10-19'}))
            self.assertEqual(release_info(root)['git_hash'],'b'*8)
            path.write_text(json.dumps({'version':__version__,'git_hash':'b'*40,'date':'bad-date'}))
            self.assertIsNone(release_info(root)['git_hash'])
            path.write_text(json.dumps({'version':'0.0.0','git_hash':'b'*40,'date':'2026-10-19'}))
            self.assertIsNone(release_info(root)['git_hash'])

    def test_missing_git_never_blocks_startup_or_invents_a_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.git').mkdir()
            with patch('subprocess.run',side_effect=OSError('git missing')):
                self.assertIsNone(release_info(root)['git_hash'])


class ProfileTests(unittest.TestCase):
    def test_new_installation_uses_english_folder(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LOCALAPPDATA':directory}):
            self.assertEqual(default_data_dir(),Path(directory)/'CitiFoxCharger')

    def test_existing_profile_keeps_credentials_and_instance_lock_in_place(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LOCALAPPDATA':directory}):
            legacy=Path(directory)/'Ladezentrale';legacy.mkdir()
            (legacy/'skoda-key.dpapi').write_bytes(b'legacy-private-test-value')
            self.assertEqual(default_data_dir(),legacy)
            self.assertEqual((legacy/'skoda-key.dpapi').read_bytes(),b'legacy-private-test-value')
            self.assertFalse((Path(directory)/'CitiFoxCharger').exists())

    def test_existing_legacy_profile_has_priority_over_another_folder(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LOCALAPPDATA':directory}):
            legacy=Path(directory)/'Ladezentrale';legacy.mkdir()
            (Path(directory)/'CitiFoxCharger').mkdir()
            self.assertEqual(default_data_dir(),legacy)
