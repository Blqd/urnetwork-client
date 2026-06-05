#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$(cd "$SCRIPT_DIR/../build" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/urnetwork"
JWT_FILE="$HOME/.urnetwork/jwt"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

mkdir -p "$CONFIG_DIR"

usage() {
    cat <<EOF
URnetwork VPN Client for Ubuntu

Usage:
    $(basename "$0") socks   [--city <city>] [--country <country>] [--region <region>]
    $(basename "$0") vpn     [--city <city>] [--country <country>] [--region <region>]
    $(basename "$0") register <email> <password>
    $(basename "$0") verify  <code>
    $(basename "$0") list-locations
    $(basename "$0") status
    $(basename "$0") stop

Modes:
    socks           Start SOCKS5 proxy on :9999 (app-level proxy)
    vpn             Start full TUN VPN (routes ALL system traffic)

Examples:
    $(basename "$0") register user@example.com mypassword
    $(basename "$0") verify 123456
    $(basename "$0") socks --country Netherlands
    $(basename "$0") vpn --city Amsterdam
EOF
    exit 0
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${YELLOW}⚠  This command requires root. Re-running with sudo...${NC}"
        exec sudo "$0" "$@"
    fi
}

load_credentials() {
    if [[ -f "$CONFIG_DIR/auth" ]]; then
        source "$CONFIG_DIR/auth"
    fi
    if [[ -z "${USER_AUTH:-}" || -z "${PASSWORD:-}" ]]; then
        echo -e "${RED}Credentials not found. Run '$(basename "$0") register <email> <password>' first.${NC}"
        exit 1
    fi
}

case "${1:-help}" in
    socks)
        load_credentials
        shift
        exec "$BUILD_DIR/socksproxy" \
            --user-auth "$USER_AUTH" \
            --password "$PASSWORD" \
            "$@"
        ;;

    vpn)
        load_credentials
        require_root "$@"
        shift
        exec "$BUILD_DIR/urnetwork-vpn" \
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
        PASSWORD="$3"

        echo -e "${CYAN}Registering with URnetwork...${NC}"
        if command -v urnetwork-provider &>/dev/null; then
            urnetwork-provider auth "$EMAIL" "$PASSWORD"
        else
            echo -e "${YELLOW}Provider binary not found. Installing...${NC}"
            curl -sL "https://github.com/urnetwork/connect/releases/download/v2026.3.23-895075980/urnetwork-provider-2026.3.23-895075980.tar.gz" \
                -o /tmp/urnetwork-provider.tar.gz
            tar -xzf /tmp/urnetwork-provider.tar.gz -C "$BUILD_DIR"
            chmod +x "$BUILD_DIR/urnetwork-provider" 2>/dev/null || true
            "$BUILD_DIR/urnetwork-provider" auth "$EMAIL" "$PASSWORD"
        fi

        cat > "$CONFIG_DIR/auth" <<-EOF
USER_AUTH='$EMAIL'
PASSWORD='$PASSWORD'
EOF
        chmod 600 "$CONFIG_DIR/auth"
        echo -e "${GREEN}Credentials saved to $CONFIG_DIR/auth${NC}"
        echo -e "${YELLOW}Check your email for a verification code, then run:${NC}"
        echo "  $(basename "$0") verify <code>"
        ;;

    verify)
        if [[ $# -lt 2 ]]; then
            echo "Usage: $(basename "$0") verify <code>"
            exit 1
        fi
        CODE="$2"
        if command -v urnetwork-provider &>/dev/null; then
            urnetwork-provider auth verify "$CODE"
        elif [[ -f "$BUILD_DIR/urnetwork-provider" ]]; then
            "$BUILD_DIR/urnetwork-provider" auth verify "$CODE"
        else
            echo -e "${RED}Provider binary not found. Run '$(basename "$0") register' first.${NC}"
            exit 1
        fi
        echo -e "${GREEN}Account verified! You can now use:${NC}"
        echo "  $(basename "$0") socks   --country <country>"
        echo "  $(basename "$0") vpn     --city <city>"
        ;;

    list-locations)
        load_credentials
        echo -e "${CYAN}Fetching available provider locations...${NC}"
        "$BUILD_DIR/socksproxy" \
            --user-auth "$USER_AUTH" \
            --password "$PASSWORD" \
            2>&1 || true
        ;;

    status)
        if [[ -f "$CONFIG_DIR/auth" ]]; then
            source "$CONFIG_DIR/auth"
            echo -e "${GREEN}✓ Credentials configured for: $USER_AUTH${NC}"
        else
            echo -e "${YELLOW}No credentials configured.${NC}"
            echo "  Run: $(basename "$0") register <email> <password>"
        fi
        if ip link show urnetwork &>/dev/null 2>&1; then
            echo -e "${GREEN}✓ VPN TUN interface is up${NC}"
        fi
        if ss -tlnp 2>/dev/null | grep -q ":9999.*socksproxy"; then
            echo -e "${GREEN}✓ SOCKS5 proxy running on :9999${NC}"
        fi
        ;;

    stop)
        echo -e "${YELLOW}Stopping URnetwork client...${NC}"
        pkill -f "socksproxy" 2>/dev/null && echo "  ✓ SOCKS5 proxy stopped" || echo "  No SOCKS5 proxy running"
        pkill -f "urnetwork-vpn" 2>/dev/null && echo "  ✓ VPN stopped" || echo "  No VPN running"
        if ip link show urnetwork &>/dev/null 2>&1; then
            sudo ip route del default dev urnetwork 2>/dev/null || true
            sudo ip link set urnetwork down 2>/dev/null || true
            echo "  ✓ TUN interface cleaned up"
        fi
        ;;

    help|--help|-h)
        usage
        ;;

    *)
        usage
        ;;
esac
