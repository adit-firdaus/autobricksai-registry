#!/usr/bin/env bash
# Hermes Local — installer + control panel for students.
# Runs Hermes (WebUI :8787) locally in Docker, wired to AutoBricks as the model provider.
# Image pulled from the autobricksai-registry package on GHCR. Owner name is
# taken from $IMAGE_OWNER (default adit-firdaus; override for forks / mirrors):
#   ghcr.io/${IMAGE_OWNER}/autobricksai-registry/autobot-hermes
# Built by this repo's .github/workflows/build-push.yml (see autobricksai-registry README.md).
#
# Usage (student):
#   curl -fsSL https://adit-firdaus.github.io/autobricksai-registry/install.sh | bash
#   AUTOBRICKS_API_KEY="abai_sk_live_..." curl -fsSL <url> | bash
#
# What you get, interactively:
#   - first run (nothing installed) -> guided install wizard
#   - already installed             -> a menu: Status / Update / Logs / Restart / Stop / Uninstall
# Piped with no terminal (CI) it installs straight through, as before.
#
# Overrides (env):
#   AUTOBRICKS_API_KEY   your abai_sk_live_... key (else you'll be prompted)
#   AUTOBRICKS_BASE_URL  model API base (default https://api.autobricksai.com/v1)
#   IMAGE_OWNER          GHCR namespace owner (default adit-firdaus; e.g. Autobricks-AI)
#   HERMES_IMAGE         full image ref override (pin a tag with ...autobot-hermes:vX.Y.Z)
#   APP_DIR              install dir (default ~/autobricks-hermes)
#   HERMES_ACTION        jump straight to: install|menu|status|update|logs|restart|stop|uninstall
#   HERMES_WIPE=1        when uninstalling non-interactively, also delete ./data
#
# Idempotent: rerunning preserves your data/.env, config.yaml, and generated password.
# ponytail: single self-contained script — embeds the compose file and provider config
#           rather than fetching/generating extra files.
set -euo pipefail

# ---- constants ----
APP_DIR="${APP_DIR:-$HOME/autobricks-hermes}"
DATA_DIR="$APP_DIR/data"
ENV_FILE="$DATA_DIR/.env"
CONFIG_FILE="$DATA_DIR/config.yaml"
COMPOSE_FILE="$APP_DIR/docker-compose.yml"
BASE_URL="${AUTOBRICKS_BASE_URL:-https://api.autobricksai.com/v1}"
IMAGE_OWNER="${IMAGE_OWNER:-adit-firdaus}"
if [ -n "${HERMES_IMAGE:-}" ]; then IMAGE="$HERMES_IMAGE"; else IMAGE="ghcr.io/${IMAGE_OWNER}/autobricksai-registry/autobot-hermes:latest"; fi
DEFAULT_MODEL="autobricksai/mimo-2.5"
PORT=8787
CONTAINER="autobricks-hermes"
API_KEYS_URL="https://autobricksai.com/account/settings?tab=api"
COMPOSE=""  # set by need_docker

# ---- output helpers ----
info()  { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m✔\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }
hr()    { printf '────────────────────────────────────────────────────────\n'; }

# ---- interactivity (script is usually piped, so real input comes from /dev/tty) ----
have_tty() { [ -r /dev/tty ]; }
ask() { # prompt -> echoes the typed line
  local p="$1" a=""
  printf '%s' "$p" > /dev/tty
  read -r a < /dev/tty || true
  printf '%s' "$a"
}
ask_secret() { # prompt -> echoes the typed line, input hidden
  local p="$1" a=""
  printf '%s' "$p" > /dev/tty
  read -rs a < /dev/tty || true
  printf '\n' > /dev/tty
  printf '%s' "$a"
}
askyn() { # prompt default(Y/N) -> 0 for yes, 1 for no
  local a; a="$(ask "$1 ")"; a="${a:-${2:-N}}"
  case "$a" in [Yy]*) return 0;; *) return 1;; esac
}

# ---- data/.env read/upsert (awk-based, no sed escaping traps) ----
read_env() { # key
  [ -f "$ENV_FILE" ] || return 0
  awk -F= -v k="$1" '$1==k{sub(/^[^=]*=/,"");print;exit}' "$ENV_FILE"
}
set_env() { # key value
  mkdir -p "$DATA_DIR"; touch "$ENV_FILE"
  awk -F= -v k="$1" -v v="$2" 'BEGIN{OFS="="}
    $1==k {print k,v; found=1; next} {print}
    END {if(!found) print k,v}' "$ENV_FILE" > "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}
gen_secret() { openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# ---- docker preflight (memoised via $COMPOSE) ----
need_docker() {
  [ -n "$COMPOSE" ] && return 0
  if ! command -v docker >/dev/null 2>&1; then
    case "$(uname -s)" in
      Darwin) hint="Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/" ;;
      Linux)  hint="Install Docker Engine: https://docs.docker.com/engine/install/  (then: sudo usermod -aG docker \$USER && re-login)" ;;
      *)      hint="Install Docker: https://docs.docker.com/get-docker/" ;;
    esac
    die "Docker is not installed. $hint"
  fi
  docker info >/dev/null 2>&1 || die "Docker is installed but the daemon isn't running. Start Docker and re-run."
  if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"
  else die "Docker Compose not found. Update Docker Desktop, or install the compose plugin."; fi
}

# ---- deployment state: running | stopped | missing ----
state() {
  local s; s="$(docker ps -a --filter "name=^/${CONTAINER}$" --format '{{.State}}' 2>/dev/null || true)"
  case "$s" in running) echo running;; "") echo missing;; *) echo stopped;; esac
}

# ---- API key: env > existing .env > prompt (with no-key redirect) ----
resolve_api_key() {
  if [ -n "${AUTOBRICKS_API_KEY:-}" ]; then API_KEY="$AUTOBRICKS_API_KEY"; return; fi
  local existing; existing="$(read_env AUTOBRICKS_API_KEY)"
  if [ -n "$existing" ]; then API_KEY="$existing"; info "Reusing existing AutoBricks API key."; return; fi
  have_tty || die "No API key and no terminal to prompt. Re-run: AUTOBRICKS_API_KEY=abai_sk_live_... curl -fsSL <url> | bash"
  if ! askyn "Do you have an AutoBricks API key? (y/N)" N; then
    info "Create one here:"
    printf '  \033[4m%s\033[0m\n' "$API_KEYS_URL"
    printf '  (sign in → API Keys → create → copy the abai_sk_live_… value, then paste below)\n'
  fi
  API_KEY="$(ask_secret 'Paste your AutoBricks API key: ')"
  [ -n "$API_KEY" ] || die "Empty API key."
}

# ---- config writers ----
write_env() {
  local sk pw
  sk="$(read_env API_SERVER_KEY)"; [ -n "$sk" ] || sk="$(gen_secret)"
  pw="$(read_env CLAUDE_PASSWORD)"; [ -n "$pw" ] || pw="$(gen_secret)"
  set_env AUTOBRICKS_API_KEY "$API_KEY"
  set_env OPENAI_API_KEY     "$API_KEY"          # hermes-workspace OpenAI-compat mode
  set_env API_SERVER_KEY     "$sk"
  set_env HERMES_API_TOKEN   "$sk"               # gateway needs API_SERVER_KEY, UI uses HERMES_API_TOKEN
  set_env CLAUDE_PASSWORD    "$pw"
  set_env HERMES_WEBUI_PASSWORD "$pw"
  set_env HERMES_PASSWORD    "$pw"
  set_env HERMES_HOME        "/opt/data"
  [ -n "$(read_env GITHUB_TOKEN)" ] || set_env GITHUB_TOKEN ""
  ok "Wrote $ENV_FILE (chmod 600)."
}
write_config() {
  if [ -f "$CONFIG_FILE" ]; then info "Keeping existing $CONFIG_FILE."; return; fi
  cat > "$CONFIG_FILE" <<YAML
# AutoBricks model provider for Hermes (OpenAI-compatible gateway).
model:
  default: $DEFAULT_MODEL
  provider: abai
providers:
  abai:
    name: AutoBricks AI
    api: $BASE_URL
    key_env: AUTOBRICKS_API_KEY
    transport: openai_chat
    models:
      - autobricksai/mimo-2.5
      - autobricksai/claude-haiku-4.5
      - autobricksai/claude-sonnet-4.6
      - autobricksai/claude-opus-4.7
      - autobricksai/gemini-2.5-flash
      - autobricksai/gemini-2.5-pro
      - autobricksai/gpt-5.4
      - autobricksai/gpt-4.1
      - autobricksai/deepseek-v4
      - autobricksai/qwen3-235b
YAML
  ok "Wrote $CONFIG_FILE."
}
write_compose() {
  cat > "$COMPOSE_FILE" <<YAML
services:
  hermes:
    image: $IMAGE
    # ponytail: image is built amd64-only; pin platform so Apple Silicon / arm64
    # hosts pull the amd64 image and run it under emulation instead of failing with
    # "no matching manifest for linux/arm64". Native on amd64 Linux.
    platform: linux/amd64
    container_name: $CONTAINER
    env_file: [data/.env]
    environment:
      ABAI_RUNTIME: runc
      HERMES_HOME: /opt/data
      GATEWAY_ALLOW_ALL_USERS: "true"
    ports:
      - "$PORT:$PORT"
    volumes:
      # ponytail: host bind-mount so the installer writes provider config from the host
      # with no docker-cp dance. Entrypoint chowns to 1000:1000 on boot; data persists.
      - ./data:/opt/data
    restart: unless-stopped
YAML
  ok "Wrote $COMPOSE_FILE."
}

# ---- boot wait + ready banner ----
wait_up() {
  info "Waiting for Hermes on http://localhost:$PORT ..."
  local i
  for i in $(seq 1 45); do
    if curl -sf "http://localhost:$PORT/" >/dev/null 2>&1; then UP=1; return; fi
    sleep 2
  done
  UP=""
}
print_ready() {
  local pw; pw="$(read_env HERMES_WEBUI_PASSWORD)"
  echo
  if [ -n "${UP:-}" ]; then ok "Hermes is running."; else
    warn "Hermes didn't answer on :$PORT within ~90s — it may still be booting. Check: $COMPOSE logs -f"
  fi
  cat <<TXT

────────────────────────────────────────────────────────
  Open Hermes:   http://localhost:$PORT
  Login password (save this):
      $pw

  Manage it (re-run this script any time for the menu, or from $APP_DIR):
      $COMPOSE logs -f          # view logs
      $COMPOSE restart          # restart
      $COMPOSE pull && $COMPOSE up -d   # update to latest image
      $COMPOSE down             # stop (keeps your data in ./data)

  Your files & config live in: $DATA_DIR
────────────────────────────────────────────────────────
TXT
}

# ---- actions ----
do_install() {
  need_docker
  local st; st="$(state)"
  if [ "$st" != missing ] && have_tty; then
    hr
    info "An existing Hermes deployment was found ($st)."
    printf '  [R] Reuse existing config, just (re)start\n'
    printf '  [U] Update to the latest image\n'
    printf '  [W] Wipe & reinstall (deletes %s)\n' "$DATA_DIR"
    local c; c="$(ask 'Choose [R/U/W] (default R): ')"; c="${c:-R}"
    case "$c" in
      [Uu]*) do_update; return;;
      [Ww]*)
        if [ -f "$COMPOSE_FILE" ]; then ( cd "$APP_DIR" && $COMPOSE down 2>/dev/null || true ); fi
        rm -rf "$APP_DIR"; ok "Wiped previous install.";;
      *)
        [ -f "$COMPOSE_FILE" ] || die "No compose file to reuse at $COMPOSE_FILE — choose W to reinstall."
        ( cd "$APP_DIR" && $COMPOSE up -d ); wait_up; print_ready; return;;
    esac
  fi

  mkdir -p "$DATA_DIR"
  info "Install dir: $APP_DIR"
  resolve_api_key
  write_env
  write_config
  write_compose

  cd "$APP_DIR"
  info "Pulling image $IMAGE ..."
  $COMPOSE pull
  info "Starting Hermes ..."
  $COMPOSE up -d
  wait_up
  print_ready
}

do_status() {
  need_docker
  local st; st="$(state)"
  hr
  printf '  Hermes Local\n'
  printf '  Image:      %s\n' "$IMAGE"
  printf '  Deployment: %s\n' "$st"
  if [ "$st" != missing ]; then
    printf '  Container:  %s\n' "$(docker ps -a --filter "name=^/${CONTAINER}$" --format '{{.Status}}' 2>/dev/null || true)"
    local dig; dig="$(docker image inspect "$IMAGE" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
    [ -n "$dig" ] && printf '  Digest:     %s\n' "$dig"
    if curl -sf -o /dev/null "http://localhost:$PORT/" 2>/dev/null; then
      printf '  WebUI:      up → http://localhost:%s\n' "$PORT"
    else
      printf '  WebUI:      not responding on :%s\n' "$PORT"
    fi
  else
    printf '  (no container — choose Install)\n'
  fi
  local code; code="$(curl -s -o /dev/null -m 6 -w '%{http_code}' "$BASE_URL/models" 2>/dev/null || echo 000)"
  case "$code" in
    000) printf '  Model API:  unreachable (%s)\n' "$BASE_URL";;
    2*)  printf '  Model API:  reachable (%s)\n' "$BASE_URL";;
    401|403) printf '  Model API:  reachable, needs key (%s)\n' "$BASE_URL";;
    *)   printf '  Model API:  HTTP %s (%s)\n' "$code" "$BASE_URL";;
  esac
  printf '  Data dir:   %s\n' "$DATA_DIR"
  hr
}

do_update() {
  need_docker
  [ -f "$COMPOSE_FILE" ] || die "No install found ($COMPOSE_FILE missing). Choose Install first."
  cd "$APP_DIR"
  local before after
  before="$(docker image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null || echo none)"
  info "Pulling latest image ..."
  $COMPOSE pull
  after="$(docker image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null || echo none)"
  if [ "$before" = "$after" ]; then
    ok "Already up to date."
  else
    info "New image: ${after#sha256:} (was ${before#sha256:})"
  fi
  info "Restarting with latest ..."
  $COMPOSE up -d
  wait_up
  [ -n "${UP:-}" ] && ok "Update complete." || warn "Updated, but :$PORT hasn't answered yet — check logs."
}

do_logs() {
  need_docker
  [ -f "$COMPOSE_FILE" ] || die "No install found. Choose Install first."
  cd "$APP_DIR"
  info "Streaming logs — press Ctrl-C to stop (this ends the script)."
  $COMPOSE logs -f
}

do_restart() {
  need_docker
  [ -f "$COMPOSE_FILE" ] || die "No install found. Choose Install first."
  ( cd "$APP_DIR" && $COMPOSE restart )
  wait_up
  [ -n "${UP:-}" ] && ok "Restarted." || warn "Restarted, but :$PORT hasn't answered yet — check logs."
}

do_stop() {
  need_docker
  [ -f "$COMPOSE_FILE" ] || die "No install found. Choose Install first."
  ( cd "$APP_DIR" && $COMPOSE stop )
  ok "Stopped (container kept; data in $DATA_DIR). Choose Restart to bring it back."
}

do_uninstall() {
  need_docker
  if [ -f "$COMPOSE_FILE" ]; then ( cd "$APP_DIR" && $COMPOSE down 2>/dev/null || true )
  else docker rm -f "$CONTAINER" 2>/dev/null || true; fi
  ok "Container removed."
  if have_tty; then
    if askyn "Also delete your data & config at $DATA_DIR? (y/N)" N; then
      rm -rf "$APP_DIR"; ok "Deleted $APP_DIR."
    else info "Kept $DATA_DIR (your key & password are preserved)."; fi
  elif [ "${HERMES_WIPE:-0}" = 1 ]; then
    rm -rf "$APP_DIR"; ok "Deleted $APP_DIR."
  else
    info "Kept $DATA_DIR (set HERMES_WIPE=1 to remove non-interactively)."
  fi
}

# ---- menu ----
menu() {
  need_docker
  while true; do
    local st; st="$(state)"
    hr
    printf '  Hermes Local — control panel\n'
    printf '  Image:    %s\n' "$IMAGE"
    printf '  Detected: %s\n' "$st"
    hr
    printf '  1) Status / health\n'
    printf '  2) Update to latest\n'
    printf '  3) Logs\n'
    printf '  4) Restart\n'
    printf '  5) Stop\n'
    printf '  6) Uninstall\n'
    printf '  7) Reinstall / reconfigure\n'
    printf '  8) Quit\n'
    local c; c="$(ask '> ')"
    case "$c" in
      1) do_status;;
      2) do_update;;
      3) do_logs;;
      4) do_restart;;
      5) do_stop;;
      6) do_uninstall;;
      7) do_install;;
      8|q|Q) ok "Bye."; exit 0;;
      "") :;;
      *) warn "Pick 1-8.";;
    esac
  done
}

# ---- entry / dispatch ----
main() {
  local action="${HERMES_ACTION:-}"
  if [ -z "$action" ]; then
    if have_tty; then
      case "$(state 2>/dev/null || echo missing)" in
        missing) action="install";;
        *)       action="menu";;
      esac
    else
      action="install"   # backward-compatible non-interactive path
    fi
  fi
  case "$action" in
    install)   do_install;;
    menu)      menu;;
    status)    do_status;;
    update)    do_update;;
    logs)      do_logs;;
    restart)   do_restart;;
    stop)      do_stop;;
    uninstall) do_uninstall;;
    *) die "Unknown HERMES_ACTION='$action' (install|menu|status|update|logs|restart|stop|uninstall)";;
  esac
}
main "$@"
