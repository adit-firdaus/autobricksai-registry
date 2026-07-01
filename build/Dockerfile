# autobricksai-registry — autobot-hermes
#
# Two-stage build that bakes the AutoBricks-specific layer on top of the
# upstream hermes-agent + hermes-workspace bases. See ./README.md for the
# upstream-version pinning policy (do NOT promote :latest tags).
#
# Layout assumption (paths are repository-relative):
#   build/Dockerfile                ← this file (build context = repo root)
#   entrypoint/                     ← entrypoint.sh, supervisord.conf,
#                                     wait-for-hermes-gateway.sh,
#                                     hermes-report-style.css,
#                                     skills-search.py, skills-install.py,
#                                     abai-mcp-server.py
#   patches/                        ← Python + Node patchers applied at
#                                     build time + first entrypoint start
#   skills/skill-workflow/          ← pre-installed skill bundle

# ─── Stage 1: hermes-agent source + Python 3.13 venv ────────────────────────
# Provides /opt/hermes/.venv/bin/hermes CLI (gateway) and /opt/hermes/ source.
#
# Pinned to v2026.6.5 — DO NOT use :latest or :main here. They move silently.
# We got bitten on 2026-06-08: upstream retagged :latest within the same window
# that dropped python-telegram-bot from the [all] extras (~2.5 GB → ~1 GB slim
# release) and the autobot-hermes:latest rebuild silently inherited the change.
# Combined with a supervisord-hermes.conf ENV_CLAUDE_PASSWORD reference and a
# few bots with skinny /opt/data/.env files, this took 4 bots offline (stuck in
# supervisord crash-loop).
# Upgrade ONLY by bumping the version explicitly here, after reviewing
# https://hub.docker.com/r/nousresearch/hermes-agent/tags
FROM nousresearch/hermes-agent:v2026.6.5 AS gateway-src

# ─── Stage 2: hermes-workspace runtime base (Node 22 + built React app) ─────
# Pinned to v2.3.0 — same rationale as above. Never use :latest or :main.
# Upgrade by bumping the version explicitly here, after reviewing
# https://github.com/outsourc-e/hermes-workspace/releases
FROM hermes-workspace-local:v2.3.0

USER root

# Consolidated apt-get: all packages in one layer with a single update/clean
# cycle. Splitting these into 3 separate RUNs added ~50 MB of apt cache
# duplication (each RUN re-ran `apt-get update`).
#   - supervisor: manages gateway (8642) + workspace (8787) processes
#   - nodejs/npm: WhatsApp bridge + camofox-browser + agent-browser
#   - git: skill installs
#   - python3-venv: skill envs
#   - chromium + libgtk-3-0/libx11-xcb1/libdbus-glib-1-2/libxt6/libasound2:
#     Camoufox (Firefox-fork) + agent-browser (Chrome DevTools Protocol) runtime libs
RUN apt-get update && apt-get install -y --no-install-recommends \
        supervisor \
        nodejs \
        npm \
        git \
        python3-venv \
        curl \
        ca-certificates \
        libgtk-3-0 \
        libx11-xcb1 \
        libdbus-glib-1-2 \
        libxt6 \
        libasound2 \
        chromium \
        pandoc \
        libreoffice-calc \
        nano \
        less \
        tree \
        jq \
        htop \
        sudo \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Grant the workspace user passwordless sudo. The sudoers file is created
# unconditionally here, but `entrypoint.sh` deletes it at boot when
# ABAI_RUNTIME != "kata" — so only Kata bots (which run inside an isolated
# QEMU microVM) get sudo. Runc bots boot with this file removed, blocking
# sudo entirely. Pairs with the security_opt drop in docker_host.py: Kata
# bots don't get `no-new-privileges`, runc bots keep it.
RUN echo "workspace ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/workspace \
    && chmod 0440 /etc/sudoers.d/workspace \
    && visudo -c -f /etc/sudoers.d/workspace

# uv — fast Python package installer used by the skill marketplace.
RUN curl -fsSL https://astral.sh/uv/install.sh | env INSTALLER_NO_MODIFY_PATH=1 sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv && \
    rm -rf /root/.cache /root/.local

# ttyd — browser terminal served at /__terminal.
RUN curl -fsSL https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 \
    -o /usr/local/bin/ttyd && chmod +x /usr/local/bin/ttyd

# miniserve — file browser served at /__explorer.
RUN curl -fsSL https://github.com/svenstaro/miniserve/releases/download/v0.35.0/miniserve-0.35.0-x86_64-unknown-linux-musl \
    -o /usr/local/bin/miniserve && chmod +x /usr/local/bin/miniserve

# agent-browser — official Vercel Labs AI-native browser CLI (Node.js v22 already present)
RUN npm install -g agent-browser \
    && (agent-browser install --chromium-path $(which chromium) 2>/dev/null || true) \
    && rm -rf /root/.npm

# `gws` CLI binary -- the execution backend the NousResearch
# productivity/google-workspace skill calls when present (and what the bot
# uses directly for advanced operations like `gws drive permissions create`).
# We do NOT bundle the upstream gws-* SKILL.md files anymore; the canonical
# skill surface is productivity/google-workspace and its SKILL.md gets the
# AutoBricks pre-auth + common-workflows patches applied later in this
# Dockerfile, after /opt/hermes is copied in.
RUN npm install -g @googleworkspace/cli@0.20.0 && \
    GWS_PKG=$(npm root -g)/@googleworkspace/cli && \
    chmod +x "${GWS_PKG}/run-gws.js" && \
    ln -sf "${GWS_PKG}/run-gws.js" /usr/local/bin/gws && \
    rm -rf /root/.npm

# Copy full hermes-agent installation from the gateway stage.
COPY --from=gateway-src /opt/hermes /opt/hermes

# The gateway-src venv was built against Python 3.13; the workspace base (Debian Bookworm)
# has Python 3.11 at /usr/bin/python3. The venv symlinks to /usr/bin/python3, so packages
# in lib/python3.13/site-packages/ are invisible to the running Python. Reinstall hermes
# (and its optional extras) so they land in lib/python3.11/site-packages/.
#
# Drop /opt/hermes/.venv/lib/python3.13 in the same layer — it's ~800 MB of
# Python 3.13-compiled bindings that the active 3.11 interpreter ignores.
# (Cleanup MUST be in the same RUN as the install — separate RUNs don't free
# space on the previous layer.)
RUN VIRTUAL_ENV=/opt/hermes/.venv /usr/local/bin/uv pip install "/opt/hermes[all]" \
    --trusted-host pypi.org --trusted-host files.pythonhosted.org && \
    rm -rf /opt/hermes/.venv/lib/python3.13 /root/.cache

# Patch the NousResearch productivity/google-workspace SKILL.md so its
# upstream `## First-Time Setup` (which walks the user through creating a
# Google Cloud project + pasting an auth code) is replaced with an
# AutoBricks pre-auth note, and append a `## Common Workflows` section
# with verified `gws` examples for share/move/copy/batch-update/drafts/etc.
# The platform writes valid creds on every Connect Google Workspace click;
# the agent only ever needs --check and --revoke. This co-locates skill-
# specific guidance with the skill itself instead of bloating AGENTS.md.
COPY patches/gws-skills-overrides.py /tmp/gws-skills-overrides.py
RUN python3 /tmp/gws-skills-overrides.py --pre-auth \
        /opt/hermes/skills/productivity/google-workspace/SKILL.md && \
    rm -f /tmp/gws-skills-overrides.py

# Add --attach support to the bundled $GAPI gmail send wrapper. Upstream
# google_api.py only accepts --to/--subject/--body/--cc/--from/--html/
# --thread-id — agents asked to "email this PDF" have no way to attach
# without writing custom base64-RFC2822 logic via the raw gws CLI. The
# patch adds a `--attach <path>` flag (repeatable) to both `gmail send`
# and `gmail reply` and switches to MIMEMultipart when attachments are
# present. Idempotent via the `# AUTOBRICKS:ATTACH-V1` marker.
COPY patches/gws-google-api-patch.py /tmp/gws-google-api-patch.py
RUN python3 /tmp/gws-google-api-patch.py \
        /opt/hermes/skills/productivity/google-workspace/scripts/google_api.py && \
    rm -f /tmp/gws-google-api-patch.py

# Slim the bundled skills tree — drops macOS-only, ML-research, gaming,
# Chinese-specific, and Hermes-internal skills irrelevant to AutoBricks AI.
# `email/` (himalaya only) is dropped because google-workspace already
# covers Gmail and the bot was reaching for himalaya as a "lighter
# alternative" in our environment where that's never the right answer.
RUN cd /opt/hermes/skills && rm -rf \
    apple \
    mlops \
    yuanbao \
    gaming \
    smart-home \
    red-teaming \
    dogfood \
    email \
    note-taking/obsidian \
    productivity/nano-pdf \
    creative/baoyu-comic creative/baoyu-infographic \
    creative/manim-video creative/p5js creative/pixel-art \
    creative/ascii-art creative/ascii-video creative/pretext \
    creative/comfyui creative/touchdesigner-mcp \
    creative/songwriting-and-ai-music creative/design-md \
    media/heartmula media/songsee \
    research/polymarket research/llm-wiki \
    devops/kanban-orchestrator devops/kanban-worker \
    software-development/debugging-hermes-tui-commands \
    software-development/node-inspect-debugger

# Pre-install WhatsApp bridge node_modules so pairing doesn't block on npm.
RUN cd /opt/hermes/scripts/whatsapp-bridge && \
    npm ci --no-fund --no-audit --progress=false && \
    rm -rf /root/.npm

# Pre-install Google Workspace skill deps (venv has no pip; use uv directly).
RUN VIRTUAL_ENV=/opt/hermes/.venv /usr/local/bin/uv pip install \
    google-api-python-client google-auth-oauthlib google-auth-httplib2 \
    --trusted-host pypi.org --trusted-host files.pythonhosted.org && \
    rm -rf /root/.cache

# Pre-install camoufox into the hermes gateway venv.
RUN VIRTUAL_ENV=/opt/hermes/.venv /usr/local/bin/uv pip install 'camoufox[geoip]' \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org && \
    rm -rf /root/.cache

# WhatsApp bridge path-fix: hermes_cli/main.py looks for the bridge at
# <site-packages>/scripts/whatsapp-bridge/bridge.js (because Path(__file__).parents[1]
# from hermes_cli/main.py resolves into site-packages/), but the file is shipped
# at /opt/hermes/scripts/whatsapp-bridge/. Symlink so `hermes whatsapp setup`
# finds it.
RUN ln -sfn /opt/hermes/scripts \
    /opt/hermes/.venv/lib/python3.11/site-packages/scripts

# dashboard_auth missing-from-wheel fix: hermes-agent v0.15.1 ships
# `hermes_cli/dashboard_auth/` in the source tree at /opt/hermes/hermes_cli/
# but the wheel install drops it (packaging bug — the subpackage doesn't get
# enumerated). hermes_cli/web_server.py imports `dashboard_auth.public_paths`
# unconditionally at module load, so `hermes dashboard` crash-loops on every
# new bot with `ModuleNotFoundError: No module named 'hermes_cli.dashboard_auth'`.
# Symlink the source subpackage into site-packages/hermes_cli/ so the import
# resolves. Same self-heal pattern as the scripts/ symlink above.
RUN ln -sfn /opt/hermes/hermes_cli/dashboard_auth \
    /opt/hermes/.venv/lib/python3.11/site-packages/hermes_cli/dashboard_auth

# WhatsApp wizard UX — the bot-mode allowlist prompt ships without an example,
# so users type local-format numbers (e.g. "94788250" for SG) without the
# country code. The bridge then rejects every incoming message with
# `allowlist_mismatch` because senderIds normalise to E.164 ("6594788250").
# Patch the prompt to surface the format requirement up-front.
RUN sed -i \
    's|Phone numbers (comma-separated, or \* for anyone)|Phone numbers in E.164 format with country code, no + or spaces (e.g. 6594788250 for SG, 14155551234 for US), or * for anyone|' \
    /opt/hermes/.venv/lib/python3.11/site-packages/hermes_cli/main.py

# pip / pip3 shim — the Hermes venv is uv-built and ships no pip binary, but
# the agent's instinct is to type `pip install <x>`. Without a shim every
# such call returns "command not found" and the agent gives up. Route both
# pip and pip3 through `uv pip` with --python pointing at the Hermes venv so
# packages land in the right site-packages (3.11, where Hermes lives, even
# though the venv interpreter is 3.13).
#
# IMPORTANT: uv pip requires `--python` AFTER the subcommand
# (`uv pip install --python <p> <pkg>`), NOT before (`uv pip --python <p>
# install <pkg>` → "tip: a similar argument exists: '--python-fetch'"). The
# shim splits the first arg off as the subcommand and injects --python in
# the right slot.
RUN printf '%s\n' \
    '#!/usr/bin/env bash' \
    'SUB="$1"; shift || true' \
    'exec /usr/local/bin/uv pip "$SUB" --python /opt/hermes/.venv/bin/python "$@"' \
    > /usr/local/bin/pip \
    && chmod +x /usr/local/bin/pip \
    && ln -sf /usr/local/bin/pip /usr/local/bin/pip3

# python shim — same problem class as pip above. Debian-slim only ships
# `python3` (Bookworm dropped the unversioned alias by default), but the
# agent's instinct is `python <file>.py`. Without a shim the bot gives up
# with "command not found" or escalates to `sudo apt-get install python` and
# hits a perms wall.
#
# Must be a shell SHIM, not a symlink — a symlink to /opt/hermes/.venv/bin/python
# follows on through /usr/bin/python3 (the venv link target), and Python's
# sys.prefix resolution then concludes it's the system Python and ignores the
# venv site-packages (httpx, reportlab, weasyprint, pypdf, …). The shim
# preserves argv[0] = /opt/hermes/.venv/bin/python so Python correctly
# identifies the venv. Don't touch /usr/bin/python3 — system Python stays
# unshimmed for apt-managed tooling.
RUN printf '%s\n' \
    '#!/usr/bin/env bash' \
    'exec /opt/hermes/.venv/bin/python "$@"' \
    > /usr/local/bin/python \
    && chmod +x /usr/local/bin/python

# Pre-install a PDF + XLSX stack so the agent never needs to hunt for
# libraries when the user asks for a PDF report or spreadsheet work.
#   - reportlab: classic programmatic PDF (most agent training data uses it)
#   - fpdf2: lightweight modern alternative
#   - weasyprint: full HTML/CSS → PDF
#   - markdown-it-py: markdown → HTML for the weasyprint pipeline
#   - pypdf: merge / split / read existing PDFs
#   - pandas + openpyxl + xlrd + xlsxwriter: the anthropics/skills/xlsx
#     workflow assumes these are importable. Without them the agent loops
#     on `pip install openpyxl` until it hits the iteration cap.
RUN VIRTUAL_ENV=/opt/hermes/.venv /usr/local/bin/uv pip install \
        reportlab fpdf2 weasyprint markdown-it-py pypdf \
        pandas openpyxl xlrd xlsxwriter \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org && \
    rm -rf /root/.cache

# Pre-install Anthropic's official `pdf` skill into the bundled skills tree
# so every new bot ships with it (and the agent doesn't need to install it
# on first use). Installed via a throwaway HERMES_HOME, then the resulting
# skill dir is copied into /opt/hermes/skills/productivity/pdf where the
# entrypoint's symlink loop picks it up.
RUN HERMES_HOME=/tmp/hermes-skill-install && export HERMES_HOME && \
    mkdir -p "$HERMES_HOME" && \
    /opt/hermes/.venv/bin/hermes skills install anthropics/skills/pdf --yes --force && \
    mkdir -p /opt/hermes/skills/productivity && \
    cp -r "$HERMES_HOME/skills/pdf" /opt/hermes/skills/productivity/pdf && \
    rm -rf "$HERMES_HOME"

# Bake the AutoBricks report stylesheet next to the pdf skill, and patch the
# upstream SKILL.md so the agent prefers weasyprint + this stylesheet over
# pandoc default for any user-facing PDF (analytics reports, summaries, etc).
# Same pattern as the google-workspace SKILL.md patch above. Without this,
# the LLM falls back to `pandoc input.md -o output.pdf` and produces plain
# Times-roman output with no branding.
COPY entrypoint/hermes-report-style.css /opt/hermes/skills/productivity/pdf/autobricks-report.css
COPY patches/patch-hermes-pdf-skill.py /tmp/patch-hermes-pdf-skill.py
RUN python3 /tmp/patch-hermes-pdf-skill.py \
        /opt/hermes/skills/productivity/pdf/SKILL.md && \
    rm -f /tmp/patch-hermes-pdf-skill.py

# Patch Hermes' models.dev integration so our locally-served `autobricksai`
# provider resolves via the `abai` alias. Without this Hermes treats every
# autobricksai/* model as text-only and routes images through the broken
# vision_analyze tool. See patches/patch-hermes-models-dev.py for the marker
# block we append (idempotent — safe to re-run on rebuilds).
COPY patches/patch-hermes-models-dev.py /tmp/patch-hermes-models-dev.py
RUN python3 /tmp/patch-hermes-models-dev.py && rm -f /tmp/patch-hermes-models-dev.py

# Add the /persona <slug> slash command to the runtime. Upstream's /personality
# is global + reads from config.yaml; our /persona is per-(platform, chat_id)
# and reads the bot's installed-agent .md files under /opt/data/agents/. The
# patcher injects into hermes_cli/commands.py + gateway/run.py + agent/
# system_prompt.py, idempotent via marker comments — see
# patches/patch-hermes-persona.py for site-by-site details.
COPY patches/patch-hermes-persona.py /tmp/patch-hermes-persona.py
RUN python3 /tmp/patch-hermes-persona.py && rm -f /tmp/patch-hermes-persona.py

# Bake the AutoBricks `skill-workflow` skill so every bot ships with the
# ability to list + run the owner's Skill Workflows. Auth uses the per-bot
# AUTOBRICKS_API_KEY mounted at /opt/data/.env at provisioning time -
# nothing to "Connect" from the dashboard. Entrypoint's symlink loop
# auto-exposes it at /opt/data/skills/automation/skill-workflow.
COPY skills/skill-workflow /opt/hermes/skills/automation/skill-workflow
RUN chown -R workspace:workspace /opt/hermes/skills/automation/skill-workflow

# Expose the hermes CLI on PATH for in-container terminals.
# copilot is the ACP adapter binary used by hermes-workspace's Operations tab to dispatch swarm tasks.
# sqlite3 CLI is required by hermes-workspace's kanban/tasks board.
RUN ln -sf /opt/hermes/.venv/bin/hermes /usr/local/bin/hermes && \
    ln -sf /opt/hermes/.venv/bin/hermes-acp /usr/local/bin/copilot && \
    apt-get update && apt-get install -y --no-install-recommends sqlite3 && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Remap workspace user UID 10010 → 1000 to match our bind-mount ownership (1000:1000).
# Remove any ghost UID 1000 (typically `node` in the node:22-slim base) first.
RUN userdel "$(getent passwd 1000 | cut -d: -f1)" 2>/dev/null || true && \
    usermod -u 1000 workspace && groupmod -g 1000 workspace && \
    mkdir -p /home/workspace && \
    chown -R workspace:workspace /app /home/workspace /opt/hermes /opt/gws-skills 2>/dev/null || true

# Download the camoufox Firefox binary into the workspace user home so every
# skill venv (which runs as workspace) finds it without a per-install fetch.
# Clean the extraction tempdir in the same layer — `camoufox fetch` leaves
# ~680 MB at /tmp/camoufox-XXXXXX which we don't need at runtime.
RUN su -l workspace -c \
    "/opt/hermes/.venv/bin/python -m camoufox fetch" && \
    rm -rf /tmp/camoufox-* /tmp/pBiQUP /tmp/node-compile-cache

# Camofox-browser (Node.js daemon for stealth automation, separate from the
# python `camoufox` package above). camoufox-js's postinstall fetches its own
# ~300 MB Camoufox binary copy; doing it here means every container starts
# with the daemon ready instead of paying ~5 min on first marketplace install.
# Different binary build than the python one — they aren't dedupable.
RUN mkdir -p /opt/camofox-browser \
    && cd /opt/camofox-browser \
    && npm init -y --silent 2>/dev/null \
    && npm install --production @askjo/camofox-browser \
    && chown -R workspace:workspace /opt/camofox-browser \
    && rm -rf /root/.npm

COPY entrypoint/supervisord.conf /etc/supervisor/conf.d/hermes.conf
COPY entrypoint/entrypoint.sh /entrypoint.sh
COPY entrypoint/wait-for-hermes-gateway.sh /opt/hermes/scripts/wait-for-hermes-gateway.sh
RUN chmod 0755 /opt/hermes/scripts/wait-for-hermes-gateway.sh
COPY patches/patch-workspace-providers.cjs /app/patch-workspace-providers.cjs
COPY patches/patch-agent-meta-seeder.cjs /app/patch-agent-meta-seeder.cjs
COPY patches/patch-skills-card-link.cjs /app/patch-skills-card-link.cjs
COPY patches/patch-skills-install.cjs /app/patch-skills-install.cjs

# Skills marketplace bridge — workspace bundle calls /app/scripts/skills-search.py
# to query the official Hermes Skill Hub (multi-registry: GitHub taps, ClawHub,
# SkillsSh, etc.). The upstream hermes-workspace image ships the route handler
# but not the script — without this, the marketplace silently falls back to
# scanning /app/skills (which only contains workspace-dispatch).
# skills-install.py: same pattern for install/uninstall (workspace bundle's
# /api/skills/install + /api/skills/uninstall return 501 in zero-fork mode;
# patch-skills-install.cjs reroutes them to this script which calls the
# `hermes skills (install|uninstall)` CLI directly.
RUN mkdir -p /app/scripts && chown workspace:workspace /app/scripts
COPY entrypoint/skills-search.py /app/scripts/skills-search.py
COPY entrypoint/skills-install.py /app/scripts/skills-install.py
RUN chmod +x /app/scripts/skills-search.py /app/scripts/skills-install.py && \
    chown workspace:workspace /app/scripts/skills-search.py /app/scripts/skills-install.py

# AutoBricks MCP exec-bridge server — exposes run_command / read_file /
# write_file over MCP at /__mcp/ so a user's local Claude Code (or Cursor
# / Zed / any MCP client) can drive shell + file ops on the bot. Bearer-
# auth'd via ABAI_MCP_TOKEN, owner-rotatable from the autobricks UI.
# Supervised by supervisord-hermes.conf (program:abai-mcp).
COPY entrypoint/abai-mcp-server.py /opt/abai-mcp-server.py
RUN chmod 0755 /opt/abai-mcp-server.py && \
    chown workspace:workspace /opt/abai-mcp-server.py

# Align hermes-agent location with upstream workspace expectations. The vendored
# skills-search.py (and potentially other workspace features) expect hermes at
# ~/hermes-agent, but our image installs it at /opt/hermes (matching the
# upstream gateway image layout). Symlink so both paths resolve.
RUN ln -sfn /opt/hermes /home/workspace/hermes-agent && \
    chown -h workspace:workspace /home/workspace/hermes-agent

RUN chmod +x /entrypoint.sh

RUN mkdir -p /opt/data && chown workspace:workspace /opt/data

EXPOSE 8787

HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:8787/ >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
