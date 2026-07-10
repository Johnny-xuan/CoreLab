#!/usr/bin/env bash
# CoreLab agent installer — Phase M M-1.1.
#
# One-shot bootstrap: pull the agent source from the backend, build a
# self-contained venv at /opt/corelab-agent, write the TOML config + a
# systemd unit, enable+start. Intended to be piped from curl:
#
#   curl -fsSL https://lab.example.com/api/v1/install/agent.sh \
#     | CORELAB_BACKEND_URL=https://lab.example.com \
#       CORELAB_SERVER_ID=42 \
#       CORELAB_ENROLLMENT_TOKEN=xxx \
#       sudo -E bash
#
# Or save first, inspect, then run:
#
#   curl -fsSL .../install/agent.sh -o install-agent.sh
#   sudo -E CORELAB_BACKEND_URL=... CORELAB_SERVER_ID=... CORELAB_ENROLLMENT_TOKEN=... \
#     bash install-agent.sh
#
# Flags:
#   --dry-run   print the actions but do not execute (useful for inspect)
#   --mock      set mock_mode=true in the agent config (no nvidia-smi needed,
#               handy when testing the wiring on a non-GPU host or container)

set -euo pipefail

# ── Required env from caller ────────────────────────────────────────
# Phase M v5 — accept either CORELAB_BACKEND_URL (legacy single URL) OR
# CORELAB_BACKEND_URLS (comma-separated list). When both are present
# the list form wins. The agent tries every URL in turn on connect.
: "${CORELAB_SERVER_ID:?CORELAB_SERVER_ID is required (integer)}"
: "${CORELAB_ENROLLMENT_TOKEN:?CORELAB_ENROLLMENT_TOKEN is required}"
if [[ -z "${CORELAB_BACKEND_URLS:-}" ]] && [[ -z "${CORELAB_BACKEND_URL:-}" ]]; then
  echo "Either CORELAB_BACKEND_URLS or CORELAB_BACKEND_URL is required." >&2
  exit 1
fi

# ── Flag parsing (before knobs because --user-mode flips defaults) ─
DRY_RUN=0
MOCK_MODE=0
USER_MODE=0       # Phase M v5 M-7 — no-sudo mode for shared/student accounts
for arg in "$@"; do
  case "$arg" in
    --dry-run)   DRY_RUN=1 ;;
    --mock)      MOCK_MODE=1 ;;
    --user-mode) USER_MODE=1 ;;
    -h|--help)
      sed -n '2,32p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# Auto-detect user-mode when running as a non-root user without
# passwordless sudo, which is common on shared lab hosts.
if [[ "$USER_MODE" -eq 0 ]] && [[ "$DRY_RUN" -eq 0 ]] && [[ "$EUID" -ne 0 ]] && ! sudo -n true 2>/dev/null; then
  USER_MODE=1
fi

# ── Knobs (env-overridable) — defaults flip for user-mode ──────────
PYTHON_BIN="${CORELAB_PYTHON_BIN:-python3}"
BASH_BIN="${CORELAB_BASH_BIN:-/bin/bash}"
SSH_KEYGEN_BIN="${CORELAB_SSH_KEYGEN_BIN:-$(command -v ssh-keygen 2>/dev/null || true)}"
INSTALL_BIN="${CORELAB_INSTALL_BIN:-$(command -v install 2>/dev/null || true)}"
CAT_BIN="${CORELAB_CAT_BIN:-$(command -v cat 2>/dev/null || true)}"
USERADD_BIN="${CORELAB_USERADD_BIN:-$(command -v useradd 2>/dev/null || true)}"
USERDEL_BIN="${CORELAB_USERDEL_BIN:-$(command -v userdel 2>/dev/null || true)}"
KILL_BIN="${CORELAB_KILL_BIN:-}"
if [[ -z "$KILL_BIN" ]]; then
  for candidate in /bin/kill /usr/bin/kill; do
    if [[ -x "$candidate" ]]; then
      KILL_BIN="$candidate"
      break
    fi
  done
fi
if [[ "$USER_MODE" -eq 1 ]]; then
  INSTALL_DIR="${CORELAB_INSTALL_DIR:-$HOME/.local/share/corelab-agent}"
  CONFIG_PATH="${CORELAB_CONFIG_PATH:-$HOME/.config/corelab-agent/agent.toml}"
  SERVICE_NAME="${CORELAB_SERVICE_NAME:-corelab-agent}"
  RUN_USER="${CORELAB_RUN_USER:-$(id -un)}"
else
  INSTALL_DIR="${CORELAB_INSTALL_DIR:-/opt/corelab-agent}"
  CONFIG_PATH="${CORELAB_CONFIG_PATH:-/etc/corelab-agent.toml}"
  SERVICE_NAME="${CORELAB_SERVICE_NAME:-corelab-agent}"
  RUN_USER="${CORELAB_RUN_USER:-corelab-agent}"
  SUDOERS_PATH="${CORELAB_SUDOERS_PATH:-/etc/sudoers.d/corelab-agent}"
fi
[[ -n "$SSH_KEYGEN_BIN" ]] || SSH_KEYGEN_BIN="/usr/bin/ssh-keygen"
[[ -n "$INSTALL_BIN" ]] || INSTALL_BIN="/usr/bin/install"
[[ -n "$CAT_BIN" ]] || CAT_BIN="/usr/bin/cat"
[[ -n "$USERADD_BIN" ]] || USERADD_BIN="/usr/sbin/useradd"
[[ -n "$USERDEL_BIN" ]] || USERDEL_BIN="/usr/sbin/userdel"
[[ -n "$KILL_BIN" ]] || KILL_BIN="/bin/kill"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

run() {
  printf '    + %s\n' "$*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    eval "$@"
  fi
}

# ── Derive WSS URLs from HTTP URL(s) for the agent's backend_urls field
# Backend serves install.sh + tarball over HTTP(S); agent connects via
# WSS. Same host, scheme is http→ws / https→wss. Phase M v5 — we now
# normalize a possibly-comma-separated list, one URL per entry.
http_to_wss() {
  local u="$1"
  case "$u" in
    https://*) echo "wss://${u#https://}/ws/agent" ;;
    http://*)  echo "ws://${u#http://}/ws/agent" ;;
    wss://*)   echo "${u%/}/ws/agent"
               # If user passed wss://x/ws/agent strip the duplicate.
               ;;
    ws://*)    echo "${u%/}/ws/agent" ;;
    *) die "URL must start with http(s)/ws(s) (got: $u)" ;;
  esac
}

WSS_URLS_CSV=""
if [[ -n "${CORELAB_BACKEND_URLS:-}" ]]; then
  # comma-separated list: split into URLs.
  IFS=',' read -r -a __URLS <<< "$CORELAB_BACKEND_URLS"
  for u in "${__URLS[@]}"; do
    u_trimmed="$(echo "$u" | xargs)"   # trim
    [[ -z "$u_trimmed" ]] && continue
    WSS_URLS_CSV+="$(http_to_wss "$u_trimmed"),"
  done
else
  WSS_URLS_CSV="$(http_to_wss "$CORELAB_BACKEND_URL"),"
fi
# strip trailing comma
WSS_URLS_CSV="${WSS_URLS_CSV%,}"

# Take the first URL as the "primary" for legacy logging / fallback HTTP.
PRIMARY_WSS_URL="${WSS_URLS_CSV%%,*}"

# Primary HTTP URL used for any installer-time http calls (e.g. /install/agent.tar.gz).
# Use CORELAB_BACKEND_URL if given; else derive from the first URL by flipping scheme.
if [[ -n "${CORELAB_BACKEND_URL:-}" ]]; then
  PRIMARY_HTTP_URL="$CORELAB_BACKEND_URL"
else
  # Derive HTTP URL from the first URL in the list.
  first_url="${CORELAB_BACKEND_URLS%%,*}"
  first_url="$(echo "$first_url" | xargs)"
  case "$first_url" in
    https://*) PRIMARY_HTTP_URL="$first_url" ;;
    http://*)  PRIMARY_HTTP_URL="$first_url" ;;
    wss://*)   PRIMARY_HTTP_URL="https://${first_url#wss://}" ;;
    ws://*)    PRIMARY_HTTP_URL="http://${first_url#ws://}" ;;
    *) die "Unparseable URL: $first_url" ;;
  esac
  # Strip /ws/agent suffix if present, since this URL is for HTTP downloads.
  PRIMARY_HTTP_URL="${PRIMARY_HTTP_URL%/ws/agent}"
  CORELAB_BACKEND_URL="$PRIMARY_HTTP_URL"
fi

# ── 0. Header ───────────────────────────────────────────────────────
log "CoreLab agent installer"
echo "    backend HTTP : $CORELAB_BACKEND_URL"
echo "    backend WSS  : $WSS_URLS_CSV"
echo "    server id    : $CORELAB_SERVER_ID"
echo "    install dir  : $INSTALL_DIR"
echo "    config       : $CONFIG_PATH"
echo "    service      : $SERVICE_NAME"
echo "    mock mode    : $([[ $MOCK_MODE -eq 1 ]] && echo 'YES' || echo 'no')"
echo "    user mode    : $([[ $USER_MODE -eq 1 ]] && echo 'YES (no-sudo, systemd --user)' || echo 'no (system-wide)')"
[[ "$DRY_RUN" -eq 1 ]] && warn "DRY-RUN: nothing will be written or executed"
echo

# ── 1. Precheck ─────────────────────────────────────────────────────
log "Precheck"
if [[ "$DRY_RUN" -eq 0 ]] && [[ "$USER_MODE" -eq 0 ]]; then
  [[ "$EUID" -eq 0 ]] || die "must run as root (use sudo) — or pass --user-mode for no-sudo install"
fi
command -v "$PYTHON_BIN" >/dev/null || die "$PYTHON_BIN not found"
PY_OK=$("$PYTHON_BIN" -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)')
[[ "$PY_OK" == "1" ]] || die "$PYTHON_BIN >= 3.11 required ($("$PYTHON_BIN" -V))"
command -v curl >/dev/null || die "curl not found"
command -v tar  >/dev/null || die "tar not found"
if [[ "$USER_MODE" -eq 0 ]] && [[ "$DRY_RUN" -eq 0 ]]; then
  command -v sudo >/dev/null || die "sudo not found"
  command -v visudo >/dev/null || die "visudo not found"
  [[ -x "$BASH_BIN" ]] || die "$BASH_BIN not executable"
  [[ -x "$SSH_KEYGEN_BIN" ]] || die "$SSH_KEYGEN_BIN not executable"
  [[ -x "$INSTALL_BIN" ]] || die "$INSTALL_BIN not executable"
  [[ -x "$CAT_BIN" ]] || die "$CAT_BIN not executable"
  [[ -x "$USERADD_BIN" ]] || die "$USERADD_BIN not executable"
  [[ -x "$USERDEL_BIN" ]] || die "$USERDEL_BIN not executable"
  [[ -x "$KILL_BIN" ]] || die "$KILL_BIN not executable"
fi
if [[ "$DRY_RUN" -eq 0 ]]; then
  command -v systemctl >/dev/null || die "systemctl not found (this script targets systemd hosts)"
fi
printf '    python : %s\n' "$("$PYTHON_BIN" -V)"
printf '    curl   : %s\n' "$(curl --version | head -1)"
if [[ "$USER_MODE" -eq 0 ]]; then
  printf '    bash   : %s\n' "$BASH_BIN"
  printf '    sshkey : %s\n' "$SSH_KEYGEN_BIN"
  printf '    install: %s\n' "$INSTALL_BIN"
  printf '    cat    : %s\n' "$CAT_BIN"
  printf '    useradd: %s\n' "$USERADD_BIN"
  printf '    userdel: %s\n' "$USERDEL_BIN"
  printf '    kill   : %s\n' "$KILL_BIN"
fi
echo

# ── 2. Download agent source tarball ────────────────────────────────
log "Download agent source"
TARBALL="$(mktemp "${TMPDIR:-/tmp}/corelab-agent.XXXXXX")"
trap 'rm -f "$TARBALL"' EXIT
TARBALL_URL="${CORELAB_BACKEND_URL%/}/api/v1/install/agent.tar.gz"
run "curl -fsSL '$TARBALL_URL' -o '$TARBALL'"

# ── 3. Stage into install dir ───────────────────────────────────────
log "Stage source into $INSTALL_DIR"
run "mkdir -p '$INSTALL_DIR'"
run "tar -xzf '$TARBALL' -C '$INSTALL_DIR'"

# ── 4. Build venv + install agent + protocol ────────────────────────
log "Set up Python venv at $INSTALL_DIR/.venv"
run "'$PYTHON_BIN' -m venv '$INSTALL_DIR/.venv'"
run "'$INSTALL_DIR/.venv/bin/pip' install --quiet --upgrade pip"
# Install protocol first (agent depends on it via [tool.uv.sources]
# workspace mapping; outside the uv workspace we resolve it manually).
run "'$INSTALL_DIR/.venv/bin/pip' install --quiet -e '$INSTALL_DIR/shared/protocol'"
run "'$INSTALL_DIR/.venv/bin/pip' install --quiet -e '$INSTALL_DIR/agent'"

# ── 5. User account / ownership ─────────────────────────────────────
if [[ "$USER_MODE" -eq 1 ]]; then
  log "User-mode: running as $RUN_USER, no useradd / chown"
  # ~/.config/corelab-agent might not exist yet — create it before we
  # try to install the toml in step 6.
  run "mkdir -p '$(dirname "$CONFIG_PATH")'"
else
  log "Ensure system user $RUN_USER"
  if id "$RUN_USER" >/dev/null 2>&1; then
    echo "    user $RUN_USER already exists"
  else
    run "useradd --system --no-create-home --shell /usr/sbin/nologin '$RUN_USER'"
  fi
  run "chown -R '$RUN_USER:$RUN_USER' '$INSTALL_DIR'"
fi

# ── 5b. sudoers for implemented privileged operations ───────────────
if [[ "$USER_MODE" -eq 0 ]]; then
  log "Write sudoers whitelist at $SUDOERS_PATH"
  TMP_SUDOERS="$(mktemp /tmp/corelab-agent-sudoers.XXXXXX)"
  {
    echo "# CoreLab agent sudoers — generated by install-agent.sh."
    echo "# Allows the agent service user to perform implemented host-local operations."
    echo "Defaults:$RUN_USER !requiretty"
    echo ""
    echo "Cmnd_Alias CORELAB_SCRIPT = $BASH_BIN -c *"
    echo "Cmnd_Alias CORELAB_SSH_VERIFY = $SSH_KEYGEN_BIN -Y verify *"
    echo "Cmnd_Alias CORELAB_AUTHKEYS = $INSTALL_BIN -m 700 -o * -g * -d /home/*/.ssh, $INSTALL_BIN -m 600 -o * -g * /tmp/corelab-authkeys-* /home/*/.ssh/authorized_keys, $CAT_BIN /home/*/.ssh/authorized_keys"
    echo "Cmnd_Alias CORELAB_USERMGMT = $USERADD_BIN *, $USERDEL_BIN *"
    echo "Cmnd_Alias CORELAB_PROCESS = $KILL_BIN -TERM *, $KILL_BIN -KILL *"
    echo "$RUN_USER ALL=(ALL) NOPASSWD: CORELAB_SCRIPT, CORELAB_SSH_VERIFY, CORELAB_AUTHKEYS, CORELAB_USERMGMT, CORELAB_PROCESS"
  } > "$TMP_SUDOERS"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "    [would write] $SUDOERS_PATH:"
    sed 's/^/      | /' "$TMP_SUDOERS"
    rm -f "$TMP_SUDOERS"
  else
    install -m 0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_PATH"
    rm -f "$TMP_SUDOERS"
    if ! visudo -c -f "$SUDOERS_PATH" >/dev/null; then
      rm -f "$SUDOERS_PATH"
      die "sudoers validation failed for $SUDOERS_PATH"
    fi
  fi
fi

# ── 6. Config file ──────────────────────────────────────────────────
log "Write $CONFIG_PATH"
TMP_TOML="$(mktemp /tmp/corelab-agent-toml.XXXXXX)"
{
  echo "# CoreLab agent config — generated by install-agent.sh."
  echo "# Mode 0600, owner $RUN_USER."
  echo ""
  # Phase M v5 — write the multi-URL list. Agent tries each in order.
  printf 'backend_urls = ['
  first=1
  IFS=',' read -r -a __WSS_LIST <<< "$WSS_URLS_CSV"
  for u in "${__WSS_LIST[@]}"; do
    if [[ $first -eq 1 ]]; then
      printf '"%s"' "$u"
      first=0
    else
      printf ', "%s"' "$u"
    fi
  done
  printf ']\n'
  echo "server_id = $CORELAB_SERVER_ID"
  echo "enrollment_token = \"$CORELAB_ENROLLMENT_TOKEN\""
  [[ "$MOCK_MODE" -eq 1 ]] && echo "mock_mode = true"
} > "$TMP_TOML"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "    [would write] $CONFIG_PATH:"
  sed 's/^/      | /' "$TMP_TOML"
  rm -f "$TMP_TOML"
elif [[ "$USER_MODE" -eq 1 ]]; then
  # User-mode: no chown — file is owned by the current user.
  install -m 0600 "$TMP_TOML" "$CONFIG_PATH"
  rm -f "$TMP_TOML"
else
  install -m 0600 -o "$RUN_USER" -g "$RUN_USER" "$TMP_TOML" "$CONFIG_PATH"
  rm -f "$TMP_TOML"
fi

# ── 7. systemd unit ─────────────────────────────────────────────────
if [[ "$USER_MODE" -eq 1 ]]; then
  UNIT_PATH="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
else
  UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
fi
log "Write $UNIT_PATH"
TMP_UNIT="$(mktemp /tmp/corelab-agent-unit.XXXXXX)"
{
  echo "[Unit]"
  echo "Description=CoreLab agent (server #$CORELAB_SERVER_ID)"
  echo "After=network-online.target"
  echo "Wants=network-online.target"
  echo ""
  echo "[Service]"
  echo "Type=simple"
  if [[ "$USER_MODE" -eq 0 ]]; then
    echo "User=$RUN_USER"
    echo "Group=$RUN_USER"
  fi
  echo "ExecStart=$INSTALL_DIR/.venv/bin/corelab-agent --config $CONFIG_PATH"
  echo "Restart=on-failure"
  echo "RestartSec=5"
  if [[ "$USER_MODE" -eq 1 ]]; then
    # systemd --user has no journal binding by default — leave stdout
    # to its inherited file (it ends up in journal --user-unit).
    echo "StandardOutput=journal"
    echo "StandardError=journal"
  else
    echo "StandardOutput=journal"
    echo "StandardError=journal"
  fi
  echo ""
  echo "[Install]"
  if [[ "$USER_MODE" -eq 1 ]]; then
    echo "WantedBy=default.target"
  else
    echo "WantedBy=multi-user.target"
  fi
} > "$TMP_UNIT"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "    [would write] $UNIT_PATH:"
  sed 's/^/      | /' "$TMP_UNIT"
  rm -f "$TMP_UNIT"
else
  run "mkdir -p '$(dirname "$UNIT_PATH")'"
  install -m 0644 "$TMP_UNIT" "$UNIT_PATH"
  rm -f "$TMP_UNIT"
fi

# ── 8. Enable + start ───────────────────────────────────────────────
if [[ "$USER_MODE" -eq 1 ]]; then
  log "Reload + enable + start $SERVICE_NAME (user scope)"
  # enable-linger lets the unit run even when the user is not logged in
  # — critical for headless ssh-then-disconnect workflows.
  if command -v loginctl >/dev/null 2>&1; then
    run "loginctl enable-linger '$(id -un)' || true"
  fi
  run "systemctl --user daemon-reload"
  run "systemctl --user enable '$SERVICE_NAME' >/dev/null"
  run "systemctl --user restart '$SERVICE_NAME'"
else
  log "Reload + enable + start $SERVICE_NAME"
  run "systemctl daemon-reload"
  run "systemctl enable '$SERVICE_NAME' >/dev/null"
  run "systemctl restart '$SERVICE_NAME'"
fi

# ── 9. Status check ─────────────────────────────────────────────────
if [[ "$DRY_RUN" -eq 0 ]]; then
  sleep 2
  log "Status"
  if [[ "$USER_MODE" -eq 1 ]]; then
    systemctl --user is-active "$SERVICE_NAME" >/dev/null \
      && printf '    service is \033[1;32mactive\033[0m\n' \
      || warn "service is not active — check 'journalctl --user -u $SERVICE_NAME -n 80'"
  else
    systemctl is-active "$SERVICE_NAME" >/dev/null \
      && printf '    service is \033[1;32mactive\033[0m\n' \
      || warn "service is not active — check 'journalctl -u $SERVICE_NAME -n 80'"
  fi
fi

echo
log "Done. Open the CoreLab web UI to see this server come online."
if [[ "$USER_MODE" -eq 1 ]]; then
  echo "    Inspect logs:  journalctl --user -u $SERVICE_NAME -f"
  echo "    Edit config:   $CONFIG_PATH (then systemctl --user restart $SERVICE_NAME)"
  echo
  warn "User-mode caveat: this agent has no sudoers privileges."
  warn "  Backend RPCs that need root (useradd / push_ssh_key / authorized_keys install) will fail."
  warn "  Suitable for connection tests and read-only telemetry; script execution and process kill still need system-mode sudoers."
else
  echo "    Inspect logs:  journalctl -u $SERVICE_NAME -f"
  echo "    Edit config:   $CONFIG_PATH (then systemctl restart $SERVICE_NAME)"
  echo "    Sudoers:       $SUDOERS_PATH"
fi
