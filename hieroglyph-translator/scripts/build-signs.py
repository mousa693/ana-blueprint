"""Regenerates public/signs.js: a small offline Gardiner sign list with Unicode glyphs.

Codepoints are looked up from Unicode names so they can't drift from the standard.
Run: python3 scripts/build-signs.py
"""
import json, re, unicodedata, pathlib

# (gardiner, description, transliteration/phonetic value, meaning/use, category)
SIGNS = [
    # Uniliteral ("alphabet") signs
    ("G1", "Egyptian vulture", "ꜣ", "Glottal sound (aleph); uniliteral", "uniliteral"),
    ("M17", "Flowering reed", "i", "Uniliteral i / y", "uniliteral"),
    ("Z4", "Two diagonal strokes", "y", "Uniliteral y; also dual marker", "uniliteral"),
    ("D36", "Forearm", "ꜥ", "Uniliteral ayin", "uniliteral"),
    ("G43", "Quail chick", "w", "Uniliteral w; plural ending", "uniliteral"),
    ("Z7", "Coil (hieratic quail chick)", "w", "Uniliteral w", "uniliteral"),
    ("D58", "Foot", "b", "Uniliteral b", "uniliteral"),
    ("Q3", "Stool", "p", "Uniliteral p", "uniliteral"),
    ("I9", "Horned viper", "f", "Uniliteral f; 'he/his' (3rd person masc. suffix)", "uniliteral"),
    ("G17", "Owl", "m", "Uniliteral m; preposition 'in, with, as'", "uniliteral"),
    ("N35", "Ripple of water", "n", "Uniliteral n; preposition 'to, for'; genitive 'of'", "uniliteral"),
    ("D21", "Mouth", "r", "Uniliteral r; preposition 'to, at, against'", "uniliteral"),
    ("O4", "Reed shelter", "h", "Uniliteral h", "uniliteral"),
    ("V28", "Twisted flax wick", "ḥ", "Uniliteral ḥ", "uniliteral"),
    ("Aa1", "Placenta (?)", "ḫ", "Uniliteral ḫ (kh)", "uniliteral"),
    ("F32", "Animal belly with udder", "ẖ", "Uniliteral ẖ", "uniliteral"),
    ("O34", "Door bolt", "z / s", "Uniliteral z (later s)", "uniliteral"),
    ("S29", "Folded cloth", "s", "Uniliteral s", "uniliteral"),
    ("N37", "Pool", "š", "Uniliteral š (sh)", "uniliteral"),
    ("N29", "Sandy slope", "q", "Uniliteral q (ḳ)", "uniliteral"),
    ("V31", "Basket with handle", "k", "Uniliteral k; 'you/your' (2nd person masc. suffix)", "uniliteral"),
    ("W11", "Jar stand", "g", "Uniliteral g", "uniliteral"),
    ("X1", "Bread loaf", "t", "Uniliteral t; feminine ending", "uniliteral"),
    ("V13", "Tethering rope", "ṯ", "Uniliteral ṯ (tj)", "uniliteral"),
    ("D46", "Hand", "d", "Uniliteral d", "uniliteral"),
    ("I10", "Cobra", "ḏ", "Uniliteral ḏ (dj)", "uniliteral"),
    ("E23", "Recumbent lion", "rw / l", "Biliteral rw; used for l in foreign names", "uniliteral"),
    # Common words, logograms and multiliterals
    ("S34", "Sandal strap (ankh)", "ꜥnḫ", "Life; to live", "word"),
    ("R11", "Djed pillar", "ḏd", "Stability, endurance", "word"),
    ("S40", "Was sceptre", "wꜣs", "Dominion, power", "word"),
    ("S38", "Crook", "ḥqꜣ", "Ruler; to rule", "word"),
    ("R8", "Cloth on a pole", "nṯr", "God", "word"),
    ("R4", "Loaf on a reed mat", "ḥtp", "Offering; peace, contentment", "word"),
    ("F35", "Heart and windpipe", "nfr", "Good, beautiful, perfect", "word"),
    ("F34", "Heart", "ib", "Heart, mind", "word"),
    ("G36", "Swallow", "wr", "Great, much", "word"),
    ("G39", "Pintail duck", "zꜣ", "Son", "word"),
    ("G14", "Vulture", "mwt", "Mother", "word"),
    ("G25", "Crested ibis", "ꜣḫ", "Spirit; effective, beneficial", "word"),
    ("G5", "Falcon", "ḥr", "Horus", "word"),
    ("L1", "Scarab beetle", "ḫpr", "To become, come into being; Khepri", "word"),
    ("L2", "Bee", "bit", "King of Lower Egypt (in nswt-bity)", "word"),
    ("M23", "Sedge plant", "sw", "King of Upper Egypt (in nswt)", "word"),
    ("V30", "Wicker basket", "nb", "Lord, master; all, every", "word"),
    ("D4", "Eye", "ir", "To do, make", "word"),
    ("D10", "Wedjat eye", "wḏꜣt", "Eye of Horus; wholeness, protection", "word"),
    ("D1", "Head in profile", "tp", "Head; upon, first", "word"),
    ("D2", "Face", "ḥr", "Face; preposition 'upon'", "word"),
    ("D28", "Raised arms", "kꜣ", "Ka (life force, spirit)", "word"),
    ("D35", "Arms in gesture of negation", "n", "Negation 'not'", "word"),
    ("X8", "Conical loaf", "di / rdi", "To give", "word"),
    ("F31", "Three fox skins", "ms", "To give birth, be born", "word"),
    ("H6", "Feather", "šw / mꜣꜥt", "Maat (truth, order); Shu", "word"),
    ("Q1", "Seat / throne", "st / ꜣst", "Seat, place; Isis", "word"),
    ("N5", "Sun disk", "rꜥ", "Sun; Ra; day (determinative of time)", "word"),
    ("N1", "Sky", "pt", "Sky, heaven", "word"),
    ("N14", "Star", "sbꜣ / dwꜣ", "Star; to worship; morning", "word"),
    ("N16", "Flat land with grains", "tꜣ", "Land, earth", "word"),
    ("O1", "House plan", "pr", "House; to go out", "word"),
    ("O28", "Column", "iwn", "Pillar; Heliopolis (Iunu)", "word"),
    ("M8", "Pool with lotus", "šꜣ", "Field, marsh", "word"),
    ("Y5", "Senet game board", "mn", "Established, enduring (Amun = imn)", "word"),
    ("W24", "Pot", "nw", "Biliteral nw", "word"),
    ("V4", "Lasso", "wꜣ", "Biliteral wꜣ", "word"),
    ("G4", "Long-legged buzzard", "tyw", "Triliteral tyw; plural ending", "word"),
    # Determinatives and markers
    ("A1", "Seated man", "—", "Determinative: man, person; 'I/me' (1st person)", "determinative"),
    ("B1", "Seated woman", "—", "Determinative: woman", "determinative"),
    ("A2", "Man with hand to mouth", "—", "Determinative: eat, drink, speak, think, feel", "determinative"),
    ("A40", "Seated god", "—", "Determinative: god, king", "determinative"),
    ("G7", "Falcon on standard", "—", "Determinative: god, divine", "determinative"),
    ("D54", "Walking legs", "—", "Determinative: motion", "determinative"),
    ("N25", "Hill country", "ḫꜣst", "Determinative: foreign land, desert", "determinative"),
    ("O49", "Village crossroads", "niwt", "Determinative: town, city", "determinative"),
    ("T14", "Throw stick", "—", "Determinative: foreigner, foreign", "determinative"),
    ("Y1", "Papyrus roll", "—", "Determinative: abstract ideas, writing", "determinative"),
    ("V10", "Cartouche", "šnw", "Encloses a royal name", "determinative"),
    ("Z1", "Single stroke", "—", "Marks a sign as a word-sign (ideogram); 'one'", "determinative"),
    ("Z2", "Three strokes", "—", "Plural marker", "determinative"),
]

def to_unicode_name(code):
    m = re.fullmatch(r"([A-Z][a-z]?)(\d+)", code)
    letters, num = m.groups()
    return f"EGYPTIAN HIEROGLYPH {letters.upper()}{int(num):03d}"

out = []
for code, desc, translit, meaning, cat in SIGNS:
    glyph = unicodedata.lookup(to_unicode_name(code))
    out.append({"gardiner": code, "glyph": glyph, "description": desc,
                "translit": translit, "meaning": meaning, "category": cat})

dest = pathlib.Path(__file__).resolve().parent.parent / "public" / "signs.js"
dest.write_text(
    "// Generated by scripts/build-signs.py — edit the list there, not here.\n"
    "window.SIGNS = " + json.dumps(out, ensure_ascii=False, indent=1) + ";\n",
    encoding="utf-8",
)
print(f"wrote {len(out)} signs to {dest}")
