#!/usr/bin/env bash
#
# hetzner.sh: the same as "server.sh hetzner", kept so older links work.
#
#   ./hetzner.sh            rent a server on Hetzner Cloud and prepare it
#   ./hetzner.sh ip         print its address
#   ./hetzner.sh destroy    delete it and its volume
#
# See server.sh for the settings, and for other providers.

set -euo pipefail

src="${BASH_SOURCE[0]:-}"
if [ -n "$src" ] && [ -f "$(dirname "$src")/server.sh" ]; then
    exec bash "$(dirname "$src")/server.sh" hetzner "$@"
fi
curl -fsSL https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/server.sh | bash -s -- hetzner "$@"
