#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$(cd "$SCRIPT_DIR/../build" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/urnetwork"
MODE_FILE="$CONFIG_DIR/mode"
AUTH_FILE="$CONFIG_DIR/auth"
PID_FILE_SOCKS="$CONFIG_DIR/socks.pid"
PID_FILE_VPN="$CONFIG_DIR/vpn.pid"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

mkdir -p "$CONFIG_DIR"

TIMEOUT=""
RETRY=3
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
    else
        PARSED_ARGS+=("$arg")
    fi
done
set -- "${PARSED_ARGS[@]}"

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

run_with_retry() {
    local mode="$1"; shift
    local bin="$1"; shift
    local port="${1:-}"; shift
    local try=1 max_try="$RETRY"

    while [[ $try -le $max_try ]]; do
        [[ $try -gt 1 ]] && echo -e "${YELLOW}↻ Retry $try/$max_try...${NC}"
        ${TIMEOUT_CMD:-} "$bin" "$@" &
        local pid=$!
        BG_PID=$pid
        save_pid "$mode" $pid

        if [[ -n "$port" ]]; then
            if wait_for_port "$port" 12; then
                echo -e "${GREEN}✓ $mode running on :$port (pid $pid)${NC}"
                wait $pid
                clean_pid
                return 0
            else
                echo -e "${YELLOW}⚠ $mode failed to bind port $port (try $try)${NC}"
                kill $pid 2>/dev/null || true
                wait $pid 2>/dev/null || true
                sleep 1
            fi
        else
            echo -e "${GREEN}✓ $mode started (pid $pid)${NC}"
            wait $pid
            clean_pid
            return 0
        fi

        try=$((try + 1))
    done

    echo -e "${RED}✗ $mode failed after $max_try attempts${NC}"
    clean_pid
    return 1
}

cleanup_bg() {
    if [[ -n "${BG_PID:-}" ]]; then
        kill "$BG_PID" 2>/dev/null || true
        wait "$BG_PID" 2>/dev/null || true
    fi
    clean_pid
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

require_root() {
    if [[ $EUID -ne 0 ]]; then
        local root_args=("$0")
        [[ -n "$TIMEOUT" ]] && root_args+=(--timeout "$TIMEOUT")
        root_args+=(--retry "$RETRY")
        root_args+=("$@")
        exec sudo USER_AUTH="$USER_AUTH" PASSWORD="$PASSWORD" "${root_args[@]}"
    fi
}

load_credentials() {
    if [[ -f "$AUTH_FILE" ]]; then
        source "$AUTH_FILE"
    fi
    if [[ -z "${USER_AUTH:-}" || -z "${PASSWORD:-}" ]]; then
        echo -e "${RED}Credentials not found. Run '$(basename "$0") register <email> <password>' first.${NC}"
        exit 1
    fi
}

is_running() {
    [[ -f "$PID_FILE_SOCKS" ]] && kill -0 "$(cat "$PID_FILE_SOCKS")" 2>/dev/null && return 0
    [[ -f "$PID_FILE_VPN" ]] && kill -0 "$(cat "$PID_FILE_VPN")" 2>/dev/null && return 0
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
                run_with_retry socks "$BUILD_DIR/socksproxy" 9999 \
                    --user-auth "$USER_AUTH" \
                    --password "$PASSWORD" \
                    "${@:2}"
                ;;
            vpn)
                require_root "$@"
                ip link delete urnetwork 2>/dev/null || true
                run_with_retry vpn "$BUILD_DIR/urnetwork-vpn" "" \
                    --user-auth "$USER_AUTH" \
                    --password "$PASSWORD" \
                    "${@:2}"
                ;;
        esac
        ;;

    off|stop)
        require_root "$@"
        echo -e "${YELLOW}⏹  Stopping...${NC}"
        if [[ -f "$PID_FILE_SOCKS" ]]; then
            kill "$(cat "$PID_FILE_SOCKS")" 2>/dev/null || true
        fi
        if [[ -f "$PID_FILE_VPN" ]]; then
            kill "$(cat "$PID_FILE_VPN")" 2>/dev/null || true
        fi
        pkill -f "socksproxy" 2>/dev/null || true
        pkill -f "urnetwork-vpn" 2>/dev/null || true
        ip link delete urnetwork 2>/dev/null || true
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
        shift
        run_with_retry socks "$BUILD_DIR/socksproxy" 9999 \
            --user-auth "$USER_AUTH" \
            --password "$PASSWORD" \
            "$@"
        ;;

    vpn)
        load_credentials
        require_root "$@"
        ip link delete urnetwork 2>/dev/null || true
        shift
        run_with_retry vpn "$BUILD_DIR/urnetwork-vpn" "" \
            --user-auth "$USER_AUTH" \
            --password "$PASSWORD" \
            "$@"
        ;;

    register)
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
            curl -sL "https://github.com/urnetwork/connect/releases/download/v2026.3.23-895075980/urnetwork-provider-2026.3.23-895075980.tar.gz" \
                -o /tmp/urnetwork-provider.tar.gz
            tar -xzf /tmp/urnetwork-provider.tar.gz -C "$BUILD_DIR" 2>/dev/null || \
                tar -xzf /tmp/urnetwork-provider.tar.gz -C /tmp && \
                cp /tmp/urnetwork-provider "$BUILD_DIR/"
            chmod +x "$BUILD_DIR/urnetwork-provider" 2>/dev/null || true
            PBIN="$BUILD_DIR/urnetwork-provider"
        fi

        "$PBIN" auth --user_auth="$EMAIL" --password="$PASSWORD_VAL" 2>&1 || \
            echo -e "${YELLOW}Account may already exist or need API registration.${NC}"

        cat > "$AUTH_FILE" <<-EOF
USER_AUTH='$EMAIL'
PASSWORD='$PASSWORD_VAL'
EOF
        chmod 600 "$AUTH_FILE"
        echo -e "${GREEN}✓ Credentials saved${NC}"
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
        "$BUILD_DIR/socksproxy" \
            --user-auth "$USER_AUTH" \
            --password "$PASSWORD" \
            list-locations \
            2>&1 || true
        ;;

    status)
        echo -e "${CYAN}Status:${NC}"
        if [[ -f "$AUTH_FILE" ]]; then
            source "$AUTH_FILE"
            echo -e "  ${GREEN}✓${NC} Account: $USER_AUTH"
        else
            echo -e "  ${YELLOW}✗${NC} No credentials"
        fi
        if [[ -f "$PID_FILE_SOCKS" ]] && kill -0 "$(cat "$PID_FILE_SOCKS")" 2>/dev/null; then
            echo -e "  ${GREEN}✓${NC} SOCKS5 proxy :9999 (pid $(cat "$PID_FILE_SOCKS"))"
        else
            echo -e "  ${YELLOW}✗${NC} SOCKS5 proxy"
        fi
        if [[ -f "$PID_FILE_VPN" ]] && kill -0 "$(cat "$PID_FILE_VPN")" 2>/dev/null; then
            echo -e "  ${GREEN}✓${NC} TUN VPN (pid $(cat "$PID_FILE_VPN"))"
        else
            echo -e "  ${YELLOW}✗${NC} TUN VPN"
        fi
        if [[ -f "$MODE_FILE" ]]; then
            echo -e "  Mode: $(cat "$MODE_FILE")"
        fi
        ;;

    help|--help|-h)
        usage
        ;;

    *)
        usage
        ;;
esac
