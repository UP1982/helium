# Helium DRM research

This private repository preserves the upstream Helium source history and adds a local macOS DRM case study and standalone reproduction tools. It is an independent private copy, not an official Helium distribution or a GitHub fork-network member.

- [Editable case study, revision 3](Helium-Crunchyroll-Case-Study.md)
- [PDF case study](Helium-Crunchyroll-Case-Study.pdf)
- [macOS reproduction tools](../../devutils/macos/drm/README.md)

## Findings

Installing a valid Google-signed Widevine module registered the component but did not resolve MediaKeys initialization. A combined module-placement and local-signing change restored the tested Crunchyroll episode. Library validation is a likely blocker; the experiment did not isolate it from module placement. A separate licensing-host connection failure was corrected by secure DNS configuration.

The initially suspected X regression was subsequently attributed by the user to their internet connection. A denied Keychain prompt was a permission choice, not a browser defect, and is not presented as a finding.

## Scope and current status

The observed result was on Apple silicon macOS with Helium 0.18.2.1. Source imported from upstream today is not necessarily the source revision used for that binary. This repository adds research and tools only; it does not integrate DRM into Chromium or change upstream packaging. Platform integration belongs in the macOS packaging repository and would require a separate validated implementation.

The everyday browser was restored to official Helium with normal library validation and default DNS after the experiment. Publishing this research does not reinstall the workaround. No app binaries, Widevine libraries, profiles, cookies, credentials, or local backup archives are committed.

Upstream's contributor policy is retained. These user-authorized additions are for the owner's private research. No issue, pull request, or other contribution was submitted to imputnet.
