import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractJson, normalize, parseModelOutput } from '../lib/parse.js';

test('extracts JSON wrapped in markdown fences and prose', () => {
  const raw = 'Here you go:\n```json\n{"translation":"life","confidence":0.8}\n```\nHope that helps';
  assert.deepEqual(extractJson(raw), { translation: 'life', confidence: 0.8 });
});

test('throws when there is no JSON object', () => {
  assert.throws(() => extractJson('I cannot read this image.'), /no JSON/);
});

test('normalizes missing and malformed fields to safe defaults', () => {
  const r = normalize({ glyphs: [{ sign: '𓋹', role: 'LOGOGRAM' }, null], script: 'klingon', confidence: 'x' });
  assert.equal(r.detected, true);
  assert.equal(r.script, 'other');
  assert.equal(r.reading_direction, 'unknown');
  assert.equal(r.confidence, 0);
  assert.equal(r.glyphs[0].role, 'logogram');
  assert.equal(r.glyphs[1].role, 'uncertain');
  assert.equal(r.translation, '');
});

test('converts percentage confidence and clamps', () => {
  assert.equal(normalize({ confidence: 85 }).confidence, 0.85);
  assert.equal(normalize({ confidence: -2 }).confidence, 0);
});

test('respects detected=false', () => {
  assert.equal(parseModelOutput('{"detected": false, "notes": "a cat"}').detected, false);
});

test('caps glyph list length', () => {
  const glyphs = Array.from({ length: 100 }, () => ({ sign: '𓀀' }));
  assert.equal(normalize({ glyphs }).glyphs.length, 40);
});
