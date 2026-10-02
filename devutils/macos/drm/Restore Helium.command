#!/bin/zsh
set -e
cd "${0:A:h}"
print 'Quit Helium first. This restores the app saved before the latest repair.'
print 'Current browsing data is kept. The full profile backup remains available.'
read 'reply?Type RESTORE to continue: '
[[ "$reply" == RESTORE ]] || exit 0
/usr/bin/env python3 repair_helium.py restore
read 'reply?Press Return to close.'
