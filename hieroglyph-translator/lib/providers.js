// One adapter per model provider. Each takes { image, text, hint } (image is
// { mime, base64 } or null) and resolves to the model's raw text reply.

import { SYSTEM_PROMPT, userPrompt } from './prompt.js';
import { DEMO_RESULT } from './demo.js';

const TIMEOUT_MS = 90_000;

async function postJson(url, headers, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const text = await res.text();
  if (!res.ok) {
    const err = new Error(`Provider returned ${res.status}: ${text.slice(0, 300)}`);
    err.status = res.status;
    throw err;
  }
  return JSON.parse(text);
}

const gemini = {
  name: 'gemini',
  defaultModel: 'gemini-2.5-flash',
  keyVar: 'GEMINI_API_KEY',
  async run(env, req) {
    const model = env.GEMINI_MODEL || this.defaultModel;
    const parts = [{ text: userPrompt({ hasImage: !!req.image, ...req }) }];
    if (req.image) parts.push({ inline_data: { mime_type: req.image.mime, data: req.image.base64 } });
    const data = await postJson(
      `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`,
      { 'x-goog-api-key': env.GEMINI_API_KEY },
      {
        system_instruction: { parts: [{ text: SYSTEM_PROMPT }] },
        contents: [{ role: 'user', parts }],
        generationConfig: { temperature: 0.2, responseMimeType: 'application/json' },
      },
    );
    const text = data.candidates?.[0]?.content?.parts?.map((p) => p.text || '').join('');
    if (!text) throw new Error(`Gemini returned no text (${data.promptFeedback?.blockReason || data.candidates?.[0]?.finishReason || 'unknown reason'})`);
    return text;
  },
};

// OpenAI-compatible chat completions: OpenRouter, Groq, Together, OpenAI itself, etc.
function openAiCompatible({ name, url, keyVar, modelVar, defaultModel, extraHeaders = {} }) {
  return {
    name,
    keyVar,
    defaultModel,
    async run(env, req) {
      const content = [{ type: 'text', text: userPrompt({ hasImage: !!req.image, ...req }) }];
      if (req.image) {
        content.push({ type: 'image_url', image_url: { url: `data:${req.image.mime};base64,${req.image.base64}` } });
      }
      const data = await postJson(
        env[url.envVar] || url.default,
        { authorization: `Bearer ${env[keyVar]}`, ...extraHeaders },
        {
          model: env[modelVar] || defaultModel,
          temperature: 0.2,
          messages: [
            { role: 'system', content: SYSTEM_PROMPT },
            { role: 'user', content },
          ],
        },
      );
      const text = data.choices?.[0]?.message?.content;
      if (!text) throw new Error(`${name} returned no text`);
      return text;
    },
  };
}

const openrouter = openAiCompatible({
  name: 'openrouter',
  url: { envVar: 'OPENROUTER_URL', default: 'https://openrouter.ai/api/v1/chat/completions' },
  keyVar: 'OPENROUTER_API_KEY',
  modelVar: 'OPENROUTER_MODEL',
  defaultModel: 'google/gemini-2.5-flash',
  extraHeaders: { 'x-title': 'Hieroglyph Translator' },
});

const openai = openAiCompatible({
  name: 'openai',
  url: { envVar: 'OPENAI_BASE_URL', default: 'https://api.openai.com/v1/chat/completions' },
  keyVar: 'OPENAI_API_KEY',
  modelVar: 'OPENAI_MODEL',
  defaultModel: 'gpt-4o-mini',
});

const anthropic = {
  name: 'anthropic',
  defaultModel: 'claude-haiku-5-5',
  keyVar: 'ANTHROPIC_API_KEY',
  async run(env, req) {
    const content = [];
    if (req.image) {
      content.push({ type: 'image', source: { type: 'base64', media_type: req.image.mime, data: req.image.base64 } });
    }
    content.push({ type: 'text', text: userPrompt({ hasImage: !!req.image, ...req }) });
    const data = await postJson(
      'https://api.anthropic.com/v1/messages',
      { 'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01' },
      {
        model: env.ANTHROPIC_MODEL || this.defaultModel,
        max_tokens: 4096,
        system: SYSTEM_PROMPT,
        messages: [{ role: 'user', content }],
      },
    );
    const text = data.content?.filter((b) => b.type === 'text').map((b) => b.text).join('');
    if (!text) throw new Error('Anthropic returned no text');
    return text;
  },
};

// Fully local and free: https://ollama.com — e.g. `ollama pull qwen2.5vl`.
const ollama = {
  name: 'ollama',
  defaultModel: 'qwen2.5vl',
  keyVar: null,
  async run(env, req) {
    const data = await postJson(
      `${env.OLLAMA_URL || 'http://localhost:11434'}/api/chat`,
      {},
      {
        model: env.OLLAMA_MODEL || this.defaultModel,
        stream: false,
        format: 'json',
        options: { temperature: 0.2 },
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          {
            role: 'user',
            content: userPrompt({ hasImage: !!req.image, ...req }),
            ...(req.image ? { images: [req.image.base64] } : {}),
          },
        ],
      },
    );
    return data.message?.content;
  },
};

// No key needed: returns a canned reading so the UI can be tried end to end.
const demo = {
  name: 'demo',
  defaultModel: 'canned example',
  keyVar: null,
  async run() {
    return JSON.stringify(DEMO_RESULT);
  },
};

export const PROVIDERS = { gemini, openrouter, openai, anthropic, ollama, demo };

// PROVIDER wins if set; otherwise use the first provider whose key is present.
export function selectProvider(env) {
  const wanted = (env.PROVIDER || '').toLowerCase();
  if (wanted) {
    const p = PROVIDERS[wanted];
    if (!p) throw new Error(`Unknown PROVIDER "${env.PROVIDER}". Options: ${Object.keys(PROVIDERS).join(', ')}`);
    if (p.keyVar && !env[p.keyVar]) throw new Error(`PROVIDER=${wanted} needs ${p.keyVar} to be set`);
    return p;
  }
  for (const p of [gemini, openrouter, anthropic, openai]) {
    if (env[p.keyVar]) return p;
  }
  return demo;
}

export function modelFor(provider, env) {
  const vars = { gemini: 'GEMINI_MODEL', openrouter: 'OPENROUTER_MODEL', openai: 'OPENAI_MODEL', anthropic: 'ANTHROPIC_MODEL', ollama: 'OLLAMA_MODEL' };
  return env[vars[provider.name]] || provider.defaultModel;
}
