# Experimental Helium DRM reproduction tools (macOS arm64)

**AI-created, unofficial workaround.** Codex wrote these research materials and reproduction tools during a user-authorized troubleshooting session. The goal is to use one Helium installation for everyday browsing and DRM playback instead of maintaining a separate Helium test copy. This is not official Helium support, a vendor-signed build, or a complete source-level DRM integration. The workaround locally re-signs the app and relaxes macOS library validation; credential/passkey compatibility and future-update behavior remain unverified. A verified Google-signed Widevine module must be sourced locally and is not distributed here.

These opt-in tools reproduce a local October 2, 2026 experiment. They are not part of Helium's build, installer, updater, or launch path. See the [case study](../../../docs/drm-research/Helium-Crunchyroll-Case-Study.md) for results, limitations, and security tradeoffs.

Requires an official `/Applications/Helium.app`, Apple silicon macOS, Python 3 on PATH, signing tools supplied by macOS, sufficient backup space, and a locally installed official Google Chrome with bundled arm64 Widevine.

Run from this directory:

```sh
python3 copy_widevine_from_chrome.py
python3 repair_helium.py prepare
# Save your work and quit Helium before applying.
python3 repair_helium.py apply
```

Open Helium normally and verify actual advancing protected playback. The command wrappers prompt before invoking apply or restore and use Python from PATH. Preparation verifies expected vendor signatures and stages a replacement; application backs up the app and closed profile before replacement. Restore keeps current browsing data:

```sh
# Quit Helium before restoring.
python3 repair_helium.py restore
```

The patch replaces vendor app signatures with local ad-hoc signatures and relaxes library validation for the outer app and helpers. Hardened runtime is retained. Vendor-bound credential entitlements are removed, and passkey/credential compatibility is unverified. The tools do not change DNS, use mock Keychain access, disable updates, install a daemon, or redistribute Widevine. The module must be sourced locally; backups and caches remain local.

The original experiment used Helium 0.18.2.1 and Widevine 4.10.3112.0. The repository's upstream source revision may be newer; these tools have not been validated against arbitrary later releases. They stop if the source is modified, its vendor identity differs, or official Helium already bundles Widevine. A new release requires reassessment and playback verification.

Known implementation limit: the preparation/application comparison hashes the main executable rather than the complete app tree. Apply promptly after preparation and stop if the app changes. Restore may select an older application backup; prefer a current official release when needed. No browser/profile restoration is run automatically by cloning this repository.

## Recovery safety and isolated tests

Apply records a complete, verified app and closed-profile backup using an atomic,
flushed pointer update **before** replacing the app. Restore copies the selected
backup to a unique staging directory beside the installed app and verifies it
before replacement. Both operations use macOS `RENAME_SWAP`: a failed copy,
unsupported filesystem, or interrupted copy leaves the installed app in place.
After replacement, the previous app stays in the printed `.helium-apply-*` or
`.helium-restore-*` directory; a verification error triggers an atomic rollback.
The full backup and current profile are retained.

If interrupted, inspect the printed staging location before deleting anything.
Before the exchange it can contain an incomplete copy; after the exchange it
contains the previous installation. These directories are intentionally retained,
and retries use new directories. `latest-backup.txt` continues to select the
verified backup even if apply stops after recording it. Do not launch retained
copies while repairing: all Helium executables and helpers, at any location,
block apply/restore, and process inspection failure also stops the operation.

Keep all browser copies closed throughout the procedure. Process checks occur
before backup and again before replacement, but cannot prevent another process
from launching between checks. This is not a lock against simultaneous repair
commands, and it does not guarantee recovery from hardware failure or power loss.
Atomic exchange requires a supporting macOS filesystem and permission to create
a staging directory beside the app; there is no unsafe rename fallback.

Run the isolated regression suite on macOS:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s . -p 'test_*.py'
```

Tests use temporary app/profile fixtures and mocked signing/process commands.
They exercise the real macOS directory exchange, partial copies, exceptions,
keyboard interrupts, abrupt process termination, process guards, pointer-write
failures, rollback, and retry. They do not launch Helium or change security settings.

## Complete Widevine caches

Both preparation and the Chrome-copy helper use `widevine_cache.py`. Keep this
file beside the other Python scripts. A module needs a parseable Widevine
manifest, a matching numeric version and CDM compatibility fields, and a nonempty
arm64 library with the expected Google signature. Module trees must contain
ordinary files/directories, without symbolic links or special files.

New caches are copied into a unique hidden sibling directory. The tools verify
that every source file and directory was copied, that file SHA-256 hashes match,
and that the source did not change during copying. They write a completeness
record and revalidate the staged tree before renaming it into the final version
path. A publication lock serializes the two helpers and is released automatically
if a process terminates. A failed copy never becomes a selectable cache.

Every reuse checks the manifest, signature, and complete recorded inventory.
Legacy caches without a completeness record, malformed manifests, missing files,
or changed files are rebuilt from a valid local source. An invalid old cache is
preserved under `.rejected-*` only after its replacement passes validation. A
complete cache with different contents for the same version is preserved and
reported as a conflict for investigation.

Preparation can rebuild from the installed profile component. If only an invalid
or legacy cache remains, it stops before staging an app and asks you to rerun
`copy_widevine_from_chrome.py` against official Chrome. It never invents a
completeness record from an old cache's remaining files. A complete recorded cache
can still be used when its original source is unavailable. Preparation also checks
the copied module inside the staged app before signing that app.

Abrupt termination may leave `.staging-*` or `.rejected-*` directories, which are
ignored during version selection. Retry uses a new staging directory. The record
proves consistency with the selected local source, not playback compatibility or
protection against an actor who can rewrite both the cache and its record.
Power-loss durability and CDM compatibility with future releases remain unverified.
