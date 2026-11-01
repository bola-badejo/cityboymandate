"""Lightweight language identification for Nigerian posts: English, Nigerian Pidgin (pcm), Yoruba (yo),
Hausa (ha), Igbo (ig). Marker-word scoring only. These lexicons are STARTER lists written without a native-speaker
review: they route posts to people who read the language, they do not make decisions. A language only counts as
supported after native speakers evaluate it and an admin adds it to `evaluated_languages`."""
from __future__ import annotations
from dataclasses import dataclass
from cityboymandate.monitor.keywords import norm

LANG_NAMES = {"en": "English", "pcm": "Nigerian Pidgin", "yo": "Yoruba", "ha": "Hausa", "ig": "Igbo", "und": "an unidentified language"}
EN = set("the is of and to that this in for on with are was has have by it at from be not as you they will been their".split())

# (marker, weight). Multi-word markers are matched on word boundaries. Ambiguous short words get weight 1.
MARKERS = {
    "pcm": [("dey", 2), ("wetin", 2), ("abeg", 2), ("una", 2), ("wahala", 2), ("sef", 2), ("wey", 2), ("comot", 2), ("sabi", 2),
            ("pikin", 2), ("tori", 2), ("no be", 2), ("make we", 2), ("e don", 2), ("e go", 2), ("na so", 2), ("na im", 2),
            ("how far", 2), ("talk say", 2), ("tok say", 2), ("dem don", 2), ("na", 1), ("dem", 1), ("dis", 1), ("dat", 1),
            ("oga", 1), ("gist", 1), ("fit", 1), ("don", 1)],
    "yo": [("ijoba", 2), ("awon", 2), ("nitori", 2), ("oloselu", 2), ("emilokan", 2), ("gbogbo", 1), ("omo", 1), ("kii", 1),
           ("wipe", 1), ("ibo", 1), ("ko ni", 2), ("won", 1), ("ati", 1)],
    "ha": [("gwamnati", 2), ("zabe", 2), ("shugaban", 2), ("cewa", 2), ("wannan", 2), ("kuma", 2), ("saboda", 2), ("ya ce", 2),
           ("ta ce", 2), ("sun ce", 2), ("kasar", 2), ("yan", 1), ("da", 1), ("ne", 1), ("ba", 1)],
    "ig": [("ndi", 2), ("nke", 2), ("maka", 2), ("anyi", 2), ("ntuli aka", 2), ("goomenti", 2), ("onye isi", 2), ("obodo", 2),
           ("kwa", 1), ("ihe", 1), ("bu", 1), ("ka", 1)],
}
MIN_SCORE = {"pcm": 2, "yo": 3, "ha": 3, "ig": 3}
DIACRITIC_HINTS = {"yo": "\u1eb9\u1e63\u0144", "ig": "\u1ee5\u1ecb\u1e45"}   # ẹ ṣ ń   |   ụ ị ṅ


@dataclass
class Detection:
    lang: str
    confidence: float
    markers: list[str]


def detect(text: str) -> Detection:
    raw = text.lower()
    padded = " " + "".join(c if (c.isalnum() or c == " ") else " " for c in norm(text)) + " "
    words = padded.split()
    scores, hits = {}, {}
    for lang, ms in MARKERS.items():
        h = [(m, w) for m, w in ms if f" {m} " in padded]
        scores[lang] = sum(w for _, w in h)
        hits[lang] = [m for m, _ in h]
        scores[lang] += 2 * sum(raw.count(ch) > 0 for ch in DIACRITIC_HINTS.get(lang, ""))
    if "\u1ecd" in raw:                                   # ọ is shared by Yoruba and Igbo
        scores["yo"] += 1
        scores["ig"] += 1
    en = sum(1 for w in words if w in EN)
    best = max(scores, key=scores.get)
    if scores[best] >= MIN_SCORE[best] and scores[best] > en:
        return Detection(best, round(min(1.0, scores[best] / 6), 2), hits[best][:5])
    if en >= 1:
        return Detection("en", round(min(1.0, en / 3), 2), [])
    return Detection("und" if len(words) < 4 else "en", 0.3, [])
