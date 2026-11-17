#!/usr/bin/env bash
set -euo pipefail
image=${1:?Usage: ci/test-image.sh IMAGE}
root=$(cd "$(dirname "$0")/.." && pwd)
docker image inspect "$image" | python3 -c '
import json, sys
config = json.load(sys.stdin)[0]["Config"]
assert config["User"] == "568:568", config["User"]
assert config["Entrypoint"] == ["inosculate"], config["Entrypoint"]
assert config["Cmd"] == ["web"], config["Cmd"]
'
docker run --rm --network none --entrypoint python \
  --mount "type=bind,src=$root/tests,dst=/tests,readonly" \
  --mount "type=bind,src=$root/ci,dst=/app/ci,readonly" \
  "$image" -m ci.run_tests
docker run --rm --network none --entrypoint python \
  --mount "type=bind,src=$root/ci,dst=/app/ci,readonly" \
  "$image" -m ci.smoke
