// Models don't always return clean JSON even when asked; this turns whatever came
// back into the fixed shape the frontend renders.

const SCRIPTS = new Set(['hieroglyphic', 'hieratic', 'demotic', 'coptic', 'other', 'none']);
const DIRECTIONS = new Set(['right-to-left', 'left-to-right', 'top-to-bottom', 'unknown']);
const ROLES = new Set(['phonogram', 'logogram', 'determinative', 'uncertain']);

export function extractJson(raw) {
  if (typeof raw !== 'string') throw new Error('Model returned no text');
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const body = fenced ? fenced[1] : raw;
  const start = body.indexOf('{');
  const end = body.lastIndexOf('}');
  if (start === -1 || end <= start) throw new Error('Model response contained no JSON object');
  return JSON.parse(body.slice(start, end + 1));
}

const str = (v) => (typeof v === 'string' ? v.trim() : v == null ? '' : String(v));
const pick = (v, allowed, fallback) => (allowed.has(str(v).toLowerCase()) ? str(v).toLowerCase() : fallback);

export function normalize(obj) {
  const o = obj && typeof obj === 'object' ? obj : {};
  const glyphs = Array.isArray(o.glyphs) ? o.glyphs.slice(0, 40) : [];
  let confidence = Number(o.confidence);
  if (!Number.isFinite(confidence)) confidence = 0;
  if (confidence > 1 && confidence <= 100) confidence /= 100; // some models answer in percent
  return {
    detected: o.detected !== false,
    script: pick(o.script, SCRIPTS, 'other'),
    reading_direction: pick(o.reading_direction, DIRECTIONS, 'unknown'),
    glyphs: glyphs.map((g) => ({
      sign: str(g?.sign),
      gardiner: str(g?.gardiner),
      description: str(g?.description),
      phonetic: str(g?.phonetic),
      role: pick(g?.role, ROLES, 'uncertain'),
    })),
    transliteration: str(o.transliteration),
    literal_gloss: str(o.literal_gloss),
    translation: str(o.translation),
    context: str(o.context),
    confidence: Math.min(1, Math.max(0, confidence)),
    notes: str(o.notes),
  };
}

export function parseModelOutput(raw) {
  return normalize(extractJson(raw));
}
