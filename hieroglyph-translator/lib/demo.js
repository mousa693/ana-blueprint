// A real, well-known formula (the ankh–djed–was group seen on countless temple
// walls) so demo mode shows what a genuine result looks like.
export const DEMO_RESULT = {
  detected: true,
  script: 'hieroglyphic',
  reading_direction: 'right-to-left',
  glyphs: [
    { sign: '𓏙', gardiner: 'X8', description: 'conical loaf', phonetic: 'di', role: 'logogram' },
    { sign: '𓋹', gardiner: 'S34', description: 'sandal strap (ankh)', phonetic: 'ꜥnḫ', role: 'logogram' },
    { sign: '𓊽', gardiner: 'R11', description: 'djed pillar', phonetic: 'ḏd', role: 'logogram' },
    { sign: '𓌀', gardiner: 'S40', description: 'was sceptre', phonetic: 'wꜣs', role: 'logogram' },
    { sign: '𓎟', gardiner: 'V30', description: 'wicker basket', phonetic: 'nb', role: 'logogram' },
    { sign: '𓏇', gardiner: 'W19', description: 'milk jug in a net', phonetic: 'mi', role: 'logogram' },
    { sign: '𓇳', gardiner: 'N5', description: 'sun disk', phonetic: 'rꜥ', role: 'logogram' },
  ],
  transliteration: 'di ꜥnḫ ḏd wꜣs nb mi rꜥ',
  literal_gloss: 'given | life | stability | dominion | all | like | Ra',
  translation: 'Given all life, stability and dominion, like Ra.',
  context:
    'A standard royal epithet that follows a king’s name on temple walls and monuments. ' +
    '(Demo mode — this is a sample result, not a reading of your photo. Add an API key to translate real images.)',
  confidence: 0.9,
  notes: 'Demo mode is on because no model API key is configured. See README for free options.',
};
