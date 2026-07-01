#!/usr/bin/env bash
# Hermes Local — one-command installer for students.
# Runs Hermes (WebUI :8787) locally in Docker, wired to AutoBricks as the model provider.
# Image pulled from the autobricksai-registry package on GHCR. Owner name is
# taken from $IMAGE_OWNER (default Autobricks-AI; override for forks / mirrors):
#   ghcr.io/${IMAGE_OWNER}/autobricksai-registry/autobot-hermes
# Built by this repo's .github/workflows/build-push.yml (see autobricksai-registry README.md).
#
# Usage (student):
#   curl -fsSL https://autobricksai.com/hermes/install.sh | bash
#   AUTOBRICKS_API_KEY="abai_sk_live_..." curl -fsSL https://autobricksai.com/hermes/install.sh | bash
#
# Overrides (env):
#   AUTOBRICKS_API_KEY   your abai_sk_live_... key (else you'll be prompted)
#   AUTOBRICKS_BASE_URL  model API base (default https://api.autobricksai.com/v1)
#   IMAGE_OWNER          GHCR namespace owner
#                        default Autobricks-AI. For personal mirrors / forks /
#                        class cohorts, set e.g. IMAGE_OWNER=adit-firdaus.
#   HERMES_IMAGE         image ref
#                        default ghcr.io/${IMAGE_OWNER}/autobricksai-registry/autobot-hermes:latest
#                        pin a tag with HERMES_IMAGE=...autobot-hermes:vX.Y.Z or :main-<sha>
#   APP_DIR              install dir (default ~/autobricks-hermes)
#
# Idempotent: rerunning preserves your data/.env, config.yaml, and generated password.
# ponytail: single self-contained script — embeds the compose file and provider config
#           rather than fetching/generating extra files. `?token=` setup links are not
#           implemented yet; use the API-key env var or the interactive prompt.
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/autobricks-hermes}"
DATA_DIR="$APP_DIR/data"
ENV_FILE="$DATA_DIR/.env"
CONFIG_FILE="$DATA_DIR/config.yaml"
COMPOSE_FILE="$APP_DIR/docker-compose.yml"
BASE_URL="${AUTOBRICKS_BASE_URL:-https://api.autobricksai.com/v1}"
IMAGE_OWNER="${IMAGE_OWNER:-Autobricks-AI}"
# Honour a fully-overridden HERMES_IMAGE; otherwise derive from IMAGE_OWNER.
if [ -n "${HERMES_IMAGE:-}" ]; then
  IMAGE="$HERMES_IMAGE"
else
  IMAGE="ghcr.io/${IMAGE_OWNER}/autobricksai-registry/autobot-hermes:latest"
fi
DEFAULT_MODEL="autobricksai/mimo-2.5"
PORT=8787

info()  { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m✔\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ---- read/upsert a KEY=VALUE in data/.env (awk-based, no sed escaping traps) ----
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

# ---- 1. Docker + compose ----
if ! command -v docker >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin) hint="Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/" ;;
    Linux)  hint="Install Docker Engine: https://docs.docker.com/engine/install/  (then: sudo usermod -aG docker \$USER && re-login)" ;;
    *)      hint="Install Docker: https://docs.docker.com/get-docker/" ;;
  esac
  die "Docker is not installed. $hint"
fi
docker info >/dev/null 2>&1 || die "Docker is installed but the daemon isn't running. Start Docker and re-run."

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  die "Docker Compose not found. Update Docker Desktop, or install the compose plugin."
fi
ok "Docker + Compose detected."

# ---- 2. App dir ----
mkdir -p "$DATA_DIR"
info "Install dir: $APP_DIR"

# ---- 3. API key (env > existing .env > prompt via /dev/tty, since stdin is the piped script) ----
existing_key="$(read_env AUTOBRICKS_API_KEY)"
if [ -n "${AUTOBRICKS_API_KEY:-}" ]; then
  API_KEY="$AUTOBRICKS_API_KEY"
elif [ -n "$existing_key" ]; then
  API_KEY="$existing_key"; info "Reusing existing AutoBricks API key."
elif [ -r /dev/tty ]; then
  printf 'Paste your AutoBricks API key (from autobricksai.com → account → API Keys): ' > /dev/tty
  read -rs API_KEY < /dev/tty; printf '\n' > /dev/tty
else
  die "No API key and no terminal to prompt. Re-run: AUTOBRICKS_API_KEY=abai_sk_live_... curl -fsSL <url> | bash"
fi
[ -n "$API_KEY" ] || die "Empty API key."

# ---- 4. Secrets (generate once, preserve on rerun) ----
API_SERVER_KEY="$(read_env API_SERVER_KEY)"; [ -n "$API_SERVER_KEY" ] || API_SERVER_KEY="$(gen_secret)"
WEBUI_PASSWORD="$(read_env CLAUDE_PASSWORD)"; [ -n "$WEBUI_PASSWORD" ] || WEBUI_PASSWORD="$(gen_secret)"

# ---- 5. Write data/.env (mirror the production provisioning wiring) ----
set_env AUTOBRICKS_API_KEY "$API_KEY"
set_env OPENAI_API_KEY     "$API_KEY"          # hermes-workspace OpenAI-compat mode
set_env API_SERVER_KEY     "$API_SERVER_KEY"
set_env HERMES_API_TOKEN   "$API_SERVER_KEY"   # same token: gateway needs API_SERVER_KEY, UI uses HERMES_API_TOKEN
set_env CLAUDE_PASSWORD    "$WEBUI_PASSWORD"
set_env HERMES_WEBUI_PASSWORD "$WEBUI_PASSWORD"
set_env HERMES_PASSWORD    "$WEBUI_PASSWORD"
set_env HERMES_HOME        "/opt/data"
[ -n "$(read_env GITHUB_TOKEN)" ] || set_env GITHUB_TOKEN ""
ok "Wrote $ENV_FILE (chmod 600)."

# ---- 6. Provider config.yaml — only if absent (don't clobber user edits) ----
if [ ! -f "$CONFIG_FILE" ]; then
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
else
  info "Keeping existing $CONFIG_FILE."
fi

# ---- 7. docker-compose.yml (regenerated each run so updates propagate; not user data) ----
cat > "$COMPOSE_FILE" <<YAML
services:
  hermes:
    image: $IMAGE
    container_name: autobricks-hermes
    env_file: [data/.env]
    environment:
      ABAI_RUNTIME: runc
      HERMES_HOME: /opt/data
      GATEWAY_ALLOW_ALL_USERS: "true"
    ports:
      - "$PORT:$PORT"
    volumes:
      # ponytail: host bind-mount (not a named volume) so the installer can write the
      # provider config from the host with no docker-cp dance. Entrypoint chowns to 1000:1000
      # on boot; data persists across up/down. Named volume only if students need portability.
      - ./data:/opt/data
    restart: unless-stopped
YAML
ok "Wrote $COMPOSE_FILE."

# ---- 8. Pull + start ----
cd "$APP_DIR"
info "Pulling image $IMAGE ..."
$COMPOSE pull
info "Starting Hermes ..."
$COMPOSE up -d

# ---- 9. Wait for the WebUI ----
info "Waiting for Hermes to come up on http://localhost:$PORT ..."
up=""
for _ in $(seq 1 45); do
  if curl -sf "http://localhost:$PORT/" >/dev/null 2>&1; then up=1; break; fi
  sleep 2
done

echo
if [ -n "$up" ]; then
  ok "Hermes is running."
else
  warn "Hermes didn't answer on :$PORT within ~90s — it may still be booting. Check: $COMPOSE logs -f"
fi

# ---- 10. Next steps ----
cat <<TXT

────────────────────────────────────────────────────────
  Open Hermes:   http://localhost:$PORT
  Login password (save this — shown once):
      $WEBUI_PASSWORD

  Manage it (from $APP_DIR):
      $COMPOSE logs -f          # view logs
      $COMPOSE restart          # restart
      $COMPOSE pull && $COMPOSE up -d   # update to latest image
      $COMPOSE down             # stop (keeps your data in ./data)

  Your files & config live in: $DATA_DIR
────────────────────────────────────────────────────────
TXT
