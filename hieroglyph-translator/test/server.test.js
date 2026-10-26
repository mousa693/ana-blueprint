import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from '../server.js';
import { selectProvider } from '../lib/providers.js';

let server, base;
const TINY_PNG =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

before(async () => {
  ({ server } = createServer({ PROVIDER: 'demo', RATE_LIMIT_PER_MIN: '5' }));
  await new Promise((r) => server.listen(0, r));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => server.close());

const post = (body) =>
  fetch(`${base}/api/translate`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

test('serves the app', async () => {
  const res = await fetch(base + '/');
  assert.equal(res.status, 200);
  assert.match(await res.text(), /Hieroglyph Translator/);
});

test('blocks path traversal', async () => {
  const res = await fetch(base + '/..%2fserver.js');
  assert.ok([403, 404].includes(res.status));
});

test('reports config', async () => {
  assert.deepEqual(await (await fetch(base + '/api/config')).json(), { provider: 'demo', model: 'canned example' });
});

test('translates an image in demo mode', async () => {
  const res = await post({ image: TINY_PNG });
  assert.equal(res.status, 200);
  const { result } = await res.json();
  assert.equal(result.detected, true);
  assert.match(result.translation, /life/i);
});

test('rejects empty and bad requests', async () => {
  assert.equal((await post({})).status, 400);
  assert.equal((await post({ image: 'data:text/html;base64,PGh0bWw+' })).status, 400);
});

test('rate limits per client', async () => {
  let last;
  for (let i = 0; i < 6; i++) last = await post({ text: '𓋹' });
  assert.equal(last.status, 429);
});

test('provider auto-selection', () => {
  assert.equal(selectProvider({}).name, 'demo');
  assert.equal(selectProvider({ GEMINI_API_KEY: 'k' }).name, 'gemini');
  assert.equal(selectProvider({ OPENROUTER_API_KEY: 'k' }).name, 'openrouter');
  assert.throws(() => selectProvider({ PROVIDER: 'anthropic' }), /ANTHROPIC_API_KEY/);
});
