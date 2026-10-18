#!/bin/sh
# Compatibility entry point: always upgrade to the current complete release.
# Installing web.py alone would omit required account and directory modules.
set -eu
TMP="$(mktemp /tmp/rustdesk-panel-current.XXXXXX)" || exit 1
trap 'rm -f "$TMP"' 0
wget -q -O "$TMP" https://raw.githubusercontent.com/kostenkodm/rustdesk-panel/main/scripts/upgrade-accounts.sh
sh "$TMP"
