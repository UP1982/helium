#!/usr/bin/env python3
"""Copy a locally installed, Google-signed arm64 CDM into the repair cache."""
import hashlib
import json
import platform
import shutil
import subprocess
from pathlib import Path

def verify(path):
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(path)],check=True)
    result=subprocess.run(['/usr/bin/codesign','-dv','--verbose=4',str(path)],capture_output=True,text=True)
    if 'TeamIdentifier=EQHXZ8M8AV' not in result.stderr:
        raise SystemExit('Expected the Google signing identity; stopping.')

if platform.machine() != 'arm64':
    raise SystemExit('This reproduction helper is for Apple silicon Macs only.')
chrome=Path('/Applications/Google Chrome.app')
verify(chrome)
root=chrome/'Contents/Frameworks/Google Chrome Framework.framework/Versions'
candidates=[]
for version in root.iterdir():
    source=version/'Libraries/WidevineCdm'
    if source.is_dir() and (source/'manifest.json').is_file():
        name=json.loads((source/'manifest.json').read_text())['version']
        candidates.append((tuple(map(int,name.split('.'))),source,name))
if not candidates:
    raise SystemExit('This Chrome installation does not bundle Widevine in the expected location.')
_,source,version=max(candidates,key=lambda entry:entry[0])
library=source/'_platform_specific/mac_arm64/libwidevinecdm.dylib'
verify(library)
cache=Path.home()/'Library/Application Support/Helium DRM Repair/Verified Widevine'/version
if cache.exists():
    if hashlib.sha256(library.read_bytes()).digest()!=hashlib.sha256((cache/'_platform_specific/mac_arm64/libwidevinecdm.dylib').read_bytes()).digest():
        raise SystemExit('A different cache already exists for this version; investigate before replacing it.')
else:
    cache.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,cache,symlinks=True)
verify(cache/'_platform_specific/mac_arm64/libwidevinecdm.dylib')
print('Copied Google-signed Widevine',version,'to the local repair cache.')
print('SHA256:',hashlib.sha256(library.read_bytes()).hexdigest())
