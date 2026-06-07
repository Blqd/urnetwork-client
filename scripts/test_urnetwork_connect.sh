#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmpdir="$(mktemp -d)"
cleanup() {
    rm -rf "$tmpdir"
}
trap cleanup EXIT

assert_missing_credentials() {
    local name="$1"
    shift

    local output status
    output="$(
        XDG_CONFIG_HOME="$tmpdir/config-$name" bash "$SCRIPT_DIR/urnetwork-connect.sh" "$@" 2>&1
    )" && status=0 || status=$?

    if [[ "$status" -eq 0 ]]; then
        echo "expected $name to fail without credentials"
        exit 1
    fi

    if [[ "$output" == *"local:"* || "$output" == *"exec:"* ]]; then
        echo "$name failed before reaching credential loading"
        echo "$output"
        exit 1
    fi

    if [[ "$output" != *"Credentials not found"* ]]; then
        echo "expected missing credentials error for $name, got:"
        echo "$output"
        exit 1
    fi
}

assert_missing_credentials "toggle" toggle
assert_missing_credentials "toggle-timeout" --timeout 1 toggle
