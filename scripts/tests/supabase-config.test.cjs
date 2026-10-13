// Run with the historical frontend's TypeScript dev dependency installed.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../../client/frontend-next/lib/supabase.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function load(env, fetch) {
  const context = { exports: {}, process: { env }, fetch, atob };
  vm.runInNewContext(code, context);
  return context.exports;
}

test('unconfigured offline frontend has no project fallback', async () => {
  const api = load({}, async () => { throw new Error('offline'); });
  const result = await api.getSupabaseConfig();
  assert.equal(result.enabled, false);
  assert.equal(result.url, '');
  assert.equal(result.publishable_key, '');
});

test('backend configuration takes precedence over deployment fallback', async () => {
  const backend = { enabled: true, url: 'https://backend.example.invalid', publishable_key: 'example-key' };
  const api = load({ NEXT_PUBLIC_SUPABASE_URL: 'https://fallback.example.invalid', NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: 'example' }, async () => ({ ok: true, json: async () => backend }));
  assert.equal((await api.getSupabaseConfig()).url, backend.url);
});

test('deployment fallback works without backend', async () => {
  const api = load({ NEXT_PUBLIC_SUPABASE_URL: 'https://example.invalid', NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: 'example' }, async () => ({ ok: false }));
  const result = await api.getSupabaseConfig();
  assert.equal(result.enabled, true);
  assert.equal(result.url, 'https://example.invalid');
});

test('missing configuration does not send a user token to relative REST routes', async () => {
  const calls = [];
  const api = load({}, async (...args) => { calls.push(args); return { ok: false }; });
  const token = 'header.' + Buffer.from(JSON.stringify({ sub: 'example-user' })).toString('base64url') + '.signature';
  assert.equal(await api.getMyProfile(token), null);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/auth/supabase-config');
});
