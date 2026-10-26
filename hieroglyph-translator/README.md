# Hieroglyph Translator 𓂀

Google-Translate-style app for ancient Egyptian: point your phone at an inscription, get the signs, transliteration and an English translation.

- **Scan** with the live camera or upload a photo (auto-downscaled to keep requests cheap)
- **Type** hieroglyphs with a tap-to-insert sign keyboard, or paste transliteration / Gardiner codes
- **Signs**: offline, searchable list of ~80 common signs (alphabet, key words, determinatives)
- **History** saved on the device
- Each result shows: translation, confidence, transliteration, word-by-word gloss, every sign with its Gardiner code, reading direction and notes on uncertain readings

No dependencies, just Node 18+.

## Run it

```bash
cd hieroglyph-translator
cp .env.example .env      # add ONE key (see below), or leave empty for demo mode
npm start                 # http://localhost:3000
```

On a phone, open the app over HTTPS (browsers only allow camera access on HTTPS or localhost), e.g. by deploying it or using a tunnel like `cloudflared tunnel --url http://localhost:3000`. It can be added to the home screen.

## Picking a model (cheap / free)

The server picks the first key it finds, or set `PROVIDER=` to force one. Keys stay on the server; the browser never sees them.

| Provider | Cost | Setup |
|---|---|---|
| **Google Gemini** (recommended) | Free tier | Key from https://aistudio.google.com/apikey → `GEMINI_API_KEY` |
| OpenRouter | Some free models (`:free` ids), others cheap | `OPENROUTER_API_KEY`, choose `OPENROUTER_MODEL` |
| Ollama | Free, runs locally | `ollama pull qwen2.5vl`, then `PROVIDER=ollama` |
| Anthropic Claude | Paid, cheap with Haiku | `ANTHROPIC_API_KEY` |
| Any OpenAI-compatible API | varies | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` |
| Demo | Free | No key: returns a sample result so you can try the UI |

Free tiers may use your data for training and have daily quotas. The server rate-limits each client (`RATE_LIMIT_PER_MIN`, default 15) so a public link can't drain your quota.

## How it works

`public/` is a plain HTML/JS app. It posts `{ image | text, hint }` to `POST /api/translate`. `server.js` sends that to the model with an Egyptologist prompt (`lib/prompt.js`) asking for a fixed JSON shape, and `lib/parse.js` cleans up whatever comes back before the UI renders it.

To add or fix signs in the offline list, edit `scripts/build-signs.py` and run `python3 scripts/build-signs.py` (Unicode codepoints are looked up by name, so they're always right).

## Limitations

General vision models are **not** reliable epigraphers. They do well on well-known formulas (offering formulas, royal names in cartouches, *ꜥnḫ ḏd wꜣs*) and clear photos. They struggle with eroded relief, long texts, hieratic and demotic. Use the confidence score and the sign-by-sign breakdown to check the reading. For better accuracy later, a dedicated sign-detection model trained on Gardiner-labelled datasets could run before the language model.

## Tests

```bash
npm test
```
