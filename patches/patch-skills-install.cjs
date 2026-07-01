#!/usr/bin/env node
/**
 * Server-bundle patch for the hermes-workspace skills marketplace —
 * unblocks Install / Uninstall in zero-fork (dashboard) mode by routing
 * those routes to the local `hermes` CLI via /app/scripts/skills-install.py
 * instead of returning the upstream 501 "legacy enhanced fork" error.
 *
 * Three independent replacements in /app/dist/server/assets/router-*.js:
 *
 *   P1: POST /api/skills/install        — dedicated handler.
 *   P2: POST /api/skills/uninstall      — dedicated handler.
 *   P3: POST /api/skills (generic)      — branch where action != "toggle".
 *
 * Each is sentinel-guarded for idempotency. If the original literal isn't
 * found (e.g. upstream bundle reshuffle), the patch logs the miss and
 * continues — no crash, no half-applied state.
 *
 * Called from entrypoint-hermes.sh at container startup.
 */
const fs = require('fs');
const path = require('path');

const SERVER_ASSETS = '/app/dist/server/assets';

const bundleFile = (() => {
  const candidates = fs.readdirSync(SERVER_ASSETS)
    .filter(f => f.startsWith('router-') && f.endsWith('.js'))
    .map(f => path.join(SERVER_ASSETS, f));
  for (const f of candidates) {
    const src = fs.readFileSync(f, 'utf8');
    if (src.includes('Skill install is only available on the legacy enhanced fork')) {
      return f;
    }
  }
  return null;
})();

if (!bundleFile) {
  console.warn('[patch-skills-install] router-*.js with install routes not found — skipping');
  process.exit(0);
}

let content = fs.readFileSync(bundleFile, 'utf-8');
const beforeLen = content.length;

// ── P1: /api/skills/install ─────────────────────────────────────────────────

const P1_SENTINEL = '/*__abai_install_v1__*/';
const P1_ORIGINAL =
`const capabilities2 = await ensureGatewayProbed();
          if (capabilities2.dashboard.available) {
            return json(
              {
                ok: false,
                error: "Skill install is only available on the legacy enhanced fork right now."
              },
              { status: 501 }
            );
          }`;
const P1_PATCHED =
`const capabilities2 = await ensureGatewayProbed();
          if (capabilities2.dashboard.available) {
            ${P1_SENTINEL}
            try {
              const __r = await execFileAsync("python3", [
                "/app/scripts/skills-install.py", "install",
                identifier, String(Boolean(body.force)), body.category || ""
              ], { timeout: 180000, maxBuffer: 2*1024*1024 });
              const __out = JSON.parse((__r.stdout || "").trim());
              return json(__out, { status: __out.ok ? 200 : 500 });
            } catch (__e) {
              return json({ ok: false, error: (__e && __e.message) || "install failed" }, { status: 500 });
            }
          }`;

let p1 = 'skipped';
if (content.includes(P1_SENTINEL)) {
  p1 = 'already';
} else if (content.includes(P1_ORIGINAL)) {
  content = content.replace(P1_ORIGINAL, P1_PATCHED);
  p1 = 'patched';
} else {
  p1 = 'target-not-found';
}

// ── P2: /api/skills/uninstall ───────────────────────────────────────────────

const P2_SENTINEL = '/*__abai_uninstall_v1__*/';
const P2_ORIGINAL =
`const capabilities2 = await ensureGatewayProbed();
          if (capabilities2.dashboard.available) {
            return json(
              {
                ok: false,
                error: "Skill uninstall is only available on the legacy enhanced fork right now."
              },
              { status: 501 }
            );
          }`;
const P2_PATCHED =
`const capabilities2 = await ensureGatewayProbed();
          if (capabilities2.dashboard.available) {
            ${P2_SENTINEL}
            try {
              const __r = await execFileAsync("python3", [
                "/app/scripts/skills-install.py", "uninstall", name
              ], { timeout: 60000, maxBuffer: 2*1024*1024 });
              const __out = JSON.parse((__r.stdout || "").trim());
              return json(__out, { status: __out.ok ? 200 : 500 });
            } catch (__e) {
              return json({ ok: false, error: (__e && __e.message) || "uninstall failed" }, { status: 500 });
            }
          }`;

let p2 = 'skipped';
if (content.includes(P2_SENTINEL)) {
  p2 = 'already';
} else if (content.includes(P2_ORIGINAL)) {
  content = content.replace(P2_ORIGINAL, P2_PATCHED);
  p2 = 'patched';
} else {
  p2 = 'target-not-found';
}

// ── P3: generic /api/skills POST (action dispatcher) ────────────────────────

const P3_SENTINEL = '/*__abai_generic_v1__*/';
const P3_ORIGINAL =
`if (capabilities2.dashboard.available) {
            if (action !== "toggle") {
              return json(
                {
                  ok: false,
                  error: "Skill install/uninstall is only available on the legacy enhanced fork right now. Zero-fork mode supports listing and toggling installed skills."
                },
                { status: 501 }
              );
            }`;
// P3_PATCHED replaces the entire `if (action !== "toggle") { return 501 }`
// block. After our replacement, control falls through to the original toggle
// handler that immediately follows in the source (const response2 = await
// dashboardFetch$1("/api/skills/toggle", ...)). For action === "install" or
// "uninstall" we return early via the local CLI bridge; for "toggle" (or any
// unknown action) we let the upstream code keep handling it.
const P3_PATCHED =
`if (capabilities2.dashboard.available) {
            ${P3_SENTINEL}
            if (action === "install" || action === "uninstall") {
              try {
                const __args = action === "install"
                  ? ["/app/scripts/skills-install.py", "install",
                     (body.identifier || ""), String(Boolean(body.force)), body.category || ""]
                  : ["/app/scripts/skills-install.py", "uninstall",
                     (body.name || body.identifier || "")];
                const __r = await execFileAsync("python3", __args, {
                  timeout: action === "install" ? 180000 : 60000,
                  maxBuffer: 2*1024*1024,
                });
                const __out = JSON.parse((__r.stdout || "").trim());
                return json(__out, { status: __out.ok ? 200 : 500 });
              } catch (__e) {
                return json({ ok: false, error: (__e && __e.message) || (action + " failed") }, { status: 500 });
              }
            }`;

let p3 = 'skipped';
if (content.includes(P3_SENTINEL)) {
  p3 = 'already';
} else if (content.includes(P3_ORIGINAL)) {
  content = content.replace(P3_ORIGINAL, P3_PATCHED);
  p3 = 'patched';
} else {
  p3 = 'target-not-found';
}

if (content.length !== beforeLen) {
  fs.writeFileSync(bundleFile, content);
}

console.log(
  '[patch-skills-install] ' + path.basename(bundleFile) +
  ' (p1=' + p1 + ' p2=' + p2 + ' p3=' + p3 + ')'
);
