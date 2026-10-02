"""Validate and atomically publish complete, locally sourced Widevine caches."""
import fcntl
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

LIBRARY = Path('_platform_specific/mac_arm64/libwidevinecdm.dylib')
RECEIPT = '.helium-cache.json'
VERSION = re.compile(r'[0-9]+(?:\.[0-9]+)*')


class InvalidModule(RuntimeError):
    """A module is incomplete, inconsistent, or unverified."""


def verify_google(path):
    """Check the existing Google signature without changing it."""
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(path)],
                   check=True, capture_output=True)
    result = subprocess.run(['/usr/bin/codesign', '-dv', '--verbose=4', str(path)],
                            check=True, capture_output=True, text=True)
    if 'TeamIdentifier=EQHXZ8M8AV' not in result.stderr.splitlines():
        raise InvalidModule('Expected the Google signing identity; stopping.')


def inventory(module):
    """Include every directory and file; reject links and special files."""
    if module.is_symlink() or not module.is_dir():
        raise InvalidModule('Expected a self-contained module directory: ' + str(module))
    entries = {}

    def visit(directory):
        for path in sorted(directory.iterdir()):
            name = path.relative_to(module).as_posix()
            if path.is_symlink():
                raise InvalidModule('Module contains a symbolic link: ' + str(path))
            if name == RECEIPT:
                continue
            if path.is_dir():
                entries[name + '/'] = None
                visit(path)
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                entries[name] = digest.hexdigest()
            else:
                raise InvalidModule('Module contains a special file: ' + str(path))

    visit(module)
    return entries


def validate_module(module, expected_version=None):
    """Validate the supported module layout and return version and full inventory."""
    try:
        entries = inventory(module)
        manifest = json.loads((module/'manifest.json').read_text())
        if not isinstance(manifest, dict) or manifest.get('name') != 'WidevineCdm':
            raise InvalidModule('Expected a WidevineCdm manifest.')
        version = manifest.get('version')
        if not isinstance(version, str) or VERSION.fullmatch(version) is None:
            raise InvalidModule('Invalid Widevine version in manifest.')
        if expected_version is not None and version != expected_version:
            raise InvalidModule('Widevine manifest version does not match its cache directory.')
        for key in ('x-cdm-module-versions', 'x-cdm-interface-versions', 'x-cdm-host-versions'):
            value = manifest.get(key)
            if not isinstance(value, str) or re.fullmatch(r'[0-9]+(?:,[0-9]+)*', value) is None:
                raise InvalidModule('Missing or invalid Widevine manifest field: ' + key)
        library = module/LIBRARY
        if LIBRARY.as_posix() not in entries or library.stat().st_size == 0:
            raise InvalidModule('Missing or empty arm64 Widevine library.')
        verify_google(library)
        return version, entries
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise InvalidModule('Cannot validate Widevine module ' + str(module) + ': ' + str(error)) from error


def validate_cache(cache, expected_version):
    """Legacy/unsealed caches must be reacquired, not certified from partial contents."""
    version, entries = validate_module(cache, expected_version)
    try:
        receipt = json.loads((cache/RECEIPT).read_text())
    except (OSError, ValueError) as error:
        raise InvalidModule('Cache has no valid completeness record; reacquire it from a local source.') from error
    if receipt != {'schema': 1, 'version': version, 'entries': entries}:
        raise InvalidModule('Cache contents differ from the verified complete copy; reacquire it.')
    return entries


def cache_module(source, cache_root, expected_version=None):
    """Stage, verify and publish; never overwrite a different complete cache."""
    version, original = validate_module(source, expected_version)
    cache_root.mkdir(parents=True, exist_ok=True)
    cache = cache_root/version
    # flock is released even after SIGKILL; concurrent helper invocations cannot
    # replace a cache between its validation and publication.
    with (cache_root/'.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if cache.exists() or cache.is_symlink():
            try:
                existing = validate_cache(cache, version)
            except InvalidModule:
                pass
            else:
                if existing != original:
                    raise RuntimeError('A different complete cache already exists for ' + version +
                                       '; investigate before replacing it.')
                return cache
        if source.resolve() == cache.resolve():
            raise InvalidModule('An incomplete cache cannot be its own repair source; reacquire it.')
        with tempfile.TemporaryDirectory(prefix='.staging-', dir=cache_root) as temporary:
            staged = Path(temporary)/'module'
            shutil.copytree(source, staged, symlinks=True)
            _, copied = validate_module(staged, version)
            _, current = validate_module(source, version)
            if copied != original or current != original:
                raise InvalidModule('Widevine changed or was incompletely copied; retry from a stable source.')
            (staged/RECEIPT).write_text(json.dumps({
                'schema': 1, 'version': version, 'entries': copied}, sort_keys=True) + '\n')
            validate_cache(staged, version)
            if cache.exists() or cache.is_symlink():
                # Only an invalid cache reaches here. Preserve it for inspection;
                # an interruption here leaves no selectable partial final cache.
                rejected = Path(tempfile.mkdtemp(prefix='.rejected-' + version + '-', dir=cache_root))
                cache.rename(rejected/'module')
            staged.rename(cache)
    return cache


def select_module(profile_modules, cache_root):
    """Prefer the newest usable version, rebuilding unsealed caches from the profile."""
    versions = set()
    for root in (profile_modules, cache_root):
        if root.exists():
            versions.update(path.name for path in root.iterdir() if VERSION.fullmatch(path.name))
    problems = []
    for version in sorted(versions, key=lambda value: tuple(map(int, value.split('.'))), reverse=True):
        cache = cache_root/version
        if cache.exists():
            try:
                validate_cache(cache, version)
            except InvalidModule as error:
                problems.append(str(error))
            else:
                return cache
        source = profile_modules/version
        if source.exists():
            try:
                validate_module(source, version)
            except InvalidModule as error:
                problems.append(str(error))
            else:
                return cache_module(source, cache_root, version)
    detail = '\n'.join(problems)
    raise InvalidModule('No complete verified arm64 Widevine module is available. '
                        'Run copy_widevine_from_chrome.py to reacquire it from official Chrome.' +
                        ('\n' + detail if detail else ''))
