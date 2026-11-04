#!/usr/bin/env bash
# SUDO_ASKPASS helper: asks for the sudo password in a separate terminal window.
#
# sudo runs this helper without a TTY (the GUI has no terminal), so the password
# cannot be read from stdin.  Instead we open a terminal emulator that prompts
# for the password and writes it to a private file; this helper waits for it and
# prints it on stdout, where sudo expects it.
#
# The password file lives in a 0700 directory (preferably on tmpfs) and is
# deleted as soon as the password has been read.
#
# sudo calls it automatically:
#   SUDO_ASKPASS=/path/to/urnetwork-askpass.sh sudo -A <command>
set -u

PROMPT="${1:-Пароль sudo: }"
TIMEOUT="${URNETWORK_ASKPASS_TIMEOUT:-180}"

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo "urnetwork-askpass: no graphical session (DISPLAY/WAYLAND_DISPLAY unset)" >&2
    exit 1
fi

RUNTIME_DIR="${XDG_RUNTIME_DIR:-}"
if [[ -z "$RUNTIME_DIR" || ! -d "$RUNTIME_DIR" ]]; then
    RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/urnetwork-askpass-runtime.XXXXXX")" || exit 1
    chmod 700 "$RUNTIME_DIR"
    OWN_RUNTIME=1
else
    OWN_RUNTIME=0
fi

WORKDIR="$(mktemp -d "$RUNTIME_DIR/urnetwork-askpass.XXXXXX")" || exit 1
chmod 700 "$WORKDIR"
PASSFILE="$WORKDIR/password"
TERM_PID=""

cleanup() {
    if [[ -n "$TERM_PID" ]] && kill -0 "$TERM_PID" 2>/dev/null; then
        kill "$TERM_PID" 2>/dev/null
    fi
    rm -rf "$WORKDIR"
    if [[ "$OWN_RUNTIME" -eq 1 ]]; then
        rm -rf "$RUNTIME_DIR"
    fi
}
trap cleanup EXIT

# The shell fragment that runs inside the terminal window.  $1 = prompt,
# $2 = password file.  The password is written with printf '%s' so backslashes
# and quotes survive untouched.
INNER='printf "%s" "$1"; IFS= read -r -s pw; printf "\n"; if [ -n "$pw" ]; then printf "%s" "$pw" > "$2"; fi'

launch_terminal() {
    local spec="$1"
    local -a parts
    read -r -a parts <<< "$spec"
    local name="${parts[0]}"
    command -v "$name" >/dev/null 2>&1 || return 1
    "${parts[@]}" bash -c "$INNER" _ "$PROMPT" "$PASSFILE" >/dev/null 2>&1 &
    TERM_PID=$!
    return 0
}

# Order matters: prefer terminals that keep running while the window is open,
# so a closed window can be detected instead of waiting for the timeout.
TERMINALS=(
    "gnome-terminal --wait --"
    "konsole -e"
    "xfce4-terminal -x"
    "mate-terminal -e"
    "tilix -e"
    "kitty"
    "alacritty -e"
    "x-terminal-emulator -e"
    "xterm -e"
)

launched=0
for term in "${TERMINALS[@]}"; do
    if launch_terminal "$term"; then
        launched=1
        break
    fi
done

if [[ "$launched" -ne 1 ]]; then
    echo "urnetwork-askpass: no terminal emulator found (tried: ${TERMINALS[*]})" >&2
    exit 1
fi

password=""
deadline=$(( $(date +%s) + TIMEOUT ))
while [[ $(date +%s) -lt $deadline ]]; do
    if [[ -s "$PASSFILE" ]]; then
        password="$(cat "$PASSFILE")"
        break
    fi
    if ! kill -0 "$TERM_PID" 2>/dev/null; then
        # The window is gone.  Some emulators fork and return immediately, so
        # give the password file a short grace period before giving up.
        tries=0
        while [[ "$tries" -lt 20 ]]; do
            if [[ -s "$PASSFILE" ]]; then
                password="$(cat "$PASSFILE")"
                break 2
            fi
            sleep 0.1
            tries=$((tries + 1))
        done
        break
    fi
    sleep 0.2
done

if [[ -z "$password" ]]; then
    echo "urnetwork-askpass: no password entered" >&2
    exit 1
fi

printf '%s\n' "$password"
