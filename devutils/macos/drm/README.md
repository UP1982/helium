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
