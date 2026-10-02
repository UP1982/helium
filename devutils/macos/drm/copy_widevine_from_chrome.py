#!/usr/bin/env python3
"""Copy a locally installed, Google-signed arm64 CDM into the repair cache."""
import hashlib
import platform
from pathlib import Path

from widevine_cache import InvalidModule, LIBRARY, cache_module, validate_module, verify_google


def copy_from_chrome(chrome, cache_root):
    verify_google(chrome)
    root = chrome/'Contents/Frameworks/Google Chrome Framework.framework/Versions'
    candidates = []
    for version in root.iterdir():
        source = version/'Libraries/WidevineCdm'
        if source.is_dir():
            try:
                name, _ = validate_module(source)
            except InvalidModule:
                continue
            candidates.append((tuple(map(int, name.split('.'))), source, name))
    if not candidates:
        raise InvalidModule('This Chrome installation has no complete verified arm64 Widevine module '
                            'in the expected location.')
    _, source, version = max(candidates, key=lambda entry: entry[0])
    cache = cache_module(source, cache_root, version)
    print('Copied Google-signed Widevine', version, 'to the local repair cache.')
    print('SHA256:', hashlib.sha256((cache/LIBRARY).read_bytes()).hexdigest())
    return cache


def main():
    if platform.machine() != 'arm64':
        raise SystemExit('This reproduction helper is for Apple silicon Macs only.')
    copy_from_chrome(Path('/Applications/Google Chrome.app'),
                     Path.home()/'Library/Application Support/Helium DRM Repair/Verified Widevine')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error))
