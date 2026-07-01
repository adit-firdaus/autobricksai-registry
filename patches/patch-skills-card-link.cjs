#!/usr/bin/env node
/**
 * Three patches to the skills marketplace bundles:
 *
 *   P1 (CLIENT grid card): inject a "View source ↗" link next to "Details"
 *      so users can open the skill's source page in a new tab without
 *      expanding the modal. Visible directly on each card.
 *
 *   P2 (CLIENT "Details" modal): rewrite the Homepage link's href so it
 *      always points to a fully-qualified external URL. Without this, the
 *      bundle's client-side normalizer falls back to a bare "org/repo" path
 *      (e.g., "vercel-labs/agent-browser") which makes <a href> relative →
 *      404 on the workspace domain.
 *
 *   P3 (SSR/server "Details" modal): same fix on the SSR copy of the same
 *      component (server/assets/skills-*.js). The server bundle is
 *      unminified TSX-output and uses the variable name `selectedSkill`,
 *      so the pattern is different but the fix is identical.
 *
 * All three patches use the same URL-normalization logic (IIFE):
 *   - already a URL (http/https) → use as-is
 *   - looks like "org/repo"       → prepend "https://github.com/"
 *   - anything else               → render no link
 *
 * Idempotent via sentinels. Silent no-op if any target string is missing.
 *
 * Called from entrypoint-hermes.sh at container startup.
 */
const fs = require('fs');
const path = require('path');

const CLIENT_ASSETS = '/app/dist/client/assets';

const bundleFile = (() => {
  const candidates = fs.readdirSync(CLIENT_ASSETS)
    .filter(f => f.startsWith('skills-') && f.endsWith('.js'))
    .map(f => path.join(CLIENT_ASSETS, f));
  for (const f of candidates) {
    const src = fs.readFileSync(f, 'utf8');
    // The marketplace grid card uniquely contains a Details button bound to b(s)
    if (src.includes('onClick:()=>b(s),children:"Details"')) return f;
  }
  return null;
})();

if (!bundleFile) {
  console.warn('[patch-skills-card-link] skills-*.js with marketplace card not found — skipping');
  process.exit(0);
}

let content = fs.readFileSync(bundleFile, 'utf-8');
const beforeLength = content.length;

// ── P1: grid card "View source ↗" link ──────────────────────────────────────

const P1_SENTINEL_V1 = '/*__abai_skills_card_link__*/';
const P1_SENTINEL_V2 = '/*__abai_skills_card_link_v2__*/';
const P1_ORIGINAL = 'e.jsx(f,{variant:"outline",size:"sm",onClick:()=>b(s),children:"Details"})';
const P1_PATCHED =
  P1_ORIGINAL +
  ',' + P1_SENTINEL_V2 +
  '(()=>{const v=s.homepage;if(!v)return null;' +
  'const u=/^https?:\\/\\//i.test(v)?v:(/^[\\w.-]+\\/[\\w.-]+$/.test(v)?"https://github.com/"+v:null);' +
  'return u?e.jsx("a",{href:u,target:"_blank",rel:"noreferrer",' +
  'className:"inline-flex items-center text-xs text-primary-500 underline decoration-border underline-offset-4 hover:text-primary hover:decoration-primary",' +
  'children:"View source ↗"}):null;})()';

let p1Status = 'skipped';
if (content.includes(P1_SENTINEL_V2)) {
  p1Status = 'already-v2';
} else if (content.includes(P1_SENTINEL_V1)) {
  p1Status = 'v1-present-needs-bundle-restore';
} else if (content.includes(P1_ORIGINAL)) {
  content = content.replace(P1_ORIGINAL, P1_PATCHED);
  p1Status = 'patched';
}

// ── P2: expanded modal "Homepage:" link ────────────────────────────────────

const P2_SENTINEL = '/*__abai_skills_modal_homepage_v1__*/';
const P2_ORIGINAL =
  'n.homepage?e.jsxs("p",{className:"text-sm text-primary-500 text-pretty",children:["Homepage:"," ",e.jsx("a",{href:n.homepage,target:"_blank",rel:"noreferrer",className:"underline decoration-border underline-offset-4 hover:decoration-primary",children:n.homepage})]}):null';
const P2_PATCHED =
  P2_SENTINEL +
  '(()=>{const v=n.homepage;if(!v)return null;' +
  'const u=/^https?:\\/\\//i.test(v)?v:(/^[\\w.-]+\\/[\\w.-]+$/.test(v)?"https://github.com/"+v:null);' +
  'return u?e.jsxs("p",{className:"text-sm text-primary-500 text-pretty",children:["Homepage:"," ",' +
  'e.jsx("a",{href:u,target:"_blank",rel:"noreferrer",' +
  'className:"underline decoration-border underline-offset-4 hover:decoration-primary",children:u})]}):null;})()';

let p2Status = 'skipped';
if (content.includes(P2_SENTINEL)) {
  p2Status = 'already-patched';
} else if (content.includes(P2_ORIGINAL)) {
  content = content.replace(P2_ORIGINAL, P2_PATCHED);
  p2Status = 'patched';
}

// ── Write client bundle if changed ─────────────────────────────────────────

if (content.length !== beforeLength) {
  fs.writeFileSync(bundleFile, content);
}

// ── P3: SSR server-side skills bundle modal Homepage link ──────────────────
// The SSR-rendered React tree includes a `selectedSkill.homepage`-driven
// link. Even though hydration replaces it client-side, the initial paint
// shows the SSR HTML — so a user who clicks fast on the broken link will
// hit a 404 on the workspace domain. Patching the SSR copy too keeps the
// link correct from first paint.

const SERVER_ASSETS = '/app/dist/server/assets';
const ssrBundleFile = (() => {
  try {
    const candidates = fs.readdirSync(SERVER_ASSETS)
      .filter(f => f.startsWith('skills-') && f.endsWith('.js'))
      .map(f => path.join(SERVER_ASSETS, f));
    for (const f of candidates) {
      const src = fs.readFileSync(f, 'utf8');
      if (src.includes('selectedSkill.homepage') && src.includes('"Homepage:"')) return f;
    }
  } catch {}
  return null;
})();

let p3Status = 'no-target-bundle';
if (ssrBundleFile) {
  let ssr = fs.readFileSync(ssrBundleFile, 'utf-8');
  const ssrLen = ssr.length;
  const P3_SENTINEL = '/*__abai_skills_ssr_homepage_v1__*/';
  // The unminified SSR pattern is multi-line. We anchor on the unique start
  // (`selectedSkill.homepage ? /* @__PURE__ */ jsxs("p"`) and the unique end
  // (`children: selectedSkill.homepage` ... `] }) : null`) — anything in
  // between is consumed lazily.
  const P3_REGEX = /selectedSkill\.homepage \? \/\* @__PURE__ \*\/ jsxs\("p"[\s\S]*?children: selectedSkill\.homepage[\s\S]*?\] \}\) : null/;
  if (ssr.includes(P3_SENTINEL)) {
    p3Status = 'already-patched';
  } else {
    const m = ssr.match(P3_REGEX);
    if (m) {
      const replacement =
        P3_SENTINEL +
        '(()=>{const v=selectedSkill.homepage;if(!v)return null;' +
        'const u=/^https?:\\/\\//i.test(v)?v:(/^[\\w.-]+\\/[\\w.-]+$/.test(v)?"https://github.com/"+v:null);' +
        'return u? jsxs("p",{className:"text-sm text-primary-500 text-pretty",children:["Homepage:"," ",' +
        'jsx("a",{href:u,target:"_blank",rel:"noreferrer",' +
        'className:"underline decoration-border underline-offset-4 hover:decoration-primary",children:u})]}):null;})()';
      ssr = ssr.replace(m[0], replacement);
      fs.writeFileSync(ssrBundleFile, ssr);
      p3Status = 'patched';
    } else {
      p3Status = 'target-not-found';
    }
  }
}

console.log(
  '[patch-skills-card-link] ' + path.basename(bundleFile) +
  ' (p1=' + p1Status + ' p2=' + p2Status + ')' +
  (ssrBundleFile ? ' + ' + path.basename(ssrBundleFile) + ' (p3=' + p3Status + ')' : ' (no SSR bundle)')
);
