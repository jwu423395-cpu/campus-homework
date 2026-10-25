import json
import os
from pathlib import Path
import tempfile
import unittest
import warnings
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from campus_homework import environment, fileio


class EnvironmentTests(unittest.TestCase):
    def test_profile_defaults_and_round_trip_are_local(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'personal' / 'profile.json'
            with patch.object(environment, 'local_profile_path', return_value=path):
                self.assertEqual(environment.load_profile(), environment.DEFAULT_PROFILE)
                self.assertEqual(environment.save_profile({'paper': 'ruled', 'font': 'chenyu', 'handwriting': 'natural', 'ink_effect': 'ballpoint'}), path)
                saved = environment.load_profile()
                self.assertEqual(saved['paper'], 'ruled')
                self.assertEqual(saved['font'], 'chenyu')
                self.assertEqual(saved['handwriting'], 'natural')
                self.assertEqual(saved['ink_effect'], 'ballpoint')
                self.assertEqual(saved['mode'], 'accurate')
                self.assertEqual(list(path.parent.glob('.profile-*.tmp')), [])

    def test_invalid_profile_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            with patch.object(environment, 'local_profile_path', return_value=path):
                environment.save_profile({'paper': 'cream'})
                original = path.read_bytes()
                for profile in ({'ink': 'red'}, {'margin': -1}, {'margin': 500}, {'font_size': 10}, {'line_spacing': .5}, {'font_size': float('nan')},
                                {'api_key': 'must-not-be-stored'}, {'mode': 'unknown'}, {'handwriting': 'unknown'}, {'ink_effect': 'unknown'}):
                    with self.assertRaises(ValueError):
                        environment.save_profile(profile)
                    self.assertEqual(path.read_bytes(), original)

    def test_photo_profile_round_trip_uses_real_local_background(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            background = Path(directory) / 'desk.png'
            Image.new('RGB', (100, 150), '#aa8866').save(background)
            corners = [[10, 10], [90, 10], [90, 140], [10, 140]]
            with patch.object(environment, 'local_profile_path', return_value=path):
                environment.save_profile({'mode': 'photo', 'photo_background': str(background), 'photo_corners': corners})
                saved = environment.load_profile()
                self.assertEqual(saved['photo_background'], str(background.resolve()))
                self.assertEqual(saved['photo_corners'], corners)
                with self.assertRaises(ValueError):
                    environment.save_profile({'photo_corners': [[1, 2]]})

    def test_failed_atomic_replace_preserves_previous_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            with patch.object(environment, 'local_profile_path', return_value=path):
                environment.save_profile({'paper': 'grid'})
                original = path.read_bytes()
                with patch.object(fileio.os, 'replace', side_effect=OSError('simulated failure')):
                    with self.assertRaises(OSError):
                        environment.save_profile({'paper': 'blank'})
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(path.parent.glob('.profile-*.tmp')), [])

    def test_windows_error17_first_profile_and_update_use_explicit_fallback(self):
        error = OSError('simulated Windows cross-device rename rejection')
        error.winerror = 17
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            with patch.object(environment, 'local_profile_path', return_value=path), \
                 patch.object(fileio.platform, 'system', return_value='Windows'), \
                 patch.object(fileio.os, 'replace', side_effect=error):
                for paper in ('grid', 'cream'):
                    with self.assertWarnsRegex(RuntimeWarning, 'non-atomic'):
                        environment.save_profile({'paper': paper, 'handwriting': 'natural'})
                    self.assertEqual(environment.load_profile()['paper'], paper)
                    self.assertEqual(environment.load_profile()['handwriting'], 'natural')
                self.assertEqual(list(path.parent.glob('.profile-*.tmp')), [])

    def test_windows_error17_partial_write_restores_previous_profile(self):
        error = OSError('simulated Windows rename rejection')
        error.winerror = 17
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            with patch.object(environment, 'local_profile_path', return_value=path):
                environment.save_profile({'paper': 'cream'})
                previous = path.read_bytes()
                writer = fileio._write_file
                def failing_writer(destination, payload):
                    if destination == path and payload != previous:
                        path.write_bytes(b'{incomplete')
                        raise OSError('simulated partial write')
                    return writer(destination, payload)
                with patch.object(fileio.platform, 'system', return_value='Windows'), \
                     patch.object(fileio.os, 'replace', side_effect=error), \
                     patch.object(fileio, '_write_file', side_effect=failing_writer):
                    with self.assertWarnsRegex(RuntimeWarning, 'non-atomic'):
                        with self.assertRaisesRegex(OSError, 'simulated partial write'):
                            environment.save_profile({'paper': 'blank'})
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(list(path.parent.glob('.profile-*.tmp')), [])

    def test_warning_as_error_aborts_fallback_before_changing_profile(self):
        error = OSError('simulated Windows rename rejection')
        error.winerror = 17
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            with patch.object(environment, 'local_profile_path', return_value=path):
                environment.save_profile({'paper': 'grid'})
                previous = path.read_bytes()
                with patch.object(fileio.platform, 'system', return_value='Windows'), \
                     patch.object(fileio.os, 'replace', side_effect=error), warnings.catch_warnings():
                    warnings.simplefilter('error', RuntimeWarning)
                    with self.assertRaisesRegex(RuntimeWarning, 'non-atomic'):
                        environment.save_profile({'paper': 'cream'})
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(list(path.parent.glob('.profile-*.tmp')), [])

    def test_failed_fallback_restoration_retains_complete_old_backup(self):
        error = OSError('simulated Windows rename rejection')
        error.winerror = 17
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            with patch.object(environment, 'local_profile_path', return_value=path):
                environment.save_profile({'paper': 'grid'})
                previous = path.read_bytes()
                writer = fileio._write_file
                def failing_writer(destination, payload):
                    if destination == path:
                        path.write_bytes(b'{incomplete')
                        raise OSError('simulated unavailable destination')
                    return writer(destination, payload)
                with patch.object(fileio.platform, 'system', return_value='Windows'), \
                     patch.object(fileio.os, 'replace', side_effect=error), \
                     patch.object(fileio, '_write_file', side_effect=failing_writer):
                    with self.assertWarnsRegex(RuntimeWarning, 'non-atomic'):
                        with self.assertRaisesRegex(OSError, 'previous contents are retained at'):
                            environment.save_profile({'paper': 'blank'})
                backups = list(path.parent.glob('.profile.json.backup-*.tmp'))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), previous)
                self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8'))['paper'], 'grid')

    def test_profile_path_respects_platform_configuration(self):
        with patch.object(environment.platform, 'system', return_value='Windows'):
            with patch.dict(os.environ, {'LOCALAPPDATA': 'C:/local-app-data'}):
                self.assertEqual(environment.local_profile_path(), Path('C:/local-app-data/campus-homework/profile.json'))
        with patch.object(environment.platform, 'system', return_value='Linux'):
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/config'}):
                self.assertEqual(environment.local_profile_path(), Path('/tmp/config/campus-homework/profile.json'))

    def test_bundled_fonts_have_matching_hashes_and_complete_licenses(self):
        result = environment.verify_fonts()
        self.assertTrue(result['ok'], result)
        self.assertEqual(set(result['fonts']), {'wenkai', 'yozai', 'chenyu'})
        for report in result['fonts'].values():
            self.assertTrue(report['sha256_ok'])
            self.assertTrue(report['license_ok'])
            self.assertEqual(report['coverage']['status'], 'checked')
        self.assertIn('谐', result['fonts']['chenyu']['coverage']['missing_sample'])
        self.assertEqual(result['fonts']['wenkai']['coverage']['missing_sample'], '')

    def test_doctor_reports_partial_capabilities_without_secrets(self):
        with patch.object(environment.shutil, 'which', return_value=None):
            with patch.dict(os.environ, {'CAMPUS_TEST_SECRET': 'private-do-not-print'}):
                result = environment.doctor()
        self.assertFalse(result['capabilities']['local_render']['available'])
        self.assertIsNone(result['capabilities']['image_generation']['available'])
        self.assertIn('fonts', result['checks'])
        self.assertIn('python', result['checks'])
        self.assertNotIn('private-do-not-print', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
