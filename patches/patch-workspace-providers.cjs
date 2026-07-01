#!/usr/bin/env node
/**
 * Inject AutoBricks AI into all hermes-workspace provider lists.
 * Runs once at container startup (entrypoint-hermes.sh).
 * Safe to re-run — guards against double-patching via "abai" check.
 */
const fs = require('fs');
const path = require('path');

const ASSETS_DIR = '/app/dist/server/assets';
const PROVIDERS_DIR = '/app/dist/client/providers';

const routerFile = fs.readdirSync(ASSETS_DIR)
  .filter(f => f.startsWith('router-') && f.endsWith('.js'))
  .map(f => path.join(ASSETS_DIR, f))[0];

if (!routerFile) {
  console.warn('[patch-providers] router-*.js not found — skipping');
  process.exit(0);
}

let content = fs.readFileSync(routerFile, 'utf-8');

if (content.includes('"abai"')) {
  console.log('[patch-providers] Already patched — skipping');
  process.exit(0);
}

// Logo: copy openai.png as stand-in for autobricksai.png
try {
  const dest = path.join(PROVIDERS_DIR, 'autobricksai.png');
  if (!fs.existsSync(dest))
    fs.copyFileSync(path.join(PROVIDERS_DIR, 'openai.png'), dest);
} catch (e) {
  console.warn('[patch-providers] Logo copy failed:', e.message);
}

let patched = 0;

// 1. Settings panel list (has id/name/logo/models/authType/envKey)
{
  const marker = '  { id: "custom", name: "Custom", logo: "", models: [], authType: "api_key" }\n];';
  const entry =
    '  {\n' +
    '    id: "abai",\n' +
    '    name: "AutoBricks AI",\n' +
    '    logo: "/providers/autobricksai.png",\n' +
    '    models: ["autobricksai/claude-sonnet-4.6","autobricksai/gemini-2.5-flash","autobricksai/gpt-4.1","autobricksai/deepseek-r1","autobricksai/qwen3-235b"],\n' +
    '    authType: "api_key",\n' +
    '    envKey: "AUTOBRICKS_API_KEY"\n' +
    '  },\n';
  if (content.includes(marker)) {
    content = content.replace(marker, entry + marker);
    patched++;
    console.log('[patch-providers] Patched settings list');
  } else {
    console.warn('[patch-providers] settings list marker not found');
  }
}

// 2. PROVIDERS$1 — onboarding/wizard list (id/name/logo/desc/authType/envKey)
{
  const marker = '    id: "custom",\n    name: "Custom (OpenAI-compat)",';
  const entry =
    '    id: "abai",\n' +
    '    name: "AutoBricks AI",\n' +
    '    logo: "/providers/autobricksai.png",\n' +
    '    desc: "AutoBricks AI platform — API key required",\n' +
    '    authType: "api_key",\n' +
    '    envKey: "AUTOBRICKS_API_KEY"\n' +
    '  },\n  {\n';
  if (content.includes(marker)) {
    content = content.replace(marker, entry + marker);
    patched++;
    console.log('[patch-providers] Patched PROVIDERS$1 (onboarding list)');
  } else {
    console.warn('[patch-providers] PROVIDERS$1 marker not found');
  }
}

// 3. PROVIDERS — backend config list (id/name/authType/envKeys)
{
  const marker = '  {\n    id: "custom",\n    name: "Custom OpenAI-compatible",\n    authType: "api_key",\n    envKeys: []\n  }\n];';
  const entry =
    '  {\n    id: "abai",\n    name: "AutoBricks AI",\n    authType: "api_key",\n    envKeys: ["AUTOBRICKS_API_KEY"]\n  },\n';
  if (content.includes(marker)) {
    content = content.replace(marker, entry + marker);
    patched++;
    console.log('[patch-providers] Patched PROVIDERS (backend list)');
  } else {
    console.warn('[patch-providers] PROVIDERS backend marker not found');
  }
}

if (patched > 0) {
  fs.writeFileSync(routerFile, content);
  console.log(`[patch-providers] Done (${patched}/3 lists patched).`);
} else {
  console.warn('[patch-providers] No lists patched — markers may have changed.');
}
