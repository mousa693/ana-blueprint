// Zero-dependency server: serves the web app and proxies translation requests to
// the configured model, so API keys never reach the browser.

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { selectProvider, modelFor } from './lib/providers.js';
import { parseModelOutput } from './lib/parse.js';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.join(ROOT, 'public');
const MAX_BODY = 8 * 1024 * 1024;
const MAX_TEXT = 2000;
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
};

function loadDotEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
  }
}

// Simple per-IP limiter so a public deployment can't burn through a free-tier quota.
function rateLimiter(perMinute) {
  const hits = new Map();
  return (ip) => {
    const now = Date.now();
    const recent = (hits.get(ip) || []).filter((t) => now - t < 60_000);
    if (recent.length >= perMinute) return false;
    recent.push(now);
    hits.set(ip, recent);
    if (hits.size > 10_000) hits.clear();
    return true;
  };
}

function send(res, status, body, type = 'application/json') {
  res.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' });
  res.end(type === 'application/json' ? JSON.stringify(body) : body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(Object.assign(new Error('Image too large (max 8 MB)'), { status: 413, expose: true }));
        req.destroy();
      } else chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function parseImage(dataUrl) {
  if (!dataUrl) return null;
  const m = /^data:([\w/+.-]+);base64,([A-Za-z0-9+/=]+)$/.exec(dataUrl);
  if (!m || !IMAGE_TYPES.has(m[1])) {
    throw Object.assign(new Error('Image must be a JPEG, PNG or WebP data URL'), { status: 400, expose: true });
  }
  return { mime: m[1], base64: m[2] };
}

function serveStatic(req, res) {
  const urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = path.normalize(path.join(PUBLIC, urlPath === '/' ? 'index.html' : urlPath));
  if (!file.startsWith(PUBLIC + path.sep)) return send(res, 403, { error: 'Forbidden' });
  fs.readFile(file, (err, data) => {
    if (err) return send(res, 404, { error: 'Not found' });
    res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' });
    res.end(data);
  });
}

export function createServer(env = process.env) {
  const provider = selectProvider(env);
  const allow = rateLimiter(Number(env.RATE_LIMIT_PER_MIN) || 15);

  const server = http.createServer(async (req, res) => {
    try {
      if (req.method === 'GET' && req.url === '/api/config') {
        return send(res, 200, { provider: provider.name, model: modelFor(provider, env) });
      }
      if (req.url === '/api/translate') {
        if (req.method !== 'POST') return send(res, 405, { error: 'Use POST' });
        const ip = req.headers['x-forwarded-for']?.split(',')[0].trim() || req.socket.remoteAddress;
        if (!allow(ip)) return send(res, 429, { error: 'Too many requests — wait a minute and try again.' });

        let body;
        try {
          body = JSON.parse(await readBody(req));
        } catch (e) {
          if (e.expose) throw e;
          return send(res, 400, { error: 'Request body must be JSON' });
        }
        const image = parseImage(body.image);
        const text = typeof body.text === 'string' ? body.text.trim().slice(0, MAX_TEXT) : '';
        const hint = typeof body.hint === 'string' ? body.hint.trim().slice(0, 500) : '';
        if (!image && !text) return send(res, 400, { error: 'Send an image or some text to translate' });

        const raw = await provider.run(env, { image, text, hint });
        let result;
        try {
          result = parseModelOutput(raw);
        } catch {
          return send(res, 502, { error: 'The model replied in an unexpected format. Try again or switch model.' });
        }
        return send(res, 200, { result, provider: provider.name, model: modelFor(provider, env) });
      }
      if (req.method === 'GET') return serveStatic(req, res);
      send(res, 405, { error: 'Method not allowed' });
    } catch (err) {
      // Our own validation errors are safe to show; provider errors may echo keys or internals.
      if (err.expose) return send(res, err.status, { error: err.message });
      console.error(`[${provider.name}]`, err.message);
      if (err.status === 429) {
        return send(res, 429, { error: 'The model provider is rate-limiting us (free-tier quota?). Try again shortly.' });
      }
      send(res, 502, { error: 'Translation failed upstream. Check the server log.' });
    }
  });
  return { server, provider };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  loadDotEnv(path.join(ROOT, '.env'));
  const port = Number(process.env.PORT) || 3000;
  const { server, provider } = createServer(process.env);
  server.listen(port, () => {
    console.log(`Hieroglyph Translator on http://localhost:${port}`);
    console.log(`Model: ${provider.name} (${modelFor(provider, process.env)})`);
    if (provider.name === 'demo') console.log('No API key found — running in demo mode. See .env.example.');
  });
}
