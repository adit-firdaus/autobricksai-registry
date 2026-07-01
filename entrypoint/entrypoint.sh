#!/bin/sh
set -e

HERMES_HOME="${HERMES_HOME:-/opt/data}"

# Sudo gate. The Dockerfile drops a passwordless sudoers entry for the
# workspace user; we only keep it on Kata bots where the QEMU microVM is the
# actual security boundary. On runc (shared host kernel) we delete the file
# so sudo refuses even though the binary is installed. ABAI_RUNTIME is set by
# the autobricksai provision flow as a -e env on `docker run`.
if [ "${ABAI_RUNTIME:-}" != "kata" ]; then
    rm -f /etc/sudoers.d/workspace
fi

# Create the directory structure the gateway expects at runtime.
# Done as root before processes start so the workspace user (UID 1000) can write freely.
mkdir -p \
    "$HERMES_HOME/cron" \
    "$HERMES_HOME/sessions" \
    "$HERMES_HOME/logs" \
    "$HERMES_HOME/hooks" \
    "$HERMES_HOME/memories" \
    "$HERMES_HOME/skills" \
    "$HERMES_HOME/skins" \
    "$HERMES_HOME/plans" \
    "$HERMES_HOME/workspace" \
    "$HERMES_HOME/home" \
    "$HERMES_HOME/agents" \
    "$HERMES_HOME/gws"
chmod 0700 "$HERMES_HOME/gws" 2>/dev/null || true
chown -R 1000:1000 "$HERMES_HOME" 2>/dev/null || true

# Expose all bundled Hermes skills in HERMES_HOME so `hermes skills list` discovers them.
# Creates per-skill symlinks rather than category symlinks so custom skills installed under
# the same category name (e.g. memory/, search/) coexist without conflicts.
for skill_dir in /opt/hermes/skills/*/*; do
    [ -d "$skill_dir" ] || continue
    cat=$(basename "$(dirname "$skill_dir")")
    slug=$(basename "$skill_dir")
    target="$HERMES_HOME/skills/$cat/$slug"
    [ -e "$target" ] && continue
    mkdir -p "$HERMES_HOME/skills/$cat"
    ln -sfn "$skill_dir" "$target"
done

# Special-case: skill-workflow must be a REAL DIRECTORY, not a symlink. The
# Hermes `skill_manage` tool's `_find_skill()` uses `Path.rglob("SKILL.md")`
# which does NOT descend into symlinked subdirectories on Python 3.11. That
# leaves skill-workflow invisible to skill_manage, and the agent then errors
# with "Skill 'skill-workflow' not found in active profile 'default'" when a
# user asks to run a workflow from chat. `hermes skills list` (CLI) and
# `_find_all_skills()` (agent's skills_list tool) both follow symlinks fine,
# so the discrepancy is purely in `_find_skill`'s walk.
#
# Other bundled skills (google-workspace, pdf, etc.) hit the same rglob gap
# in principle, but the agent invokes them via `skill_view`/`hermes skills run`
# which take a path directly — `_find_skill` isn't on the hot path there.
# `skill-workflow` IS on the hot path because the agent calls `skill_manage`
# to introspect it before invoking. Convert just this one for now.
SW_LINK="$HERMES_HOME/skills/automation/skill-workflow"
SW_SRC="/opt/hermes/skills/automation/skill-workflow"
if [ -L "$SW_LINK" ] && [ -d "$SW_SRC" ]; then
    rm -f "$SW_LINK"
    cp -r "$SW_SRC" "$SW_LINK"
    chown -R 1000:1000 "$SW_LINK" 2>/dev/null || true
fi

# Migration: clean up stale gws-* symlinks left over from the previous
# image (which bundled /opt/gws-skills/ and symlinked each gws-<slug>
# under HERMES_HOME/skills/google-workspace/). The bundle is gone now -
# Google Workspace guidance lives entirely in the NousResearch
# productivity/google-workspace SKILL.md - so any leftover symlinks point
# at non-existent /opt/gws-skills/<slug> and would clutter the agent's
# skills_list with broken entries. Idempotent: prunes only broken symlinks.
if [ -d "$HERMES_HOME/skills/google-workspace" ]; then
    find "$HERMES_HOME/skills/google-workspace" -maxdepth 1 -type l \
        ! -exec test -e {} \; -delete 2>/dev/null || true
    # If the directory is now empty, remove it so it doesn't show as a
    # category in the Skills Browser.
    rmdir "$HERMES_HOME/skills/google-workspace" 2>/dev/null || true
fi
chown -R 1000:1000 "$HERMES_HOME/skills" 2>/dev/null || true

# pty-helper.py (terminal) chdirs to /home/workspace/.hermes as the working directory.
mkdir -p /home/workspace/.hermes
chown -R 1000:1000 /home/workspace/.hermes 2>/dev/null || true

# Defensive: hermes_cli/main.py expects scripts/whatsapp-bridge/bridge.js under
# site-packages/, but the bridge is shipped at /opt/hermes/scripts/. Re-create
# the symlink at startup so containers built from older images self-heal.
SP="/opt/hermes/.venv/lib/python3.11/site-packages"
if [ ! -e "$SP/scripts" ] && [ -d "/opt/hermes/scripts" ]; then
    ln -sfn /opt/hermes/scripts "$SP/scripts" 2>/dev/null || true
fi

# Defensive: hermes-agent v0.15.1 wheel drops hermes_cli/dashboard_auth/ (the
# source is at /opt/hermes/hermes_cli/dashboard_auth/ but pip didn't ship it).
# Without this symlink, `hermes dashboard` crash-loops with
# `ModuleNotFoundError: No module named 'hermes_cli.dashboard_auth'`.
if [ ! -e "$SP/hermes_cli/dashboard_auth" ] && [ -d "/opt/hermes/hermes_cli/dashboard_auth" ]; then
    ln -sfn /opt/hermes/hermes_cli/dashboard_auth "$SP/hermes_cli/dashboard_auth" 2>/dev/null || true
fi

# Kanban plugin (Tasks page in workspace).
# hermes-agent ships the dashboard plugin source at /opt/hermes/plugins/kanban
# but doesn't auto-install it — `hermes plugins install` only accepts Git URLs.
# Without /opt/data/plugins/kanban present + enabled, the workspace Tasks page
# shows "Failed to load tasks" because /api/plugins/kanban/board on the
# dashboard returns the SPA shell instead of JSON.
#
# Symlink so /opt/data/plugins/kanban → /opt/hermes/plugins/kanban (matches
# how individual skills are symlinked from the bundled tree). Single source
# of truth, zero drift on hermes-agent upgrades. Dashboard plugin scanner is
# Python and follows symlinks natively, unlike the workspace's Node-based
# skills scanner which we had to patch separately.
# Order matters: this MUST run before supervisord starts the dashboard.
if [ -d /opt/hermes/plugins/kanban ]; then
    mkdir -p "$HERMES_HOME/plugins"
    ln -sfn /opt/hermes/plugins/kanban "$HERMES_HOME/plugins/kanban"
    chown -h 1000:1000 "$HERMES_HOME/plugins/kanban" 2>/dev/null || true
    # `hermes plugins enable` writes ~/.hermes/plugins.json for the workspace
    # user. Idempotent — second-run is a no-op. Cap wall time so a hung CLI
    # cannot block supervisord (seen on dh4 mass-restart: entrypoint stuck for
    # 20+ minutes, all bots unhealthy, gw/ws=000). Non-login su avoids .profile
    # hangs under load.
    su -s /bin/sh workspace -c "timeout 30 /opt/hermes/.venv/bin/hermes plugins enable kanban" 2>/dev/null || true
fi

# Seed default config files if the provision step didn't write them yet.
if [ ! -f "$HERMES_HOME/.env" ] && [ -f "/opt/hermes/.env.example" ]; then
    cp "/opt/hermes/.env.example" "$HERMES_HOME/.env"
    chown workspace:workspace "$HERMES_HOME/.env" 2>/dev/null || true
fi
if [ ! -f "$HERMES_HOME/config.yaml" ] && [ -f "/opt/hermes/cli-config.yaml.example" ]; then
    cp "/opt/hermes/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
    chown workspace:workspace "$HERMES_HOME/config.yaml" 2>/dev/null || true
fi

# Source .env so HERMES_PASSWORD (and any other vars) are in the environment
# when supervisord starts — programs use %(ENV_HERMES_PASSWORD)s substitution.
set -a
[ -f "$HERMES_HOME/.env" ] && . "$HERMES_HOME/.env"
set +a

# Defensive defaults for env vars supervisord-hermes.conf references via
# %(ENV_*)s substitution. If any are absent (older provisioned bots where the
# provisioning code didn't yet write them, or bots whose .env got truncated by
# an interrupted write), supervisord aborts parsing with
# "Format string ... contains names ('ENV_FOO') which cannot be expanded"
# and the container crash-loops indefinitely. Setting empty defaults lets
# supervisord parse cleanly — auth-gated features may degrade until a proper
# provisioning sync runs, but the container boots and is reachable.
: "${CLAUDE_PASSWORD:=}"
: "${GITHUB_TOKEN:=}"
: "${HERMES_API_TOKEN:=}"
: "${API_SERVER_KEY:=}"
export CLAUDE_PASSWORD GITHUB_TOKEN HERMES_API_TOKEN API_SERVER_KEY

# Patch hermes-workspace provider list to add AutoBricks AI entry
node /app/patch-workspace-providers.cjs 2>/dev/null || true

# Patch Operations bundle to seed agent system prompts from profile config.yaml
node /app/patch-agent-meta-seeder.cjs 2>/dev/null || true

# Patch marketplace skill card to add a "View source" link to the original skill page
node /app/patch-skills-card-link.cjs 2>/dev/null || true

# Patch marketplace install/uninstall server routes to use the local hermes CLI
# instead of returning 501 "legacy enhanced fork only" in zero-fork mode.
node /app/patch-skills-install.cjs 2>/dev/null || true

# Symlink swarm roster into the persistent data volume so Operations tab
# agents survive container recreation (workspace CWD is /app, roster = /app/swarm.yaml).
ln -sf /opt/data/swarm.yaml /app/swarm.yaml 2>/dev/null || true

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/hermes.conf -n
