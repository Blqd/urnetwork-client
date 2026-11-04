#!/usr/bin/env bash
set -euo pipefail

# Launch Codex through the system route/TUN by removing proxy variables that
# otherwise force Codex back to an old local proxy.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
unset http_proxy https_proxy all_proxy no_proxy

exec "${CODEX_BIN:-codex}" "$@"
