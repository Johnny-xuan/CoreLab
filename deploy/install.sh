#!/usr/bin/env bash
# CoreLab — backend installer (Phase M v5).
#
# Stranger story (default = LAN mode, no flag):
#   $ git clone .../corelab && cd corelab
#   $ ./deploy/install.sh
#
#   → probes LAN IP + egress public IP
#   → generates all secrets via openssl rand
#   → writes deploy/.env (preserves any existing .env unless --force)
#   → docker compose up -d, waits backend healthy
#   → seeds the discovered URLs into redis under
#       corelab:install:seed_urls   so setup_service.initialize() can
#       fold them into lab.public_urls when the first lab is created
#   → prints the URL list so the admin picks which to share
#
# Opt-in tunnel (校外用户场景):
#   $ ./deploy/install.sh --tunnel cloudflare
#
#   → also brings up the cloudflared profile in compose, waits for the
#     trycloudflare URL to appear in cloudflared logs, appends it to
#     the seed list. No Cloudflare account / DNS / domain needed.
#
# Auto-detected public-IP mode: if probing the egress public IP shows
# the host is directly reachable from the internet, that URL is added
# to the seed list automatically. Future M-5.3 work will trigger Caddy
# ACME on it.
#
# Re-running on a host that already has a .env keeps the existing
# secrets (idempotent restart) unless --force is given.

set -euo pipefail

# ─── flags ─────────────────────────────────────────────────────────────
TUNNEL_MODE="none"      # none | cloudflare
SKIP_AUTO_INSTALL=0
FORCE_RESET_ENV=0
DRY_RUN=0
HTTP_PORT="${HTTP_PORT:-80}"

usage() {
  cat <<EOF
CoreLab installer v5

Usage: $0 [--tunnel cloudflare] [--force] [--no-auto-install] [--dry-run]

Options:
  --tunnel cloudflare   Also start the Cloudflare Quick Tunnel sidecar
                        so users outside this LAN can reach CoreLab.
  --force               Overwrite existing deploy/.env (regenerates secrets).
                        Default: keep existing secrets if .env is present.
  --no-auto-install     Do not attempt to install docker via get.docker.com
                        when no container runtime is found.
  --dry-run             Print what would happen, change nothing.
  -h, --help            Show this help.

Environment:
  HTTP_PORT=8080        Override host HTTP port (default 80).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tunnel)
      TUNNEL_MODE="${2:-cloudflare}"
      shift 2
      ;;
    --tunnel=*)
      TUNNEL_MODE="${1#--tunnel=}"
      shift
      ;;
    --force)
      FORCE_RESET_ENV=1
      shift
      ;;
    --no-auto-install)
      SKIP_AUTO_INSTALL=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$TUNNEL_MODE" in
  none|cloudflare) ;;
  *)
    echo "Unsupported --tunnel mode: $TUNNEL_MODE (only 'cloudflare' for now)" >&2
    exit 1
    ;;
esac

# ─── helpers ───────────────────────────────────────────────────────────
c_log() { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
c_ok()  { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
c_warn(){ printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
c_err() { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; }
have()  { command -v "$1" >/dev/null 2>&1; }

# Resolve repo root and deploy dir up front so the rest of the script
# can use absolute paths (avoids "where is $0" surprises when stranger
# pipes us through curl | bash).
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
DEPLOY_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"

# ─── 1. detect container runtime ──────────────────────────────────────
RUNTIME=""        # docker | podman
COMPOSE_CMD=""    # the exact compose invocation to use

detect_runtime() {
  if have docker && docker compose version >/dev/null 2>&1; then
    RUNTIME="docker"
    COMPOSE_CMD="docker compose"
    c_ok "Detected: docker + docker compose"
    return 0
  fi
  if have docker && have docker-compose; then
    RUNTIME="docker"
    COMPOSE_CMD="docker-compose"
    c_ok "Detected: docker + docker-compose (legacy)"
    return 0
  fi
  if have podman && podman compose version >/dev/null 2>&1; then
    RUNTIME="podman"
    COMPOSE_CMD="podman compose"
    c_ok "Detected: podman + podman compose"
    return 0
  fi
  if have podman && have podman-compose; then
    RUNTIME="podman"
    COMPOSE_CMD="podman-compose"
    c_ok "Detected: podman + podman-compose"
    return 0
  fi
  return 1
}

try_auto_install_docker() {
  if [[ $SKIP_AUTO_INSTALL -eq 1 ]]; then
    c_err "No container runtime found, and --no-auto-install is set."
    c_err "Install docker (https://get.docker.com) or podman + compose, then re-run."
    exit 1
  fi
  if [[ $EUID -ne 0 ]] && ! sudo -n true 2>/dev/null; then
    c_err "No container runtime found, and no root / passwordless sudo to install one."
    c_err "Either:"
    c_err "  - install docker (curl -fsSL https://get.docker.com | sudo sh)"
    c_err "  - install podman + podman-compose via your package manager"
    c_err "  - run this installer as root"
    exit 1
  fi
  c_log "No container runtime found — auto-installing docker via get.docker.com..."
  if [[ $DRY_RUN -eq 1 ]]; then
    c_log "(dry-run) would run: curl -fsSL https://get.docker.com | sh"
    return 0
  fi
  if [[ $EUID -eq 0 ]]; then
    curl -fsSL https://get.docker.com | sh
  else
    curl -fsSL https://get.docker.com | sudo sh
  fi
  if ! detect_runtime; then
    c_err "docker installed but compose subcommand still missing. Check manually."
    exit 1
  fi
}

# ─── 2. generate (or preserve) secrets ────────────────────────────────
MYSQL_ROOT_PASSWORD=""
MYSQL_USER_PASSWORD=""
MYSQL_APP_USER_PASSWORD=""
JWT_SECRET=""

# `openssl rand -base64` may emit chars MySQL or env-file syntax dislike
# (=, /, +). Strip them post-hoc and slice to a fixed length so the
# secret is always env-safe.
gen_secret() {
  local bytes="$1" length="$2"
  openssl rand -base64 "$bytes" | tr -d '\n+/=' | head -c "$length"
}

read_existing_env_value() {
  local key="$1" file="$2"
  awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/,""); print; exit}' "$file"
}

prepare_secrets() {
  local env_file="$DEPLOY_DIR/.env"
  if [[ -f "$env_file" ]] && [[ $FORCE_RESET_ENV -eq 0 ]]; then
    c_log "Existing $env_file detected — preserving existing secrets."
    MYSQL_ROOT_PASSWORD="$(read_existing_env_value MYSQL_ROOT_PASSWORD "$env_file")"
    MYSQL_USER_PASSWORD="$(read_existing_env_value MYSQL_USER_PASSWORD "$env_file")"
    MYSQL_APP_USER_PASSWORD="$(read_existing_env_value MYSQL_APP_USER_PASSWORD "$env_file")"
    JWT_SECRET="$(read_existing_env_value JWT_SECRET "$env_file")"
    # Regenerate any missing pieces (e.g. user upgraded from old .env).
    [[ -n "$MYSQL_ROOT_PASSWORD" ]] || MYSQL_ROOT_PASSWORD="$(gen_secret 32 32)"
    [[ -n "$MYSQL_USER_PASSWORD" ]] || MYSQL_USER_PASSWORD="$(gen_secret 32 32)"
    [[ -n "$MYSQL_APP_USER_PASSWORD" ]] || MYSQL_APP_USER_PASSWORD="$(gen_secret 32 32)"
    [[ -n "$JWT_SECRET" ]] || JWT_SECRET="$(gen_secret 48 48)"
  else
    c_log "Generating secrets via openssl rand..."
    MYSQL_ROOT_PASSWORD="$(gen_secret 32 32)"
    MYSQL_USER_PASSWORD="$(gen_secret 32 32)"
    MYSQL_APP_USER_PASSWORD="$(gen_secret 32 32)"
    JWT_SECRET="$(gen_secret 48 48)"
  fi
}

# ─── 3. probe network ─────────────────────────────────────────────────
LAN_IP=""
PUBLIC_IP=""
PUBLIC_IP_REACHABLE=0     # set later via /healthz check after backend is up
TUNNEL_URL=""             # filled in if --tunnel cloudflare succeeds

probe_lan_ip() {
  # Each branch is wrapped in `|| true` because pipefail + the fact
  # that any of these tools may be missing (mac has no `ip`, no
  # `hostname -I`) would otherwise abort the whole installer just
  # because one probe attempt happened to be unsupported.

  # Prefer the source IP for the route to the public internet — this
  # is the interface the host actually uses to leave the LAN and is
  # what other machines on the same LAN can reach back on.
  if have ip; then
    LAN_IP="$(ip route get 1.1.1.1 2>/dev/null \
              | awk '/src/ {for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}' \
              || true)"
  fi
  if [[ -z "${LAN_IP:-}" ]] && have hostname; then
    LAN_IP="$( { hostname -I 2>/dev/null || true; } | awk '{print $1}' || true)"
  fi
  if [[ -z "${LAN_IP:-}" ]] && have ifconfig; then
    # macOS / BSD fallback
    LAN_IP="$(ifconfig 2>/dev/null \
              | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}' \
              || true)"
  fi
}

probe_public_ip() {
  # Tries a few different services so a single one being blocked
  # (campus firewall, GFW, etc.) doesn't break the probe outright.
  local svc
  for svc in https://ifconfig.me https://api.ipify.org https://ifconfig.co; do
    PUBLIC_IP="$(curl -fsSL --max-time 5 "$svc" 2>/dev/null | tr -d '[:space:]')"
    if [[ -n "$PUBLIC_IP" ]]; then
      return 0
    fi
  done
  PUBLIC_IP=""
}

probe_network() {
  c_log "Probing network..."
  probe_lan_ip
  probe_public_ip

  if [[ -n "$LAN_IP" ]]; then
    c_ok "  LAN IP        : $LAN_IP"
  else
    c_warn "  LAN IP        : (could not determine)"
  fi
  if [[ -n "$PUBLIC_IP" ]]; then
    if [[ "$PUBLIC_IP" == "$LAN_IP" ]]; then
      c_ok "  Public IP     : $PUBLIC_IP (same as LAN — likely a public-facing host)"
    else
      c_ok "  Public IP     : $PUBLIC_IP (egress; inbound reachability to be tested after start)"
    fi
  else
    c_warn "  Public IP     : (could not determine — no outbound to ifconfig.me et al)"
  fi
}

# ─── 4. write .env ────────────────────────────────────────────────────
write_env() {
  local env_file="$DEPLOY_DIR/.env"
  local primary_url="http://localhost:${HTTP_PORT}"
  if [[ -n "$LAN_IP" ]]; then
    primary_url="http://${LAN_IP}:${HTTP_PORT}"
  fi

  c_log "Writing $env_file..."
  if [[ $DRY_RUN -eq 1 ]]; then
    c_log "(dry-run) would write .env with primary URL = $primary_url"
    return 0
  fi

  cat > "$env_file" <<EOF
# CoreLab — generated by deploy/install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
#
# DO NOT commit this file. Regenerate via deploy/install.sh --force.

MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}
MYSQL_USER_PASSWORD=${MYSQL_USER_PASSWORD}
MYSQL_APP_USER_PASSWORD=${MYSQL_APP_USER_PASSWORD}
JWT_SECRET=${JWT_SECRET}

MYSQL_HOST_PORT=3307
CORELAB_DATA_DIR=./data
HTTP_PORT=${HTTP_PORT}

# Primary public URL — picked by install.sh from network probe (prefers
# LAN IP). This is the URL baked into the backend public-URL var and
# into agent install snippets. Override / add more URLs from the
# Public Access card in Lab Overview after setup.
BACKEND_PUBLIC_URL=${primary_url}

# Tunnel mode set by install.sh (none | cloudflare_quick).
# Default 'none' = LAN mode (vLLM-style direct port binding). Set
# to cloudflare_quick when started with --tunnel cloudflare.
CORELAB_INITIAL_TUNNEL_MODE=$([[ "$TUNNEL_MODE" == "cloudflare" ]] && echo "cloudflare_quick" || echo "none")

CORS_ORIGINS=
LOG_LEVEL=INFO

# Normal deployments should run the reservation tick + URL probe scheduler.
# Set SCHEDULER_ENABLED=false only for debugging.
SCHEDULER_ENABLED=true
SCHEDULER_TICK_SECONDS=30
EOF
  chmod 600 "$env_file" || true
}

# ─── 5. docker compose up ─────────────────────────────────────────────
compose_up() {
  c_log "$COMPOSE_CMD up -d --build..."
  if [[ $DRY_RUN -eq 1 ]]; then
    local profile_arg=""
    [[ "$TUNNEL_MODE" == "cloudflare" ]] && profile_arg="--profile tunnel "
    c_log "(dry-run) would: cd $DEPLOY_DIR && $COMPOSE_CMD ${profile_arg}up -d --build"
    return 0
  fi
  cd "$DEPLOY_DIR"
  if [[ "$TUNNEL_MODE" == "cloudflare" ]]; then
    $COMPOSE_CMD --profile tunnel up -d --build
  else
    $COMPOSE_CMD up -d --build
  fi
}

# ─── 6. wait for backend ──────────────────────────────────────────────
wait_for_backend() {
  c_log "Waiting for backend /healthz on localhost:${HTTP_PORT}..."
  if [[ $DRY_RUN -eq 1 ]]; then
    c_log "(dry-run) skipping healthcheck."
    return 0
  fi
  local i
  for i in $(seq 1 60); do
    if curl -fsS --max-time 2 "http://localhost:${HTTP_PORT}/healthz" >/dev/null 2>&1; then
      c_ok "Backend healthy."
      return 0
    fi
    sleep 2
  done
  c_err "Backend did not become healthy in 120s."
  c_err "Run: $COMPOSE_CMD logs backend"
  exit 1
}

# ─── 7. test public IP inbound reachability ──────────────────────────
# Best-effort: ask api.ipify-style services to reflect a probe, OR use
# a public HTTP→TCP probe service. As a no-extra-dep approach we just
# try curling http://$PUBLIC_IP:$HTTP_PORT/healthz from this very host
# — this works iff the host has direct internet ingress OR the LAN
# router NATs the egress back to itself (most home routers do that
# hairpinning; campus NATs sometimes don't). False negatives are OK:
# unreachable means "can't confirm" not "definitely no".
probe_public_reachable() {
  [[ -n "$PUBLIC_IP" ]] || return 0
  [[ "$PUBLIC_IP" != "$LAN_IP" ]] || return 0   # same IP — no extra info

  c_log "Probing inbound reachability of $PUBLIC_IP:${HTTP_PORT} (best-effort)..."
  if curl -fsS --max-time 5 "http://${PUBLIC_IP}:${HTTP_PORT}/healthz" >/dev/null 2>&1; then
    PUBLIC_IP_REACHABLE=1
    c_ok "  Public IP $PUBLIC_IP:${HTTP_PORT} appears reachable from this host (no guarantee from real internet)."
  else
    PUBLIC_IP_REACHABLE=0
    c_warn "  Public IP $PUBLIC_IP:${HTTP_PORT} not reachable from this host — probably NAT/firewall blocks inbound."
  fi
}

# ─── 8. parse cloudflared URL (if --tunnel cloudflare) ───────────────
wait_for_tunnel_url() {
  [[ "$TUNNEL_MODE" == "cloudflare" ]] || return 0
  c_log "Waiting for Cloudflare Quick Tunnel URL (parses cloudflared logs)..."
  if [[ $DRY_RUN -eq 1 ]]; then
    TUNNEL_URL="https://dry-run.trycloudflare.com"
    return 0
  fi
  local i
  for i in $(seq 1 60); do
    TUNNEL_URL="$($COMPOSE_CMD logs cloudflared 2>&1 \
                  | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' \
                  | tail -1 || true)"
    if [[ -n "$TUNNEL_URL" ]]; then
      c_ok "Tunnel URL: $TUNNEL_URL"
      return 0
    fi
    sleep 2
  done
  c_warn "Could not parse trycloudflare URL from cloudflared logs within 120s."
  c_warn "Run: $COMPOSE_CMD logs cloudflared    to inspect."
}

# ─── 9. seed initial URLs into redis ──────────────────────────────────
# Setup wizard (setup_service.initialize, M-5.2 task) will pop this key
# when creating the first lab and fold the URLs into lab.public_urls.
#
# Redis is on tmpfs so this seed naturally expires on container restart
# — that's fine, install.sh re-seeds every run.
seed_urls_to_redis() {
  if [[ $DRY_RUN -eq 1 ]]; then
    c_log "(dry-run) would seed redis key corelab:install:seed_urls"
    return 0
  fi

  # Build JSON array of seed entries. Shape matches what setup_service
  # will write into lab.public_urls (kind/source/url).
  local entries=()
  if [[ -n "$LAN_IP" ]]; then
    entries+=("$(printf '{"url":"http://%s:%s","kind":"lan","source":"install_sh_probe"}' "$LAN_IP" "$HTTP_PORT")")
  fi
  if [[ -n "$PUBLIC_IP" ]] && [[ "$PUBLIC_IP" != "$LAN_IP" ]]; then
    entries+=("$(printf '{"url":"http://%s:%s","kind":"public_ip","source":"install_sh_probe","verified_reachable":%s}' \
                "$PUBLIC_IP" "$HTTP_PORT" "$([[ $PUBLIC_IP_REACHABLE -eq 1 ]] && echo true || echo false)")")
  fi
  if [[ -n "$TUNNEL_URL" ]]; then
    entries+=("$(printf '{"url":"%s","kind":"cloudflare_quick","source":"tunnel_runtime"}' "$TUNNEL_URL")")
  fi

  if [[ ${#entries[@]} -eq 0 ]]; then
    c_warn "No URLs discovered to seed (probe found nothing)."
    return 0
  fi

  local json="["
  local sep=""
  local e
  for e in "${entries[@]}"; do
    json="${json}${sep}${e}"
    sep=","
  done
  json="${json}]"

  cd "$DEPLOY_DIR"
  if printf '%s' "$json" | $COMPOSE_CMD exec -T redis redis-cli -x SET corelab:install:seed_urls >/dev/null 2>&1; then
    c_ok "Seeded ${#entries[@]} URL(s) into redis (corelab:install:seed_urls)."
  else
    c_warn "Could not seed URLs to redis (will fall back to BACKEND_PUBLIC_URL only at setup)."
  fi
}

# ─── 10. final summary ────────────────────────────────────────────────
print_summary() {
  echo
  echo "═══════════════════════════════════════════════════════════════"
  echo "🎉 CoreLab is up."
  echo
  echo "Open ONE of these URLs in a browser that can reach this host:"
  echo
  local printed=0
  if [[ -n "$LAN_IP" ]]; then
    printf '  \033[1;37mhttp://%s:%s\033[0m\n' "$LAN_IP" "$HTTP_PORT"
    echo  "    LAN mode — reachable by anyone on this LAN / your campus network / VPN"
    printed=1
  fi
  if [[ -n "$PUBLIC_IP" ]] && [[ "$PUBLIC_IP" != "$LAN_IP" ]]; then
    printf '  \033[1;37mhttp://%s:%s\033[0m\n' "$PUBLIC_IP" "$HTTP_PORT"
    if [[ $PUBLIC_IP_REACHABLE -eq 1 ]]; then
      echo "    Public IP — appears reachable from the internet (great, no tunnel needed)"
    else
      echo "    Public IP — egress only; inbound likely blocked by NAT/firewall (won't work from internet without a port-forward)"
    fi
    printed=1
  fi
  if [[ -n "$TUNNEL_URL" ]]; then
    printf '  \033[1;37m%s\033[0m\n' "$TUNNEL_URL"
    echo  "    Cloudflare Quick Tunnel — reachable from anywhere on the internet"
    echo  "    ⚠ URL is random and changes when cloudflared restarts."
    echo  "    ⚠ Quick Tunnels have a 200-concurrent-conn limit; upgrade to a Named Tunnel"
    echo  "      from the Public Access card after setup if needed."
    printed=1
  fi
  if [[ $printed -eq 0 ]]; then
    printf '  \033[1;37mhttp://localhost:%s\033[0m\n' "$HTTP_PORT"
    echo  "    Same-host only (network probe found nothing else)"
  fi
  echo
  if [[ "$TUNNEL_MODE" != "cloudflare" ]]; then
    echo "Need users outside this LAN/campus to access CoreLab?"
    echo "  Either re-run with: ./deploy/install.sh --tunnel cloudflare"
    echo "  Or enable Tunnel later in the Public Access card after setup."
    echo
  fi
  echo "Next: open one URL above → setup wizard (lab name + admin account)."
  echo "═══════════════════════════════════════════════════════════════"
}

# ─── main ─────────────────────────────────────────────────────────────
main() {
  c_log "CoreLab installer v5"
  c_log "===================="
  if [[ "$TUNNEL_MODE" == "cloudflare" ]]; then
    c_log "Mode: LAN + Cloudflare Quick Tunnel"
  else
    c_log "Mode: LAN only (re-run with --tunnel cloudflare to expose to the internet)"
  fi

  if ! detect_runtime; then
    try_auto_install_docker
  fi

  prepare_secrets
  probe_network
  write_env
  compose_up
  wait_for_backend
  probe_public_reachable
  wait_for_tunnel_url
  seed_urls_to_redis
  print_summary
}

main "$@"
