#!/usr/bin/env bash
set -Eeuo pipefail

for attempt in {1..100}; do
  if [[ -S /tmp/.X11-unix/X96 && -s "${XAUTHORITY}" ]]; then
    exec wine "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
  fi
  sleep 0.1
done

printf 'MT5 Auto desktop is not ready.\n' >&2
exit 1
