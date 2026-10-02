#!/bin/zsh
set -e
cd "${0:A:h}"
print 'Helium DRM repair: quit Helium before continuing.'
print 'This backs up the app and profile, then reapplies the library-signing exception.'
print 'The exception reduces one macOS protection. Keychain access remains normal.'
read 'reply?Type REPAIR to continue: '
[[ "$reply" == REPAIR ]] || exit 0
/usr/bin/env python3 repair_helium.py prepare
/usr/bin/env python3 repair_helium.py apply
print 'Finished. Open Helium normally and approve its Keychain prompt if shown.'
read 'reply?Press Return to close.'
