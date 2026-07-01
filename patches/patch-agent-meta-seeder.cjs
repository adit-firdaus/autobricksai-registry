#!/usr/bin/env node
/**
 * Patch the hermes-workspace Operations client bundle so installed agents
 * show their system prompt, emoji, and description without manual configuration.
 *
 * Two-part patch applied to operations-*.js:
 *
 *   1. Injects window.__abaiAgentMeta = {...} as a SYNCHRONOUS literal at the top
 *      of the bundle, baked from the current profile config.yaml files on disk.
 *      This runs before React mounts, so Z() sees the data on the very first render.
 *
 *   2. Modifies the Z(id) function (which reads agent meta from localStorage) to
 *      fall back to window.__abaiAgentMeta[id] when localStorage has no entry.
 *
 * Safe to re-run — the meta injection section is replaced on each run so data
 * stays current after installs/uninstalls. The Z() patch is idempotent.
 *
 * Called from entrypoint-hermes.sh at container startup, and also invoked by
 * docker_host.py after install_hermes_agent / uninstall_hermes_agent.
 */
const fs = require('fs');
const path = require('path');

const CLIENT_ASSETS = '/app/dist/client/assets';
const PROFILES_DIR = '/opt/data/profiles';

// ── Find the operations bundle ────────────────────────────────────────────────
// There may be multiple operations-*.js files (route loader + main bundle).
// We need the one that contains the Z() function and the localStorage key prefix.

const bundleFile = (() => {
  const candidates = fs.readdirSync(CLIENT_ASSETS)
    .filter(f => f.startsWith('operations-') && f.endsWith('.js'))
    .map(f => path.join(CLIENT_ASSETS, f));
  // Prefer the file that has both Z() and the localStorage prefix — that's the main bundle
  for (const f of candidates) {
    const src = fs.readFileSync(f, 'utf8');
    if (src.includes('function Z(e)') && src.includes('operations:agents:')) return f;
  }
  // Fall back to the largest file
  return candidates.sort((a, b) => fs.statSync(b).size - fs.statSync(a).size)[0];
})();

if (!bundleFile) {
  console.warn('[patch-agent-meta-seeder] operations-*.js not found — skipping');
  process.exit(0);
}

// ── Collect agent meta from profile meta.json files ──────────────────────────
// _rebuild_swarm_yaml() writes a meta.json alongside config.yaml in each
// profile directory. This JSON is trivially parseable (no YAML quoting issues).

const agentMeta = {};

if (fs.existsSync(PROFILES_DIR)) {
  for (const name of fs.readdirSync(PROFILES_DIR)) {
    if (name === 'default') continue;
    const metaPath = path.join(PROFILES_DIR, name, 'meta.json');
    if (!fs.existsSync(metaPath)) continue;
    try {
      const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
      if (meta.systemPrompt) agentMeta[name] = meta;
    } catch (e) {
      console.warn('[patch-agent-meta-seeder] Could not parse meta.json for', name, ':', e.message);
    }
  }
}

// ── Patch the bundle ──────────────────────────────────────────────────────────

let content = fs.readFileSync(bundleFile, 'utf-8');

// Remove previous meta injection (sentinel markers)
const META_START = '/*__abai_meta_start__*/';
const META_END = '/*__abai_meta_end__*/';
const prevStart = content.indexOf(META_START);
const prevEnd   = content.indexOf(META_END);
if (prevStart !== -1 && prevEnd !== -1) {
  content = content.slice(0, prevStart) + content.slice(prevEnd + META_END.length);
}

// Build the synchronous meta injection
const metaJs = `${META_START}window.__abaiAgentMeta=${JSON.stringify(agentMeta)};${META_END}`;

// Inject at start of file (before any imports/code)
content = metaJs + '\n' + content;

// ── Patch Z() to use window.__abaiAgentMeta as fallback ──────────────────────
// Two branches in Z(e) need patching:
//
//   Branch 1 — no localStorage entry (r is null):
//     if(!r)return{emoji:q(e),description:"",systemPrompt:"",color:K(e),...}
//
//   Branch 2 — stale localStorage entry with empty systemPrompt:
//     const s=JSON.parse(r);return{emoji:b(s.emoji)||q(e),...systemPrompt:b(s.systemPrompt),...}
//     b() strips falsy values, so an empty stored systemPrompt stays empty without this patch.
//
// Both branches fall back to window.__abaiAgentMeta[id] when the stored value is missing.

const Z_ORIGINAL = `if(!r)return{emoji:q(e),description:"",systemPrompt:"",color:K(e),createdAt:new Date().toISOString()}`;
const Z_PATCHED  =
  `if(!r){var _s=window.__abaiAgentMeta&&window.__abaiAgentMeta[e];` +
  `return{emoji:_s&&_s.emoji||q(e),description:_s&&_s.description||"",` +
  `systemPrompt:_s&&_s.systemPrompt||"",color:_s&&_s.color||K(e),` +
  `createdAt:_s&&_s.createdAt||new Date().toISOString()}}`;

// Branch 2: existing localStorage entry — merge __abaiAgentMeta when fields are empty.
// Sentinel: "systemPrompt:b(s.systemPrompt),color" uniquely identifies this branch.
const Z2_ORIGINAL =
  `const s=JSON.parse(r);return{emoji:b(s.emoji)||q(e),description:b(s.description),` +
  `systemPrompt:b(s.systemPrompt),color:b(s.color)||K(e),createdAt:b(s.createdAt)||new Date().toISOString()}}` +
  `catch{return{emoji:q(e),description:"",systemPrompt:"",color:K(e),createdAt:new Date().toISOString()}}`;
const Z2_PATCHED =
  `const s=JSON.parse(r);var _sx=window.__abaiAgentMeta&&window.__abaiAgentMeta[e];` +
  `return{emoji:b(s.emoji)||(_sx&&_sx.emoji)||q(e),description:b(s.description)||(_sx&&_sx.description)||"",` +
  `systemPrompt:b(s.systemPrompt)||(_sx&&_sx.systemPrompt)||"",color:b(s.color)||(_sx&&_sx.color)||K(e),` +
  `createdAt:b(s.createdAt)||new Date().toISOString()}}` +
  `catch{var _sy=window.__abaiAgentMeta&&window.__abaiAgentMeta[e];` +
  `return{emoji:_sy&&_sy.emoji||q(e),description:_sy&&_sy.description||"",` +
  `systemPrompt:_sy&&_sy.systemPrompt||"",color:_sy&&_sy.color||K(e),createdAt:new Date().toISOString()}}`;

let z1Patched = false;
if (content.includes(Z_PATCHED)) {
  z1Patched = true;
} else if (content.includes(Z_ORIGINAL)) {
  content = content.replace(Z_ORIGINAL, Z_PATCHED);
  z1Patched = true;
} else {
  console.warn('[patch-agent-meta-seeder] Z() branch-1 target not found — Z() fallback not patched');
}

let z2Patched = false;
if (content.includes(Z2_PATCHED)) {
  z2Patched = true;
} else if (content.includes(Z2_ORIGINAL)) {
  content = content.replace(Z2_ORIGINAL, Z2_PATCHED);
  z2Patched = true;
} else {
  console.warn('[patch-agent-meta-seeder] Z() branch-2 target not found — stale localStorage entries will not be overridden');
}

if (z1Patched || z2Patched) {
  console.log('[patch-agent-meta-seeder] Z() patched (b1=' + z1Patched + ' b2=' + z2Patched + '); updated meta for', Object.keys(agentMeta).length, 'agent(s)');
}

fs.writeFileSync(bundleFile, content);

// ── Patch tH() in main-*.js to show correct agent names for delegated tasks ──
// hermes-workspace's tH(subagentId, goal) matches goal keywords against a
// hardcoded persona list (Max=DevOps, Ada=QA, Luna=Research, etc.).
// "Build a 3-statement model" matches Max because "build" is in his keyword list.
//
// Fix: intercept tH() — if the goal starts with YAML frontmatter (our delegation
// format embeds the .md file as the goal prefix), extract name+emoji from it and
// return a custom persona instead of a random/keyword-matched one.

const mainBundle = (() => {
  const candidates = fs.readdirSync(CLIENT_ASSETS)
    .filter(f => f.startsWith('main-') && f.endsWith('.js'))
    .map(f => path.join(CLIENT_ASSETS, f));
  for (const f of candidates) {
    const src = fs.readFileSync(f, 'utf8');
    if (src.includes('DevOps Specialist') && src.includes('specialties')) return f;
  }
  return null;
})();

if (mainBundle) {
  let mainContent = fs.readFileSync(mainBundle, 'utf-8');

  // Target: the start of tH() — unique because of the uS cache + cS.filter combo
  const TH_ORIGINAL = `function tH(e,t){const n=uS.get(e);if(n)return n;`;
  const TH_PATCHED  =
    `function tH(e,t){const n=uS.get(e);if(n)return n;` +
    // If goal starts with YAML frontmatter (---\nname: ...), extract the name+emoji
    `if(t){var _fm=t.slice(0,600);var _fn=_fm.match(/^---[\\s\\S]*?\\nname:\\s*(.+)/m);` +
    `var _fe=_fm.match(/\\nemoji:\\s*(\\S+)/m);` +
    `if(_fn){var _fp={name:_fn[1].trim(),role:'Specialist',emoji:_fe?_fe[1].trim():'🤖',color:'text-purple-400',specialties:[]};` +
    `uS.set(e,_fp);return _fp}}`;

  const TH_SENTINEL = '/*__abai_th_patched__*/';

  if (mainContent.includes(TH_SENTINEL)) {
    console.log('[patch-agent-meta-seeder] tH() already patched in', path.basename(mainBundle));
  } else if (mainContent.includes(TH_ORIGINAL)) {
    mainContent = mainContent.replace(TH_ORIGINAL, TH_SENTINEL + TH_PATCHED);
    fs.writeFileSync(mainBundle, mainContent);
    console.log('[patch-agent-meta-seeder] Patched tH() in', path.basename(mainBundle), '— delegates now show installed agent names');
  } else {
    console.warn('[patch-agent-meta-seeder] tH() target not found in main bundle — delegate display names unchanged');
  }
} else {
  console.warn('[patch-agent-meta-seeder] main-*.js not found — tH() patch skipped');
}

