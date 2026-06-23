#!/bin/sh
set -eu

if [ -n "${MAKEMKV_KEY:-}" ]; then
  mkdir -p /config/.MakeMKV
  printf 'app_Key = "%s"\n' "$MAKEMKV_KEY" > /config/.MakeMKV/settings.conf
fi

python -m app init
exec python -m app "$@"

