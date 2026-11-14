#!/usr/bin/env bash
# setup.sh - Zero-friction environment setup and hosting helper for opencode-unlimited-proxy.
#
# Usage:
#   ./scripts/setup.sh                         # Interactive setup wizard
#   ./scripts/setup.sh --accept-tos --ipv6 auto --generate-keys
#   ./scripts/setup.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STACK_DIR="$ROOT_DIR/stack"
ENV_FILE="$STACK_DIR/.env"
ENV_EXAMPLE="$STACK_DIR/.env.example"

# Display helpers
info()  { printf '\033[34mℹ\033[0m %s\n' "$1"; }
ok()    { printf '\033[32m✔\033[0m %s\n' "$1"; }
warn()  { printf '\033[33m⚠\033[0m %s\n' "$1"; }
err()   { printf '\033[31m✖\033[0m %s\n' "$1"; }

show_help() {
    cat <<EOF
Usage: ./scripts/setup.sh [OPTIONS]

Zero-friction setup helper for opencode-unlimited-proxy.
Inspects the host, configures stack/.env, tests connectivity, and generates credentials.

Options:
  --accept-tos             Acknowledge & accept Cloudflare WARP Terms of Service
  --ipv6 <prefix|auto>     Set or auto-detect host global routed IPv6 /64 prefix
  --generate-keys          Generate random secure server API key and WebUI password
  --key <apikey>           Set explicit server API key
  --port <port>            Set custom API listen port (default: 8080)
  --webui-port <port>      Set custom WebUI listen port (default: 8081)
  --pool-size <n>          Set engine WARP pool size (1-32, default: 4)
  --force                  Overwrite existing stack/.env file
  -h, --help               Show this help message

Examples:
  ./scripts/setup.sh
  ./scripts/setup.sh --accept-tos --ipv6 auto --generate-keys
EOF
}

OPT_ACCEPT_TOS=0
OPT_IPV6=""
OPT_GEN_KEYS=0
OPT_KEY=""
OPT_PORT=""
OPT_WEBUI_PORT=""
OPT_POOL_SIZE=""
OPT_FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --accept-tos)
            OPT_ACCEPT_TOS=1
            shift
            ;;
        --ipv6)
            OPT_IPV6="${2:-}"
            shift 2
            ;;
        --generate-keys)
            OPT_GEN_KEYS=1
            shift
            ;;
        --key)
            OPT_KEY="${2:-}"
            shift 2
            ;;
        --port)
            OPT_PORT="${2:-}"
            shift 2
            ;;
        --webui-port)
            OPT_WEBUI_PORT="${2:-}"
            shift 2
            ;;
        --pool-size)
            OPT_POOL_SIZE="${2:-}"
            shift 2
            ;;
        --force)
            OPT_FORCE=1
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            err "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

printf "\033[1m=== opencode-unlimited-proxy setup wizard ===\033[0m\n\n"

# 1. Platform & Environment Check
OS="$(uname -s)"
info "Operating System: $OS ($(uname -m))"

if [ "$OS" != "Linux" ]; then
    warn "Non-Linux OS detected ($OS). Docker Desktop on macOS/Windows lacks host-networking source binding."
    warn "If you run into networking issues, see stack/docker-compose.bridge.yml for bridge mode."
fi

# 2. Docker & Compose Check
if command -v docker >/dev/null 2>&1; then
    if docker compose version >/dev/null 2>&1; then
        ok "Docker and Docker Compose v2 available: $(docker compose version --short 2>/dev/null || echo 'installed')"
    else
        warn "Docker is installed, but 'docker compose' (v2 plugin) was not found."
    fi
else
    warn "Docker is not detected in PATH. You can still prepare .env for deployment."
fi

# 3. Detect Global IPv6 Prefix
DETECTED_IPV6=""
if [ "$OS" = "Linux" ] && command -v ip >/dev/null 2>&1; then
    DETECTED_IPV6=$(ip -6 addr show scope global 2>/dev/null | grep -oE '[0-9a-fA-F:]+/[0-9]+' | head -n 1 || true)
    if [ -n "$DETECTED_IPV6" ]; then
        # Normalise to /64
        PREFIX_BASE=$(echo "$DETECTED_IPV6" | cut -d/ -f1 | awk -F: '{print $1":"$2":"$3":"$4"::"}')
        DETECTED_IPV6="${PREFIX_BASE}/64"
        info "Detected global routed IPv6 prefix: $DETECTED_IPV6"
    fi
fi

# Resolve IPv6 setting
FINAL_IPV6=""
if [ "$OPT_IPV6" = "auto" ]; then
    FINAL_IPV6="$DETECTED_IPV6"
elif [ -n "$OPT_IPV6" ]; then
    FINAL_IPV6="$OPT_IPV6"
elif [ -t 0 ] && [ -n "$DETECTED_IPV6" ]; then
    printf "Use detected IPv6 prefix (%s) for distinct WARP egress blocks? [Y/n] " "$DETECTED_IPV6"
    read -r ans || ans=""
    if [ "$ans" != "n" ] && [ "$ans" != "N" ]; then
        FINAL_IPV6="$DETECTED_IPV6"
    fi
fi

# 4. Terms of Service Acceptance
FINAL_ACCEPT_TOS="$OPT_ACCEPT_TOS"
if [ "$FINAL_ACCEPT_TOS" != "1" ]; then
    if [ -t 0 ]; then
        printf "\nCloudflare WARP Terms: https://www.cloudflare.com/application/terms/\n"
        printf "Do you accept Cloudflare's Terms of Service for the WARP tunnel? [y/N] "
        read -r ans || ans=""
        if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
            FINAL_ACCEPT_TOS=1
        fi
    fi
fi

if [ "$FINAL_ACCEPT_TOS" != "1" ]; then
    warn "WARP_ACCEPT_TOS is set to 0. The engine will require WARP_ACCEPT_TOS=1 in .env before starting."
fi

# 5. Credentials Generation
FINAL_KEY="$OPT_KEY"
FINAL_WEBUI_PASS=""
if [ "$OPT_GEN_KEYS" = "1" ] || ([ -z "$FINAL_KEY" ] && [ -t 0 ]); then
    if [ "$OPT_GEN_KEYS" = "1" ]; then
        GEN_WANTED="y"
    else
        printf "Generate random secure API key and WebUI password? [Y/n] "
        read -r ans || ans=""
        GEN_WANTED="$ans"
    fi

    if [ "$GEN_WANTED" != "n" ] && [ "$GEN_WANTED" != "N" ]; then
        if command -v openssl >/dev/null 2>&1; then
            FINAL_KEY="sk-$(openssl rand -hex 16)"
            FINAL_WEBUI_PASS="$(openssl rand -hex 12)"
        else
            FINAL_KEY="sk-$(python3 -c 'import secrets; print(secrets.token_hex(16))' 2>/dev/null || echo 'change-me-random-key')"
            FINAL_WEBUI_PASS="$(python3 -c 'import secrets; print(secrets.token_hex(12))' 2>/dev/null || echo 'change-me-random-pass')"
        fi
        info "Generated API Key: $FINAL_KEY"
        info "Generated WebUI Password: $FINAL_WEBUI_PASS"
    fi
fi

# 6. Write stack/.env
if [ -f "$ENV_FILE" ] && [ "$OPT_FORCE" != "1" ]; then
    warn "$ENV_FILE already exists. Keeping existing file (use --force to overwrite)."
else
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    
    if [ "$FINAL_ACCEPT_TOS" = "1" ]; then
        sed -i 's/WARP_ACCEPT_TOS=0/WARP_ACCEPT_TOS=1/' "$ENV_FILE"
    fi
    if [ -n "$FINAL_IPV6" ]; then
        sed -i "s|^WARP_SOURCE_IPV6=.*|WARP_SOURCE_IPV6=$FINAL_IPV6|" "$ENV_FILE"
    fi
    if [ -n "$FINAL_KEY" ]; then
        sed -i "s|^# OPENCODE2API_SERVER_KEYS=.*|OPENCODE2API_SERVER_KEYS=$FINAL_KEY|" "$ENV_FILE"
    fi
    if [ -n "$FINAL_WEBUI_PASS" ]; then
        sed -i "s|^# WEBUI_PASSWORD=.*|WEBUI_PASSWORD=$FINAL_WEBUI_PASS|" "$ENV_FILE"
    fi
    if [ -n "$OPT_PORT" ]; then
        sed -i "s|^# LISTEN_PORT=.*|LISTEN_PORT=$OPT_PORT|" "$ENV_FILE"
    fi
    if [ -n "$OPT_WEBUI_PORT" ]; then
        sed -i "s|^# WEBUI_PORT=.*|WEBUI_PORT=$OPT_WEBUI_PORT|" "$ENV_FILE"
    fi
    if [ -n "$OPT_POOL_SIZE" ]; then
        sed -i "s|^# WARP_POOL_SIZE=.*|WARP_POOL_SIZE=$OPT_POOL_SIZE|" "$ENV_FILE"
    fi
    ok "Created and configured $ENV_FILE"
fi

printf "\n\033[1m=== Setup Complete ===\033[0m\n"
printf "Next steps:\n"
printf "  1. Start the gateway:\n"
printf "     \033[36mcd stack && docker compose up -d\033[0m\n"
printf "  2. Verify health:\n"
printf "     \033[36m./scripts/smoke.sh\033[0m\n"
printf "  3. Test upstream gates directly:\n"
printf "     \033[36m./scripts/gate-probe.py\033[0m\n"
if [ -n "$FINAL_KEY" ]; then
printf "\nConfigured API Key: %s\n" "$FINAL_KEY"
fi
