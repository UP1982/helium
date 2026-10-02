"""Isolated recovery tests; never invoke codesign, ps, or installed Helium."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('repair_helium.py')
spec = importlib.util.spec_from_file_location('repair', SCRIPT)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


@unittest.skipUnless(sys.platform == 'darwin', 'Exercises macOS RENAME_SWAP')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = root/'Applications/Helium.app'
        self.state = root/'State'
        self.data = root/'Profile'
        self.stage = self.state/'Prepared Helium.app'
        self.backup = self.state/'Backup saved'
        for path, content in [(self.app, 'installed'), (self.stage, 'prepared'),
                              (self.backup/'Helium.app', 'saved')]:
            (path/'Contents/MacOS').mkdir(parents=True)
            (path/'Contents/MacOS/Helium').write_text(content)
        self.data.mkdir()
        (self.data/'History-wal').write_text('current browsing data')
        (self.state/'latest-backup.txt').write_text(str(self.backup))
        (self.state/'prepared.json').write_text(json.dumps({
            'original_sha256': repair.sha(self.app/'Contents/MacOS/Helium')}))
        for name, value in [('APP', self.app), ('STATE', self.state),
                            ('DATA', self.data), ('STAGED', self.stage)]:
            mock = patch.object(repair, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(repair, 'run', return_value='')
        self.run = mock.start()
        self.addCleanup(mock.stop)
        mock = patch.object(repair.subprocess, 'run')
        mock.start()
        self.addCleanup(mock.stop)

    def content(self, app=None):
        return ((app or self.app)/'Contents/MacOS/Helium').read_text()

    def assert_original(self):
        self.assertEqual(self.content(), 'installed')
        self.assertEqual(self.content(self.backup/'Helium.app'), 'saved')
        self.assertEqual((self.data/'History-wal').read_text(), 'current browsing data')

    def test_restore_success_keeps_previous_app_and_profile(self):
        repair.restore()
        self.assertEqual(self.content(), 'saved')
        previous = list(self.app.parent.glob('.helium-restore-*/Helium.app'))
        self.assertEqual(len(previous), 1)
        self.assertEqual(self.content(previous[0]), 'installed')
        self.assertEqual((self.data/'History-wal').read_text(), 'current browsing data')
        self.assertEqual((self.state/'latest-backup.txt').read_text(), str(self.backup))

    def test_partial_copy_failure_or_interrupt_keeps_original_and_retry_works(self):
        for failure in [OSError('disk full'), KeyboardInterrupt(), SystemExit(15)]:
            with self.subTest(failure=type(failure).__name__):
                def partial(source, target, **kwargs):
                    target.mkdir()
                    (target/'partial').write_text('incomplete')
                    raise failure
                with patch.object(repair.shutil, 'copytree', side_effect=partial):
                    with self.assertRaises(type(failure)):
                        repair.restore()
                self.assert_original()
        repair.restore()
        self.assertEqual(self.content(), 'saved')

    def test_candidate_verification_failure_keeps_original(self):
        def verify(*args):
            if args[0] == '/usr/bin/codesign' and '.helium-restore-' in args[-1]:
                raise RuntimeError('invalid candidate')
            return ''
        self.run.side_effect = verify
        with self.assertRaises(RuntimeError):
            repair.restore()
        self.assert_original()

    def test_exchange_failure_keeps_original(self):
        with patch.object(repair, 'exchange_apps', side_effect=OSError('unsupported')):
            with self.assertRaises(OSError):
                repair.restore()
        self.assert_original()

    def test_post_exchange_verification_failure_or_interrupt_rolls_back(self):
        for failure in [RuntimeError('verification failed'), KeyboardInterrupt(), SystemExit(15)]:
            with self.subTest(failure=type(failure).__name__):
                def verify(*args):
                    if args[0] == '/usr/bin/codesign' and args[-1] == str(self.app):
                        raise failure
                    return ''
                self.run.side_effect = verify
                with self.assertRaises(type(failure)):
                    repair.restore()
                self.assert_original()

    def test_missing_backup_keeps_original(self):
        self.run.side_effect = lambda *args: ''
        (self.state/'latest-backup.txt').write_text(str(self.state/'missing'))
        with self.assertRaises(FileNotFoundError):
            repair.restore()
        self.assert_original()

    def test_processes_at_any_location_and_helpers_block_apply_and_restore(self):
        for executable in ['/Applications/Helium.app/Contents/MacOS/Helium',
                           '/Volumes/Other/Helium copy.app/Contents/MacOS/Helium',
                           '/tmp/Test.app/Contents/Frameworks/Helium Framework.framework/Versions/1/Helpers/Helium Helper (Renderer).app/Contents/MacOS/Helium Helper (Renderer)',
                           'Helium Helper', 'Helium Helper (GPU)', 'Helium Helper (Plugin)']:
            for action in [repair.apply, repair.restore]:
                with self.subTest(executable=executable, action=action.__name__):
                    self.run.return_value = executable + '\n'
                    with self.assertRaisesRegex(RuntimeError, 'Quit all Helium'):
                        action()
                    self.assert_original()

    def test_unrelated_executables_allowed(self):
        self.run.return_value = '/usr/bin/python3\n/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\nHeliumUpdater\n'
        repair.ensure_closed()

    def test_process_inspection_failure_fails_closed(self):
        self.run.side_effect = RuntimeError('ps denied')
        with self.assertRaises(RuntimeError):
            repair.restore()
        self.assert_original()

    def test_process_start_during_staging_blocks_exchange(self):
        checks = 0
        def inspect(*args):
            nonlocal checks
            if args[0] == '/bin/ps':
                checks += 1
                return '' if checks == 1 else 'Helium Helper (Renderer)\n'
            return ''
        self.run.side_effect = inspect
        with self.assertRaises(RuntimeError):
            repair.restore()
        self.assert_original()

    def test_backup_pointer_persisted_before_exchange(self):
        original_exchange = repair.exchange_apps
        def exchange(first, second):
            backup = Path((self.state/'latest-backup.txt').read_text().strip())
            self.assertNotEqual(backup, self.backup)
            self.assertEqual(self.content(backup/'Helium.app'), 'installed')
            self.assertEqual((backup/'User Data/History-wal').read_text(), 'current browsing data')
            original_exchange(first, second)
        with patch.object(repair, 'exchange_apps', side_effect=exchange):
            repair.apply()
        self.assertEqual(self.content(), 'prepared')
        repair.restore()
        self.assertEqual(self.content(), 'installed')

    def test_backup_pointer_failure_keeps_old_pointer_and_installation(self):
        for failure_point in ['fsync', 'replace']:
            with self.subTest(failure_point=failure_point):
                # Isolate timestamps so failed backups cannot collide on retry.
                with tempfile.TemporaryDirectory(dir=self.state) as directory:
                    backup = Path(directory)
                    if failure_point == 'fsync':
                        mock = patch.object(repair.os, 'fsync', side_effect=OSError('disk full'))
                    else:
                        mock = patch.object(Path, 'replace', side_effect=OSError('permission denied'))
                    with mock, self.assertRaises(OSError):
                        repair.record_backup(backup)
                self.assertEqual((self.state/'latest-backup.txt').read_text(), str(self.backup))
                self.assert_original()
                self.assertEqual(list(self.state.glob('backup-pointer-*')), [])

    def test_apply_pointer_failure_prevents_swap(self):
        with patch.object(repair, 'record_backup', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                repair.apply()
        self.assert_original()

    def test_partial_app_backup_never_updates_pointer(self):
        copy = repair.shutil.copytree
        def partial(source, target, **kwargs):
            if source == self.app:
                target.mkdir()
                raise OSError('backup failed')
            return copy(source, target, **kwargs)
        with patch.object(repair.shutil, 'copytree', side_effect=partial):
            with self.assertRaises(OSError):
                repair.apply()
        self.assertEqual((self.state/'latest-backup.txt').read_text(), str(self.backup))
        self.assert_original()

    def test_abrupt_kill_during_copy_or_after_exchange_preserves_installation(self):
        # Child process redirects all globals to this fixture; verification and
        # process listing are mocked. RENAME_SWAP itself runs on real temp dirs.
        harness = '''
import importlib.util, os, signal, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('repair', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.APP = Path(sys.argv[2]); m.STATE = Path(sys.argv[3]); m.DATA = Path(sys.argv[4])
m.STAGED = m.STATE/'Prepared Helium.app'
m.run = lambda *args: ''
m.subprocess.run = lambda *args, **kwargs: None
if sys.argv[5].endswith('copy'):
    copy = m.shutil.copytree
    def interrupted(source, target, *args, **kwargs):
        if '.helium-' not in str(target):
            return copy(source, target, *args, **kwargs)
        target.mkdir(); (target/'partial').write_text('partial')
        os.kill(os.getpid(), signal.SIGKILL)
    m.shutil.copytree = interrupted
else:
    exchange = m.exchange_apps
    def interrupted(first, second):
        exchange(first, second)
        os.kill(os.getpid(), signal.SIGKILL)
    m.exchange_apps = interrupted
(m.apply if sys.argv[5].startswith('apply-') else m.restore)()
'''
        # The subprocess module is shared with repair; undo only that mock here.
        with patch.object(subprocess, 'run', wraps=REAL_SUBPROCESS_RUN):
            for phase in ['copy', 'exchange', 'apply-copy', 'apply-exchange']:
                with self.subTest(phase=phase):
                    (self.state/'prepared.json').write_text(json.dumps({
                        'original_sha256': repair.sha(self.app/'Contents/MacOS/Helium')}))
                    result = subprocess.run([sys.executable, '-c', harness, str(SCRIPT),
                                             str(self.app), str(self.state), str(self.data), phase],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                    expected = {'copy': 'installed', 'exchange': 'saved',
                                'apply-copy': 'saved', 'apply-exchange': 'prepared'}
                    self.assertEqual(self.content(), expected[phase])
                    if phase.startswith('apply-'):
                        latest = Path((self.state/'latest-backup.txt').read_text().strip())
                        self.assertEqual(self.content(latest/'Helium.app'), 'saved')
                        repair.restore()
                        self.assertEqual(self.content(), 'saved')
                    self.assertEqual(self.content(self.backup/'Helium.app'), 'saved')
            previous = [p for p in self.app.parent.glob('.helium-restore-*/Helium.app')
                        if (p/'Contents/MacOS/Helium').exists()]
            self.assertTrue(any(self.content(p) == 'installed' for p in previous))


REAL_SUBPROCESS_RUN = subprocess.run
if __name__ == '__main__':
    unittest.main()
