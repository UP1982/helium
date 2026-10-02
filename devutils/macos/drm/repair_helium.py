#!/usr/bin/env python3
"""Prepare, apply, or restore the locally verified Helium DRM workaround."""
import argparse
import datetime
import hashlib
import json
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

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
    commands = run('/bin/ps', 'ax', '-o', 'command=').splitlines()
    if any(c.startswith(str(APP/'Contents/MacOS/Helium')) for c in commands):
        raise RuntimeError('Quit your main Helium first. This tool does not close your tabs for you.')

def prepare():
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', str(APP))
    if 'TeamIdentifier=S4Q33XPHB4' not in signature(APP):
        raise RuntimeError('Helium is already modified or its signing identity changed. Install the current official release before repairing it.')
    versions = []
    for directory in (DATA/'WidevineCdm', STATE/'Verified Widevine'):
        if directory.exists():
            versions.extend(p for p in directory.iterdir() if p.name.replace('.','').isdigit() and (p/'_platform_specific/mac_arm64/libwidevinecdm.dylib').exists())
    versions.sort(key=lambda p: tuple(map(int,p.name.split('.'))))
    if not versions:
        raise RuntimeError('No installed arm64 Widevine module was found.')
    module = versions[-1]
    lib = module/'_platform_specific/mac_arm64/libwidevinecdm.dylib'
    run('/usr/bin/codesign', '--verify', '--strict', str(lib))
    if 'TeamIdentifier=EQHXZ8M8AV' not in signature(lib):
        raise RuntimeError('Widevine does not have its expected Google signature.')
    STATE.mkdir(parents=True,exist_ok=True)
    cached = STATE/'Verified Widevine'/module.name
    if not cached.exists():
        cached.parent.mkdir(exist_ok=True)
        shutil.copytree(module,cached,symlinks=True)
    module = cached
    with tempfile.TemporaryDirectory(prefix='stage-',dir=STATE) as temp:
        stage=(Path(temp)/'Helium.app').resolve()
        shutil.copytree(APP,stage,symlinks=True)
        framework=stage/'Contents/Frameworks/Helium Framework.framework'
        version=(framework/'Versions/Current').resolve()
        bundled=version/'Libraries/WidevineCdm'
        if bundled.exists():
            raise RuntimeError('Official Helium now bundles Widevine. Reassess the need for this patch.')
        shutil.copytree(module,bundled,symlinks=True)
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
    backup=STATE/('Backup '+stamp);backup.mkdir()
    # Back up the full profile only after the browser is closed, including SQLite WALs.
    shutil.copytree(DATA,backup/'User Data',symlinks=True)
    shutil.copytree(APP,backup/'Helium.app',symlinks=True)
    run('/usr/bin/codesign','--verify','--deep','--strict',str(backup/'Helium.app'))
    # Stage on the same volume for a rename; keep the previous app intact on failure.
    incoming=APP.with_name('Helium DRM Replacement.app')
    if incoming.exists():raise RuntimeError('An unfinished replacement already exists in Applications.')
    shutil.copytree(STAGED,incoming,symlinks=True)
    for attr in ('com.apple.FinderInfo','com.apple.ResourceFork'):
        subprocess.run(['/usr/bin/xattr','-rd',attr,str(incoming)],capture_output=True)
    run('/usr/bin/codesign','--verify','--deep','--strict',str(incoming))
    old=STATE/('Replaced Helium '+stamp+'.app')
    APP.rename(old)
    try:
        incoming.rename(APP)
        run('/usr/bin/codesign','--verify','--deep','--strict',str(APP))
    except Exception:
        if APP.exists():APP.rename(STATE/('Failed Helium '+stamp+'.app'))
        old.rename(APP)
        raise
    (STATE/'latest-backup.txt').write_text(str(backup))
    print('DRM patch applied. Normal Keychain access and the existing profile are retained.')
    print('App and full profile backup:',backup)

def restore():
    ensure_closed()
    backup=Path((STATE/'latest-backup.txt').read_text())
    saved=backup/'Helium.app'
    run('/usr/bin/codesign','--verify','--deep','--strict',str(saved))
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    APP.rename(STATE/('Removed patch '+stamp+'.app'))
    shutil.copytree(saved,APP,symlinks=True)
    print('Original app restored. Your current profile was not rolled back; its backup remains available.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','apply','restore'])
    action=parser.parse_args().action
    try:globals()[action]()
    except Exception as e:raise SystemExit(str(e))
