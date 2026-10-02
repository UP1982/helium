#!/usr/bin/env python3
"""Prepare, apply, or restore the locally verified Helium DRM workaround."""
import argparse
import datetime
import ctypes
import os
import re
import hashlib
import json
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from widevine_cache import select_module, validate_cache

APP = Path('/Applications/Helium.app')
DATA = Path.home()/'Library/Application Support/net.imput.helium'
STATE = Path.home()/'Library/Application Support/Helium DRM Repair'
STAGED = STATE/'Prepared Helium.app'

def run(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout

def signature(path):
    return subprocess.run(['/usr/bin/codesign', '-dv', '--verbose=4', str(path)], capture_output=True, text=True).stderr

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def ensure_closed():
    # comm contains the executable, without arguments. Check the executable name
    # regardless of bundle location, including orphaned Chromium helpers.
    executables = run('/bin/ps', 'ax', '-o', 'comm=').splitlines()
    if any(re.fullmatch(r'Helium(?: Helper(?: \([^)]+\))?)?',
                        Path(c.strip()).name) for c in executables):
        raise RuntimeError('Quit all Helium copies and helpers first. This tool does not close your tabs for you.')


def record_backup(backup):
    # Never truncate the existing pointer, and fail before changing the app if
    # persistence fails. Only verified, complete backups reach this function.
    with tempfile.NamedTemporaryFile(mode='w', prefix='backup-pointer-', dir=STATE,
                                     delete=False) as pointer:
        temporary = Path(pointer.name)
        try:
            pointer.write(str(backup.resolve()) + '\n')
            pointer.flush()
            os.fsync(pointer.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(STATE/'latest-backup.txt')
    finally:
        temporary.unlink(missing_ok=True)


def exchange_apps(first, second):
    # macOS RENAME_SWAP exchanges both names in one filesystem operation. There
    # is no interval with APP missing, even if the process is killed abruptly.
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    exchange = libc.renamex_np
    exchange.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    exchange.restype = ctypes.c_int
    if exchange(os.fsencode(first), os.fsencode(second), 0x00000002):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def replace_app(source, label, clear_attributes=False):
    # A unique sibling stages on the app's volume. Keep this directory on every
    # failure: after an exchange it holds the previous, operational installation.
    directory = Path(tempfile.mkdtemp(prefix='.helium-' + label + '-', dir=APP.parent))
    incoming = directory/'Helium.app'
    print('Recovery/staging location:', incoming, flush=True)
    shutil.copytree(source, incoming, symlinks=True)
    if clear_attributes:
        for attr in ('com.apple.FinderInfo', 'com.apple.ResourceFork'):
            subprocess.run(['/usr/bin/xattr', '-rd', attr, str(incoming)], capture_output=True)
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', str(incoming))
    ensure_closed()
    exchange_apps(APP, incoming)
    try:
        run('/usr/bin/codesign', '--verify', '--deep', '--strict', str(APP))
    except BaseException:
        exchange_apps(APP, incoming)
        raise
    print('Previous installation retained at:', incoming)

def prepare():
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', str(APP))
    if 'TeamIdentifier=S4Q33XPHB4' not in signature(APP):
        raise RuntimeError('Helium is already modified or its signing identity changed. Install the current official release before repairing it.')
    STATE.mkdir(parents=True,exist_ok=True)
    module = select_module(DATA/'WidevineCdm', STATE/'Verified Widevine')
    with tempfile.TemporaryDirectory(prefix='stage-',dir=STATE) as temp:
        stage=(Path(temp)/'Helium.app').resolve()
        shutil.copytree(APP,stage,symlinks=True)
        framework=stage/'Contents/Frameworks/Helium Framework.framework'
        version=(framework/'Versions/Current').resolve()
        bundled=version/'Libraries/WidevineCdm'
        if bundled.exists():
            raise RuntimeError('Official Helium now bundles Widevine. Reassess the need for this patch.')
        shutil.copytree(module,bundled,symlinks=True)
        validate_cache(bundled, module.name)
        for item in list((version/'Helpers').glob('*.app'))+[framework,stage]:
            original=APP/item.relative_to(stage)
            r=subprocess.run(['/usr/bin/codesign','-d','--xml','--entitlements','-',str(original)],capture_output=True,check=True)
            ent=plistlib.loads(r.stdout) if r.stdout.strip() else {}
            # These groups depend on the vendor's signing identity; preserve ordinary
            # browser entitlements and renderer JIT, but do not claim the vendor identity.
            for k in ('com.apple.application-identifier','keychain-access-groups','com.apple.developer.web-browser.public-key-credential'):
                ent.pop(k,None)
            if item!=framework:
                ent['com.apple.security.cs.disable-library-validation']=True
            ep=Path(temp)/'entitlements.plist';ep.write_bytes(plistlib.dumps(ent))
            for attr in ('com.apple.FinderInfo','com.apple.ResourceFork'):
                subprocess.run(['/usr/bin/xattr','-rd',attr,str(item)],capture_output=True)
            run('/usr/bin/codesign','--force','--sign','-','--options','runtime','--entitlements',str(ep),str(item))
        run('/usr/bin/codesign','--verify','--deep','--strict',str(stage))
        if STAGED.exists():
            STAGED.rename(STATE/('Unused preparation '+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.app'))
        stage.rename(STAGED)
        (STATE/'prepared.json').write_text(json.dumps({'original_sha256':sha(APP/'Contents/MacOS/Helium'),'version':plistlib.loads((APP/'Contents/Info.plist').read_bytes())['CFBundleShortVersionString'],'widevine':module.name},indent=2)+'\n')
    print('Replacement prepared and its signatures verified. Your installed app is unchanged.')

def apply():
    ensure_closed()
    metadata=json.loads((STATE/'prepared.json').read_text())
    if metadata['original_sha256']!=sha(APP/'Contents/MacOS/Helium'):
        raise RuntimeError('Helium changed since preparation. Prepare again.')
    run('/usr/bin/codesign','--verify','--deep','--strict',str(STAGED))
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup=Path(tempfile.mkdtemp(prefix='Backup '+stamp+'-', dir=STATE))
    # Back up the full profile only after the browser is closed, including SQLite WALs.
    shutil.copytree(DATA,backup/'User Data',symlinks=True)
    shutil.copytree(APP,backup/'Helium.app',symlinks=True)
    run('/usr/bin/codesign','--verify','--deep','--strict',str(backup/'Helium.app'))
    ensure_closed()
    record_backup(backup)
    replace_app(STAGED, 'apply', clear_attributes=True)
    print('DRM patch applied. Normal Keychain access and the existing profile are retained.')
    print('App and full profile backup:',backup)

def restore():
    ensure_closed()
    backup=Path((STATE/'latest-backup.txt').read_text().strip())
    saved=backup/'Helium.app'
    run('/usr/bin/codesign','--verify','--deep','--strict',str(saved))
    replace_app(saved, 'restore')
    print('Original app restored. Your current profile was not rolled back; its backup remains available.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','apply','restore'])
    action=parser.parse_args().action
    try:globals()[action]()
    except Exception as e:raise SystemExit(str(e))
