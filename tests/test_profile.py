import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from citifoxcharger.profile import default_data_dir


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
