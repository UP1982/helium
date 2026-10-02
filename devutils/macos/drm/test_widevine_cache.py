"""Cache fault-injection tests using temporary data and mocked signature checks."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import copy_widevine_from_chrome as chrome_copy
import repair_helium as repair
import widevine_cache as cache

VERSION = '4.10.3112.0'
REAL_COPYTREE = shutil.copytree
REAL_RUN = subprocess.run


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root/'Profile/WidevineCdm'
        self.source = self.profile/VERSION
        self.cache_root = self.root/'Verified Widevine'
        self.target = self.cache_root/VERSION
        self.make_module(self.source)
        self.verify = patch.object(cache, 'verify_google').start()
        self.addCleanup(patch.stopall)
        # An unexpected external command is a test failure, never a real action.
        patch.object(subprocess, 'run', side_effect=AssertionError('Unexpected external command')).start()

    def make_module(self, path, version=VERSION):
        (path/cache.LIBRARY).parent.mkdir(parents=True)
        (path/cache.LIBRARY).write_bytes(b'fixture signed library')
        (path/'LICENSE').write_text('fixture license')
        (path/'manifest.json').write_text(json.dumps({
            'name': 'WidevineCdm', 'version': version,
            'x-cdm-module-versions': '4', 'x-cdm-interface-versions': '10',
            'x-cdm-host-versions': '10,11'}))
        return path

    def publish(self):
        return cache.cache_module(self.source, self.cache_root, VERSION)

    def partial_target(self, with_manifest=False):
        (self.target/cache.LIBRARY).parent.mkdir(parents=True)
        shutil.copyfile(self.source/cache.LIBRARY, self.target/cache.LIBRARY)
        if with_manifest:
            shutil.copyfile(self.source/'manifest.json', self.target/'manifest.json')

    def test_new_cache_and_idempotent_reuse_match_every_source_file(self):
        self.assertEqual(self.publish(), self.target)
        self.assertEqual(cache.validate_cache(self.target, VERSION), cache.inventory(self.source))
        with patch.object(shutil, 'copytree', side_effect=AssertionError('Unexpected recopy')):
            self.assertEqual(self.publish(), self.target)

    def test_manifest_and_library_validation(self):
        manifest = json.loads((self.source/'manifest.json').read_text())
        variants = [None, [], {}, {**manifest, 'name': 'Other'},
                    {**manifest, 'version': '../escape'},
                    {**manifest, 'version': 123},
                    {**manifest, 'version': '4.10.0.0'},
                    {**manifest, 'x-cdm-host-versions': ''},
                    {**manifest, 'x-cdm-interface-versions': None}]
        for value in variants:
            with self.subTest(value=value):
                (self.source/'manifest.json').write_text(json.dumps(value))
                with self.assertRaises(cache.InvalidModule):
                    self.publish()
                self.assertFalse(self.target.exists())
        (self.source/'manifest.json').write_text('{')
        with self.assertRaises(cache.InvalidModule):
            self.publish()
        (self.source/'manifest.json').write_text(json.dumps(manifest))
        (self.source/cache.LIBRARY).write_bytes(b'')
        with self.assertRaises(cache.InvalidModule):
            self.publish()
        (self.source/cache.LIBRARY).unlink()
        with self.assertRaises(cache.InvalidModule):
            self.publish()

    def test_signature_failure_never_publishes(self):
        self.verify.side_effect = cache.InvalidModule('Bad signature')
        with self.assertRaises(cache.InvalidModule):
            self.publish()
        self.assertFalse(self.target.exists())

    def test_links_and_special_files_are_rejected(self):
        (self.source/'linked').symlink_to(self.source/'LICENSE')
        with self.assertRaises(cache.InvalidModule):
            self.publish()
        (self.source/'linked').unlink()
        os.mkfifo(self.source/'pipe')
        with self.assertRaises(cache.InvalidModule):
            self.publish()

    def test_missing_manifest_cache_is_preserved_and_rebuilt(self):
        self.partial_target()
        self.publish()
        self.assertEqual(cache.validate_cache(self.target, VERSION), cache.inventory(self.source))
        rejected = list(self.cache_root.glob('.rejected-*/module'))
        self.assertEqual(len(rejected), 1)
        self.assertTrue((rejected[0]/cache.LIBRARY).exists())
        self.assertFalse((rejected[0]/'manifest.json').exists())

    def test_legacy_cache_with_matching_dylib_and_manifest_is_not_trusted(self):
        self.partial_target(with_manifest=True)
        with self.assertRaises(cache.InvalidModule):
            cache.validate_cache(self.target, VERSION)
        self.publish()
        self.assertTrue((self.target/'LICENSE').is_file())
        cache.validate_cache(self.target, VERSION)

    def test_damaged_receipted_cache_is_rebuilt(self):
        self.publish()
        for damage in ['missing', 'changed', 'extra', 'receipt']:
            with self.subTest(damage=damage):
                if damage == 'missing':
                    (self.target/'LICENSE').unlink()
                elif damage == 'changed':
                    (self.target/'LICENSE').write_text('incomplete')
                elif damage == 'extra':
                    (self.target/'unexpected').write_text('extra')
                else:
                    (self.target/cache.RECEIPT).write_text('{}')
                with self.assertRaises(cache.InvalidModule):
                    cache.validate_cache(self.target, VERSION)
                self.publish()
                self.assertEqual(cache.validate_cache(self.target, VERSION), cache.inventory(self.source))

    def test_cache_only_requires_receipt_and_all_recorded_files(self):
        self.partial_target(with_manifest=True)
        empty_profile = self.root/'Absent profile'
        with self.assertRaisesRegex(cache.InvalidModule, 'reacquire'):
            cache.select_module(empty_profile, self.cache_root)
        with self.assertRaisesRegex(cache.InvalidModule, 'own repair source'):
            cache.cache_module(self.target, self.cache_root, VERSION)
        self.publish()
        self.assertEqual(cache.select_module(empty_profile, self.cache_root), self.target)
        (self.target/'LICENSE').unlink()
        with self.assertRaises(cache.InvalidModule):
            cache.select_module(empty_profile, self.cache_root)

    def test_selection_rebuilds_same_version_and_skips_incomplete_newer_cache(self):
        self.partial_target()
        newer = self.cache_root/'4.99.0.0'
        newer.mkdir()
        self.assertEqual(cache.select_module(self.profile, self.cache_root), self.target)
        cache.validate_cache(self.target, VERSION)

    def test_preparation_reuses_complete_cache_despite_different_source_layout(self):
        self.publish()
        # Chrome and profile packages may contain different ancillary files.
        (self.source/'LICENSE').unlink()
        self.assertEqual(cache.select_module(self.profile, self.cache_root), self.target)
        self.assertTrue((self.target/'LICENSE').exists())

    def test_complete_conflicting_cache_is_preserved(self):
        self.publish()
        before = cache.inventory(self.target)
        (self.source/'LICENSE').write_text('different source, same library')
        with self.assertRaisesRegex(RuntimeError, 'different complete cache'):
            self.publish()
        self.assertEqual(cache.validate_cache(self.target, VERSION), before)

    def test_partial_copy_failures_preserve_existing_partial_cache_and_retry(self):
        self.partial_target()
        before = cache.inventory(self.target)
        for failure in [OSError('disk full'), KeyboardInterrupt(), SystemExit(15)]:
            with self.subTest(failure=type(failure).__name__):
                def partial(source, target, *args, **kwargs):
                    (target/cache.LIBRARY).parent.mkdir(parents=True)
                    (target/cache.LIBRARY).write_bytes(b'copied first')
                    raise failure
                with patch.object(shutil, 'copytree', side_effect=partial):
                    with self.assertRaises(type(failure)):
                        self.publish()
                self.assertEqual(cache.inventory(self.target), before)
                self.assertFalse(list(self.cache_root.glob('.rejected-*')))
                self.assertFalse(list(self.cache_root.glob('.staging-*')))
        self.publish()
        cache.validate_cache(self.target, VERSION)

    def test_silent_incomplete_copy_is_rejected(self):
        def incomplete(source, target, *args, **kwargs):
            result = REAL_COPYTREE(source, target, *args, **kwargs)
            if Path(source) == self.source:
                (Path(target)/'LICENSE').unlink()
            return result
        with patch.object(shutil, 'copytree', side_effect=incomplete):
            with self.assertRaisesRegex(cache.InvalidModule, 'incompletely copied'):
                self.publish()
        self.assertFalse(self.target.exists())

    def test_source_change_during_copy_is_rejected(self):
        def changing(source, target, *args, **kwargs):
            result = REAL_COPYTREE(source, target, *args, **kwargs)
            if Path(source) == self.source:
                (self.source/'LICENSE').write_text('updated during copy')
            return result
        with patch.object(shutil, 'copytree', side_effect=changing):
            with self.assertRaisesRegex(cache.InvalidModule, 'incompletely copied'):
                self.publish()
        self.assertFalse(self.target.exists())
        self.publish()
        cache.validate_cache(self.target, VERSION)

    def test_receipt_write_and_publication_failures_are_retryable(self):
        original_write = Path.write_text
        original_rename = Path.rename
        for point in ['receipt', 'publish']:
            with self.subTest(point=point):
                def write(path, *args, **kwargs):
                    if path.name == cache.RECEIPT:
                        raise OSError('disk full')
                    return original_write(path, *args, **kwargs)
                def rename(path, target):
                    if path.parent.name.startswith('.staging-'):
                        raise OSError('rename failed')
                    return original_rename(path, target)
                mock = patch.object(Path, 'write_text', write) if point == 'receipt' else patch.object(Path, 'rename', rename)
                with mock, self.assertRaises(OSError):
                    self.publish()
                self.assertFalse(self.target.exists())
        self.publish()
        cache.validate_cache(self.target, VERSION)

    def test_chrome_helper_rebuilds_incomplete_cache(self):
        chrome = self.root/'Chrome.app'
        source = chrome/'Contents/Frameworks/Google Chrome Framework.framework/Versions/123/Libraries/WidevineCdm'
        self.make_module(source)
        self.partial_target(with_manifest=True)
        with patch.object(chrome_copy, 'verify_google') as verify:
            self.assertEqual(chrome_copy.copy_from_chrome(chrome, self.cache_root), self.target)
            verify.assert_called_once_with(chrome)
        self.assertEqual(cache.validate_cache(self.target, VERSION), cache.inventory(source))

    def test_chrome_helper_rejects_incomplete_source(self):
        chrome = self.root/'Chrome.app'
        source = chrome/'Contents/Frameworks/Google Chrome Framework.framework/Versions/123/Libraries/WidevineCdm'
        self.make_module(source)
        (source/'manifest.json').unlink()
        with patch.object(chrome_copy, 'verify_google'):
            with self.assertRaises(cache.InvalidModule):
                chrome_copy.copy_from_chrome(chrome, self.cache_root)
        self.assertFalse(self.target.exists())

    def test_prepare_rebuilds_partial_cache_and_validates_bundled_tree(self):
        state = self.root/'State'
        app = self.root/'Helium.app'
        framework = app/'Contents/Frameworks/Helium Framework.framework/Versions'
        (framework/'1/Libraries').mkdir(parents=True)
        (framework/'1/Helpers').mkdir()
        (framework/'Current').symlink_to('1')
        (app/'Contents/MacOS').mkdir()
        (app/'Contents/MacOS/Helium').write_text('original app')
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': '1'}))
        self.cache_root = state/'Verified Widevine'
        self.target = self.cache_root/VERSION
        self.partial_target()
        with patch.object(repair, 'APP', app), patch.object(repair, 'STATE', state), \
             patch.object(repair, 'DATA', self.profile.parent), \
             patch.object(repair, 'STAGED', state/'Prepared Helium.app'), \
             patch.object(repair, 'run', return_value=''), \
             patch.object(repair, 'signature', return_value='TeamIdentifier=S4Q33XPHB4'), \
             patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout=b'')):
            repair.prepare()
        bundled = state/'Prepared Helium.app/Contents/Frameworks/Helium Framework.framework/Versions/1/Libraries/WidevineCdm'
        self.assertEqual(cache.validate_cache(bundled, VERSION), cache.inventory(self.source))
        self.assertEqual((app/'Contents/MacOS/Helium').read_text(), 'original app')

    def test_prepare_stops_before_staging_when_only_partial_cache_exists(self):
        state = self.root/'State'
        self.cache_root = state/'Verified Widevine'
        self.target = self.cache_root/VERSION
        self.partial_target(with_manifest=True)
        with patch.object(repair, 'STATE', state), \
             patch.object(repair, 'DATA', self.root/'Missing profile'), \
             patch.object(repair, 'run', return_value='') as run, \
             patch.object(repair, 'signature', return_value='TeamIdentifier=S4Q33XPHB4'), \
             patch.object(shutil, 'copytree', side_effect=AssertionError('Must not stage')):
            with self.assertRaises(cache.InvalidModule):
                repair.prepare()
        self.assertEqual(run.call_count, 1)  # Read-only app signature check only.

    @unittest.skipUnless(os.name == 'posix', 'Uses SIGKILL and flock')
    def test_abrupt_termination_during_copy_quarantine_and_publish_is_retryable(self):
        harness = '''
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import widevine_cache as m
m.verify_google = lambda path: None
phase = sys.argv[4]
if phase == 'copy':
    def interrupted(source, target, *args, **kwargs):
        (target/m.LIBRARY).parent.mkdir(parents=True)
        (target/m.LIBRARY).write_bytes(b'partial')
        os.kill(os.getpid(), signal.SIGKILL)
    m.shutil.copytree = interrupted
else:
    rename = Path.rename
    def interrupted(path, target):
        result = rename(path, target)
        if (phase == 'quarantine' and Path(target).parent.name.startswith('.rejected-')) or \
           (phase == 'publish' and path.parent.name.startswith('.staging-')):
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    Path.rename = interrupted
m.cache_module(Path(sys.argv[2]), Path(sys.argv[3]))
'''
        for phase in ['copy', 'quarantine', 'publish']:
            with self.subTest(phase=phase):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    cache_root = Path(directory)
                    target = cache_root/VERSION
                    if phase == 'quarantine':
                        target.mkdir()
                        (target/'partial').write_text('old partial cache')
                    result = REAL_RUN([sys.executable, '-c', harness, str(Path(__file__).parent),
                                       str(self.source), str(cache_root), phase],
                                      capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                    if phase == 'publish':
                        cache.validate_cache(target, VERSION)
                    else:
                        self.assertFalse(target.exists())
                    # The dead process releases the publication lock automatically.
                    cache.cache_module(self.source, cache_root)
                    self.assertEqual(cache.validate_cache(target, VERSION), cache.inventory(self.source))


if __name__ == '__main__':
    unittest.main()
