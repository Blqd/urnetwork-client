#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$(cd "$SCRIPT_DIR/../build" && pwd)"
# URNETWORK_CONFIG_DIR keeps the config path stable across the sudo re-exec,
# where HOME/XDG_CONFIG_HOME may point at the root account instead.
if [[ ${EUID} -eq 0 && -z "${URNETWORK_CONFIG_DIR:-}" && -n "${SUDO_USER:-}" ]]; then
    _sudo_home="$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)"
    URNETWORK_CONFIG_DIR="${_sudo_home:-$HOME}/.config/urnetwork"
    unset _sudo_home
fi
CONFIG_DIR="${URNETWORK_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/urnetwork}"
MODE_FILE="$CONFIG_DIR/mode"
AUTH_FILE="$CONFIG_DIR/auth"
PID_FILE_SOCKS="$CONFIG_DIR/socks.pid"
PID_FILE_VPN="$CONFIG_DIR/vpn.pid"
STATE_FILE="$CONFIG_DIR/state.json"
ROUTE_BACKUP="$CONFIG_DIR/default-route.before"
LOCK_FILE="$CONFIG_DIR/connect.lock"
STOPPING=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

mkdir -p "$CONFIG_DIR"

# Credential files left behind by a cancelled sudo prompt are short-lived.
find "$CONFIG_DIR" -maxdepth 1 -name '.creds.*' -mmin +1 -delete 2>/dev/null || true

# /proc-based liveness checks. `kill -0` reports EPERM for a root-owned engine
# started from a user shell, which used to make `status` claim the VPN was off.
pid_alive() {
    local pid="${1:-}"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    [[ -d "/proc/$pid" ]]
}

# Alive and either our binary or an executable we are not allowed to inspect
# (a root engine seen from a user shell).  Used for read-only status checks.
pid_is_expected() {
    local pid="${1:-}" want="${2:-}" exe
    pid_alive "$pid" || return 1
    exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
    [[ -z "$exe" || "$exe" == *"$want"* ]]
}

# Kill only engine processes whose executable we can verify, never an arbitrary
# pid that merely appears in a pid file.
kill_engines_by_exe() {
    local want="$1" pid exe
    for pid in $(pgrep -f "$want" 2>/dev/null || true); do
        exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
        case "$exe" in
            */"$want") ;;
            *) continue ;;
        esac
        kill "$pid" 2>/dev/null || true
    done
}

# The launcher hands credentials to the root copy through a 0600 file instead of
# sudo env_keep: a user-controlled environment let any process running as the
# user redirect the root engine's state file (write/delete as root).
consume_creds_file() {
    local path="${1:-}" line key value owner
    [[ -f "$path" ]] || return 0
    owner="$(stat -c %u "$path" 2>/dev/null || echo 0)"
    if [[ $EUID -eq 0 && -n "${SUDO_UID:-}" && "$owner" != "$SUDO_UID" ]]; then
        echo -e "${RED}⚠ ignoring credentials file with unexpected owner${NC}" >&2
        rm -f "$path" 2>/dev/null || true
        return 0
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in ''|'#'*) continue ;; esac
        key="${line%%=*}"; value="${line#*=}"
        case "$key" in
            USER_AUTH) USER_AUTH="$value" ;;
            PASSWORD) PASSWORD="$value" ;;
        esac
    done < "$path"
    rm -f "$path" 2>/dev/null || true
    export USER_AUTH PASSWORD
}

TIMEOUT=""
RETRY=3
CREDS_FILE_ARG=""
PARSED_ARGS=()
SKIP_NEXT=false
for ((i=1; i<=$#; i++)); do
    if $SKIP_NEXT; then SKIP_NEXT=false; continue; fi
    arg="${!i}"
    if [[ "$arg" == "--timeout" ]]; then
        next=$((i+1))
        if [[ $next -le $# ]]; then
            TIMEOUT="${!next}"
            SKIP_NEXT=true
        fi
    elif [[ "$arg" == "--retry" ]]; then
        next=$((i+1))
        if [[ $next -le $# ]]; then
            RETRY="${!next}"
            SKIP_NEXT=true
        fi
    elif [[ "$arg" == "--creds-file" ]]; then
        next=$((i+1))
        if [[ $next -le $# ]]; then
            CREDS_FILE_ARG="${!next}"
            SKIP_NEXT=true
        fi
    else
        PARSED_ARGS+=("$arg")
    fi
done
set -- "${PARSED_ARGS[@]}"

# Only root consumes the launcher's credentials file; the user-level copy just
# forwards the path to the installed (root-owned) script.
[[ -n "$CREDS_FILE_ARG" ]] && consume_creds_file "$CREDS_FILE_ARG"

TIMEOUT_CMD=""
[[ -n "$TIMEOUT" ]] && TIMEOUT_CMD="timeout $TIMEOUT"

wait_for_port() {
    local port="$1" max_wait="${2:-10}"
    for ((i=1; i<=max_wait; i++)); do
        if ss -tlnp | grep -q ":${port} "; then
            return 0
        fi
        sleep 1
    done
    return 1
}

save_default_route() {
    # Journal the real default route: if the engine dies while it is the
    # default, the host would otherwise lose connectivity until DHCP renews.
    local spec
    spec="$(ip route show default 2>/dev/null | head -n1 || true)"
    if [[ -n "$spec" && "$spec" == *"dev urnetwork"* ]]; then
        echo -e "${YELLOW}⚠ default route already points at urnetwork; not saving it${NC}" >&2
        return 0
    fi
    if [[ -n "$spec" ]]; then
        printf '%s\n' "$spec" > "$ROUTE_BACKUP"
    fi
}

restore_default_route() {
    [[ -s "$ROUTE_BACKUP" ]] || return 0
    local spec dev
    spec="$(cat "$ROUTE_BACKUP")"
    [[ -n "$spec" ]] || return 0
    # The journal lives in a user-writable directory, so never feed root an
    # arbitrary route specification: accept only the shape produced by
    # `ip route show default` and require the referenced device to exist.
    if [[ ! "$spec" =~ ^default\ via\ [0-9a-fA-F.:]+\ dev\ [A-Za-z0-9_.-]+(\ .*)?$ ]]; then
        echo -e "${YELLOW}⚠ ignoring malformed route journal${NC}" >&2
        rm -f "$ROUTE_BACKUP"
        return 0
    fi
    dev="$(printf '%s\n' "$spec" | sed -E 's/.* dev ([A-Za-z0-9_.-]+).*/\1/')"
    if ! ip link show "$dev" >/dev/null 2>&1; then
        echo -e "${YELLOW}⚠ route journal references missing device $dev; dropping it${NC}" >&2
        rm -f "$ROUTE_BACKUP"
        return 0
    fi
    # shellcheck disable=SC2086
    ip route replace $spec 2>/dev/null || true
    rm -f "$ROUTE_BACKUP"
}

remove_ipv6_blackhole() {
    ip -6 route del blackhole default metric 10 2>/dev/null || true
}

cleanup_stale_vpn() {
    ip link show urnetwork >/dev/null 2>&1 || return 0
    if is_running; then
        return 0
    fi
    echo -e "${YELLOW}⚠ removing stale urnetwork device (no engine is running)${NC}"
    ip route del default dev urnetwork 2>/dev/null || true
    ip link delete urnetwork 2>/dev/null || true
    remove_ipv6_blackhole
    restore_default_route
}

acquire_lock() {
    mkdir -p "$CONFIG_DIR"
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo -e "${RED}Another urnetwork-connect instance is already running${NC}" >&2
        exit 1
    fi
}

run_with_retry() {
    local mode="$1"; shift
    local bin="$1"; shift
    local port="${1:-}"; shift
    local try=1 max_try="$RETRY" rc child_cmd=()

    while [[ $try -le $max_try ]]; do
        [[ $try -gt 1 ]] && echo -e "${YELLOW}↻ Retry $try/$max_try...${NC}"
        child_cmd=()
        [[ -n "$TIMEOUT" ]] && child_cmd=(timeout "$TIMEOUT")
        # credentials travel via env only: never put them in argv where `ps` shows them
        child_cmd+=("$bin" "$@")
        env USER_AUTH="$USER_AUTH" PASSWORD="$PASSWORD" "${child_cmd[@]}" &
        local pid=$!
        BG_PID=$pid
        save_pid "$mode" $pid

        if [[ -n "$port" && "$try" -le 1 ]]; then
            if ! wait_for_port "$port" 12; then
                echo -e "${YELLOW}⚠ $mode failed to bind port $port (try $try)${NC}"
                kill $pid 2>/dev/null || true
                wait $pid 2>/dev/null || true
                try=$((try + 1))
                continue
            fi
        fi
        echo -e "${GREEN}✓ $mode running${port:+ on :$port} (pid $pid)${NC}"

        # `wait` under `set -e` used to abort before the retry/cleanup, so a
        # crash was never retried and the pid file was left behind.
        set +e
        wait "$pid"
        rc=$?
        set -e
        clean_pid

        if [[ "$STOPPING" -eq 1 || "$rc" -eq 0 || "$rc" -eq 143 || "$rc" -eq 130 ]]; then
            return 0
        fi
        echo -e "${YELLOW}⚠ $mode exited with status $rc (attempt $try/$max_try)${NC}"
        restore_default_route
        remove_ipv6_blackhole
        ip link delete urnetwork 2>/dev/null || true
        try=$((try + 1))
        sleep 1
    done

    echo -e "${RED}✗ $mode failed after $max_try attempts${NC}"
    clean_pid
    return 1
}

cleanup_bg() {
    STOPPING=1
    if [[ -n "${BG_PID:-}" ]]; then
        kill "$BG_PID" 2>/dev/null || true
        wait "$BG_PID" 2>/dev/null || true
    fi
    clean_pid
    remove_ipv6_blackhole
    restore_default_route
}
trap cleanup_bg SIGINT SIGTERM EXIT

usage() {
    cat <<EOF
URnetwork VPN Client for Ubuntu

Usage:
    $(basename "$0") on                     # включить VPN (последний режим)
    $(basename "$0") off                    # выключить
    $(basename "$0") toggle                 # переключить вкл/выкл
    $(basename "$0") socks  [опции]          # SOCKS5 прокси
    $(basename "$0") vpn    [опции]          # полный TUN VPN
    $(basename "$0") register <email> <pass> # регистрация
    $(basename "$0") verify  <code>          # верификация email
    $(basename "$0") list-locations          # список доступных стран
    $(basename "$0") status                  # статус

Опции для socks/vpn:
    --city <city>       Выбрать город
    --country <country> Выбрать страну
    --region <region>   Выбрать регион
    --timeout <сек>     Авто-остановка через N секунд
    --retry <N>         Кол-во попыток при ошибке (по умолч. 3)

Примеры:
    $(basename "$0") register user@example.com mypass
    $(basename "$0") verify 123456
    $(basename "$0") on
    $(basename "$0") off
    $(basename "$0") socks --country Netherlands
    $(basename "$0") vpn --city "New York" --timeout 120
    $(basename "$0") socks --timeout 300 --retry 5
EOF
    exit 0
}

# Root-owned copy of this script.  The Debian package installs it under
# /usr/lib/urnetwork/scripts; scripts/install-nopasswd.sh (repository flow)
# uses /usr/local/libexec.  Prefer whichever is present so the sudoers rule
# never authorises a user-writable copy.
INSTALLED_SCRIPT="${URNETWORK_INSTALLED_SCRIPT:-}"
if [[ -z "$INSTALLED_SCRIPT" ]]; then
    for candidate in \
        /usr/lib/urnetwork/scripts/urnetwork-connect.sh \
        /usr/local/libexec/urnetwork/urnetwork-connect.sh; do
        if [[ -x "$candidate" ]]; then
            INSTALLED_SCRIPT="$candidate"
            break
        fi
    done
fi

find_credentials_helper() {
    if [[ -n "${URNETWORK_CREDENTIALS_HELPER:-}" && -x "${URNETWORK_CREDENTIALS_HELPER}" ]]; then
        printf '%s\n' "$URNETWORK_CREDENTIALS_HELPER"
        return 0
    fi
    for candidate in \
        /usr/lib/urnetwork/scripts/urnetwork-credentials.py \
        /usr/local/libexec/urnetwork/urnetwork-credentials.py \
        "$SCRIPT_DIR/urnetwork-credentials.py"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        local root_args=()
        [[ -n "$TIMEOUT" ]] && root_args+=(--timeout "$TIMEOUT")
        root_args+=(--retry "$RETRY")
        root_args+=("$@")

        # Credentials travel in a 0600 file under the config dir rather than the
        # environment: sudo env_keep for USER_AUTH/PASSWORD was equivalent to a
        # write/delete-as-root primitive for any process running as the user.
        # The root copy verifies the owner and removes the file immediately.
        if [[ -n "${USER_AUTH:-}" ]]; then
            local creds_file="$CONFIG_DIR/.creds.$$"
            ( umask 077; printf 'USER_AUTH=%s\nPASSWORD=%s\n' "$USER_AUTH" "${PASSWORD:-}" > "$creds_file" )
            root_args+=(--creds-file "$creds_file")
        fi

        # When started from the GUI there is no TTY, so sudo cannot read a
        # password from stdin.  The GUI exports SUDO_ASKPASS, whose helper
        # opens its own terminal window; -A tells sudo to use it.
        local -a sudo_cmd=(sudo)
        if [[ -n "${SUDO_ASKPASS:-}" ]] \
           && { [[ ! -t 0 ]] || [[ -n "${URNETWORK_FORCE_ASKPASS:-}" ]]; }; then
            sudo_cmd+=(-A)
        fi

        # Prefer the root-owned copy installed by scripts/install-nopasswd.sh:
        # the sudoers rule then never authorises a user-writable script.
        if [[ -n "$INSTALLED_SCRIPT" && -x "$INSTALLED_SCRIPT" ]]; then
            exec "${sudo_cmd[@]}" "$INSTALLED_SCRIPT" "${root_args[@]}"
        fi
        echo -e "${YELLOW}⚠ Installed copy not found; running the current copy as root.${NC}" >&2
        exec "${sudo_cmd[@]}" "$0" "${root_args[@]}"
    fi
}

load_credentials() {
    USER_AUTH="${USER_AUTH:-}"
    PASSWORD="${PASSWORD:-}"
    NETWORK_NAME="${NETWORK_NAME:-}"
    if [[ -f "$AUTH_FILE" ]]; then
        # Never `source` this file: it is user-writable data, not code.
        local line key value
        while IFS= read -r line || [[ -n "$line" ]]; do
            case "$line" in
                ''|'#'*) continue ;;
            esac
            key="${line%%=*}"
            value="${line#*=}"
            # legacy files wrote quoted values
            value="${value%\'}"
            value="${value#\'}"
            value="${value%\"}"
            value="${value#\"}"
            case "$key" in
                USER_AUTH) USER_AUTH="$value" ;;
                PASSWORD) PASSWORD="$value" ;;
                NETWORK_NAME) NETWORK_NAME="$value" ;;
            esac
        done < "$AUTH_FILE"
    fi
    # The GUI stores the password in the system secret store (GNOME Keyring).
    # Try it before giving up, so terminal use keeps working without a
    # plaintext password in the auth file.
    if [[ -z "$PASSWORD" && -n "$USER_AUTH" ]]; then
        local cred_helper
        cred_helper="$(find_credentials_helper || true)"
        if [[ -n "$cred_helper" ]]; then
            PASSWORD="$(/usr/bin/python3 "$cred_helper" get "$USER_AUTH" 2>/dev/null || true)"
        fi
    fi
    if [[ -z "$USER_AUTH" || -z "$PASSWORD" ]]; then
        echo -e "${RED}Credentials not found. Run '$(basename "$0") register <email> <password>' first.${NC}"
        exit 1
    fi
}

is_running() {
    local pid
    if [[ -f "$PID_FILE_SOCKS" ]]; then
        pid="$(cat "$PID_FILE_SOCKS")"
        pid_is_expected "$pid" "socksproxy" && return 0
    fi
    if [[ -f "$PID_FILE_VPN" ]]; then
        pid="$(cat "$PID_FILE_VPN")"
        pid_is_expected "$pid" "urnetwork-vpn" && return 0
    fi
    return 1
}

save_pid() {
    local mode="$1" pid="$2"
    case "$mode" in
        socks) echo "$pid" > "$PID_FILE_SOCKS" ;;
        vpn)   echo "$pid" > "$PID_FILE_VPN" ;;
    esac
    echo "$mode" > "$MODE_FILE"
}

clean_pid() {
    rm -f "$PID_FILE_SOCKS" "$PID_FILE_VPN"
}

case "${1:-help}" in
    on)
        load_credentials
        if [[ -f "$MODE_FILE" ]]; then
            LAST_MODE="$(cat "$MODE_FILE")"
        else
            LAST_MODE="socks"
        fi
        echo -e "${CYAN}▶  Starting $LAST_MODE...${NC}"
        case "$LAST_MODE" in
            socks)
                acquire_lock
                run_with_retry socks "$BUILD_DIR/socksproxy" 9999 "${@:2}"
                ;;
            vpn)
                require_root "$@"
                acquire_lock
                save_default_route
                cleanup_stale_vpn
                run_with_retry vpn "$BUILD_DIR/urnetwork-vpn" "" "${@:2}"
                ;;
            *)
                echo -e "${RED}Unknown mode in $MODE_FILE: $LAST_MODE${NC}" >&2
                exit 1
                ;;
        esac
        ;;

    off|stop)
        # Do not ask for a password when there is nothing to clean up: a
        # graceful engine exit already removes the device and restores routes.
        if [[ ! -f "$PID_FILE_SOCKS" && ! -f "$PID_FILE_VPN" \
              && ! -e "$ROUTE_BACKUP" \
              && -z "$(ip -6 route show blackhole default 2>/dev/null || true)" ]] \
           && ! ip link show urnetwork >/dev/null 2>&1; then
            echo -e "${GREEN}✓ Nothing to stop${NC}"
            exit 0
        fi
        require_root "$@"
        echo -e "${YELLOW}⏹  Stopping...${NC}"
        if [[ -f "$PID_FILE_SOCKS" ]]; then
            socks_pid="$(cat "$PID_FILE_SOCKS")"
            if pid_is_expected "$socks_pid" "socksproxy"; then
                kill "$socks_pid" 2>/dev/null || true
            else
                echo -e "${YELLOW}⚠ pid $socks_pid from socks.pid is not socksproxy; not killing${NC}" >&2
            fi
        fi
        if [[ -f "$PID_FILE_VPN" ]]; then
            vpn_pid="$(cat "$PID_FILE_VPN")"
            if pid_is_expected "$vpn_pid" "urnetwork-vpn"; then
                kill "$vpn_pid" 2>/dev/null || true
                for _ in 1 2 3 4 5 6 7 8 9 10; do
                    pid_alive "$vpn_pid" || break
                    sleep 0.3
                done
                kill -9 "$vpn_pid" 2>/dev/null || true
            else
                echo -e "${YELLOW}⚠ pid $vpn_pid from vpn.pid is not urnetwork-vpn; not killing${NC}" >&2
            fi
        fi
        kill_engines_by_exe socksproxy
        kill_engines_by_exe urnetwork-vpn
        ip link delete urnetwork 2>/dev/null || true
        remove_ipv6_blackhole
        restore_default_route
        clean_pid
        echo -e "${GREEN}✓ Stopped${NC}"
        ;;

    toggle)
        if is_running; then
            exec "$0" off
        else
            on_args=("$0")
            [[ -n "$TIMEOUT" ]] && on_args+=(--timeout "$TIMEOUT")
            on_args+=(--retry "$RETRY" "on")
            exec "${on_args[@]}" "${@:2}"
        fi
        ;;

    socks)
        load_credentials
        acquire_lock
        shift
        run_with_retry socks "$BUILD_DIR/socksproxy" 9999 "$@"
        ;;

    vpn)
        load_credentials
        require_root "$@"
        acquire_lock
        save_default_route
        cleanup_stale_vpn
        shift
        run_with_retry vpn "$BUILD_DIR/urnetwork-vpn" "" "$@"
        ;;

    register)
        if [[ $EUID -eq 0 ]]; then
            echo -e "${RED}register must run as your normal user, not root${NC}" >&2
            exit 1
        fi
        if [[ $# -lt 3 ]]; then
            echo "Usage: $(basename "$0") register <email> <password>"
            exit 1
        fi
        EMAIL="$2"
        PASSWORD_VAL="$3"

        echo -e "${CYAN}Registering with URnetwork...${NC}"
        PBIN=""
        if command -v urnetwork-provider &>/dev/null; then
            PBIN="$(which urnetwork-provider)"
        elif [[ -f "$BUILD_DIR/urnetwork-provider" ]]; then
            PBIN="$BUILD_DIR/urnetwork-provider"
        else
            echo -e "${YELLOW}Downloading provider binary...${NC}"
            # No `local` here: this branch runs at script scope, where `local`
            # aborts the whole script under `set -e`.  Keep the global cleanup
            # trap too instead of replacing it.
            tmpdir="$(mktemp -d)"
            trap 'rm -rf "$tmpdir"; cleanup_bg' EXIT
            curl -fsSL "https://github.com/urnetwork/connect/releases/download/v2026.3.23-895075980/urnetwork-provider-2026.3.23-895075980.tar.gz" \
                -o "$tmpdir/provider.tar.gz"
            # `tar A || tar B && cp` used to run `cp` even after a successful
            # extraction and abort the script under `set -e`.
            if tar -xzf "$tmpdir/provider.tar.gz" -C "$BUILD_DIR" 2>/dev/null; then
                :
            elif tar -xzf "$tmpdir/provider.tar.gz" -C "$tmpdir"; then
                install -m 755 "$tmpdir/urnetwork-provider" "$BUILD_DIR/urnetwork-provider"
            else
                echo -e "${RED}Could not unpack provider binary${NC}" >&2
                exit 1
            fi
            chmod +x "$BUILD_DIR/urnetwork-provider" 2>/dev/null || true
            PBIN="$BUILD_DIR/urnetwork-provider"
        fi

        "$PBIN" auth --user_auth="$EMAIL" --password="$PASSWORD_VAL" 2>&1 || \
            echo -e "${YELLOW}Account may already exist or need API registration.${NC}"

        # Prefer the desktop secret store; the auth file then keeps only the
        # account name. (No `local` here: this branch runs at script scope.)
        cred_helper="$(find_credentials_helper || true)"
        stored=0
        if [[ -n "$cred_helper" ]] \
           && printf '%s\n' "$PASSWORD_VAL" | /usr/bin/python3 "$cred_helper" set "$EMAIL" >/dev/null 2>&1; then
            stored=1
        fi
        umask 077
        if [[ "$stored" -eq 1 ]]; then
            printf 'USER_AUTH=%s\n# password is stored in the system secret store\n' "$EMAIL" > "$AUTH_FILE.tmp"
        else
            printf 'USER_AUTH=%s\nPASSWORD=%s\n' "$EMAIL" "$PASSWORD_VAL" > "$AUTH_FILE.tmp"
        fi
        chmod 600 "$AUTH_FILE.tmp"
        mv -f "$AUTH_FILE.tmp" "$AUTH_FILE"
        if [[ "$stored" -eq 1 ]]; then
            echo -e "${GREEN}✓ Credentials saved (password in the system secret store)${NC}"
        else
            echo -e "${GREEN}✓ Credentials saved${NC}"
        fi
        echo -e "${YELLOW}Check email for verification code, then:${NC}"
        echo "  $(basename "$0") verify <code>"
        ;;

    verify)
        if [[ $# -lt 2 ]]; then
            echo "Usage: $(basename "$0") verify <code>"
            exit 1
        fi
        CODE="$2"
        PBIN=""
        command -v urnetwork-provider &>/dev/null && PBIN="$(which urnetwork-provider)" || PBIN="$BUILD_DIR/urnetwork-provider"
        if [[ -f "$PBIN" ]]; then
            "$PBIN" auth "$CODE"
            echo -e "${GREEN}✓ Account verified!${NC}"
            echo "  $(basename "$0") on"
        else
            echo -e "${RED}Provider binary not found. Run register first.${NC}"
        fi
        ;;

    list-locations)
        load_credentials
        echo -e "${CYAN}Available locations:${NC}"
        # Let the engine's exit code reach the caller instead of hiding it.
        USER_AUTH="$USER_AUTH" PASSWORD="$PASSWORD" \
            "$BUILD_DIR/socksproxy" list-locations 2>&1
        ;;

    status)
        echo -e "${CYAN}Status:${NC}"
        if [[ -f "$AUTH_FILE" ]]; then
            account="$(grep -m1 '^USER_AUTH=' "$AUTH_FILE" 2>/dev/null | cut -d= -f2- || true)"
            echo -e "  ${GREEN}✓${NC} Account: ${account:-?}"
        else
            echo -e "  ${YELLOW}✗${NC} No credentials"
        fi
        if [[ -f "$PID_FILE_SOCKS" ]] && pid_is_expected "$(cat "$PID_FILE_SOCKS")" "socksproxy"; then
            echo -e "  ${GREEN}✓${NC} SOCKS5 proxy :9999 (pid $(cat "$PID_FILE_SOCKS"))"
        else
            echo -e "  ${YELLOW}✗${NC} SOCKS5 proxy"
        fi
        if [[ -f "$PID_FILE_VPN" ]] && pid_is_expected "$(cat "$PID_FILE_VPN")" "urnetwork-vpn"; then
            echo -e "  ${GREEN}✓${NC} TUN VPN (pid $(cat "$PID_FILE_VPN"))"
        else
            echo -e "  ${YELLOW}✗${NC} TUN VPN"
        fi
        if [[ -f "$MODE_FILE" ]]; then
            echo -e "  Mode: $(cat "$MODE_FILE")"
        fi
        # A crashed engine can leave the default route pointing at a missing
        # device, which silently takes the whole machine offline.
        if ip route show default 2>/dev/null | grep -q 'dev urnetwork'; then
            if ip link show urnetwork >/dev/null 2>&1; then
                echo -e "  ${GREEN}✓${NC} default route via urnetwork"
            else
                echo -e "  ${RED}✗${NC} default route points at missing urnetwork device (offline risk)"
                echo -e "     run: sudo $(basename "$0") off"
            fi
        fi
        if [[ -f "$STATE_FILE" ]]; then
            echo -e "  State: $(tr -d '\n' < "$STATE_FILE")"
        fi
        for bin in socksproxy urnetwork-vpn; do
            if [[ ! -x "$BUILD_DIR/$bin" ]]; then
                echo -e "  ${YELLOW}✗${NC} $bin is not built (run: make build)"
            fi
        done
        ;;

    help|--help|-h)
        usage
        ;;

    *)
        usage
        ;;
esac
