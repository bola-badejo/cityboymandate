"""Extract dates and numbers from text and decide whether a claim and a document sentence are talking about the
same thing. Deliberately conservative: it only reports a conflict when the same predicate (e.g. 'registration closes')
has exactly one candidate value in the document. Ambiguity means silence, and silence means UNVERIFIED."""
from __future__ import annotations
import re
from dataclasses import dataclass

MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
          "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10,
          "november": 11, "nov": 11, "december": 12, "dec": 12}
_M = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE1 = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_M})\b\.?(?:,?\s+(\d{{4}}))?", re.I)
DATE2 = re.compile(rf"\b({_M})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)
DATE3 = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")                       # day/month/year (Nigerian order)
NUM = re.compile(r"(?P<cur>\u20a6|NGN\s?|N(?=\d))?(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?"
                 r"(?P<scale>trillion|billion|million|thousand|tn|bn|m|k)?\b\s?(?P<pct>%|per\s?cent|percent)?(?P<naira>\s?naira)?", re.I)
SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "trillion": 1e12, "tn": 1e12}
STOP = set("a an the of in on at to for and or is are was were be been by with from as that this it its into than then their there which "
           "who whom will would shall should has have had not no according commission said says say".split())
GROUPS = {"close": {"close", "closes", "closed", "closing", "deadline", "ends", "end", "ending", "expire", "expires", "last"},
          "open": {"open", "opens", "opened", "opening", "begins", "begin", "starts", "start", "commences", "commence"},
          "poll": {"election", "elections", "poll", "polls", "vote", "voting", "held", "holds", "hold", "conduct", "conducted"},
          "sign": {"signed", "sign", "signs", "assented", "assent"},
          "announce": {"announced", "announce", "announces", "declared", "declare"},
          "schedule": {"date", "scheduled", "slated", "fixed"}}
G_OF = {t: g for g, ts in GROUPS.items() for t in ts}


@dataclass
class Slot:
    kind: str          # date | num
    value: tuple
    start: int
    end: int


def stem(t: str) -> str:
    return t[:-1] if t.endswith("s") and len(t) > 3 else t


def toks(s: str) -> list[str]:
    return re.findall(r"[a-z]+", s.lower())


def extract(text: str) -> list[Slot]:
    out: list[Slot] = []
    for m in DATE1.finditer(text):
        d, mo, y = int(m.group(1)), MONTHS[m.group(2).lower()], m.group(3)
        if 1 <= d <= 31:
            out.append(Slot("date", (mo, d, int(y) if y else None), m.start(), m.end()))
    for m in DATE2.finditer(text):
        d, mo, y = int(m.group(2)), MONTHS[m.group(1).lower()], m.group(3)
        if 1 <= d <= 31:
            out.append(Slot("date", (mo, d, int(y) if y else None), m.start(), m.end()))
    for m in DATE3.finditer(text):
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            out.append(Slot("date", (mo, d, int(m.group(3))), m.start(), m.end()))
    out.sort(key=lambda s: (s.start, -(s.end - s.start)))
    dates, last = [], -1
    for s in out:                                            # drop overlapping date matches
        if s.start >= last:
            dates.append(s)
            last = s.end
    nums = []
    for m in NUM.finditer(text):
        if any(d.start <= m.start() < d.end for d in dates):
            continue
        v = float(m.group("n").replace(",", ""))
        scale = (m.group("scale") or "").lower()
        v *= SCALE.get(scale, 1)
        unit = "pct" if m.group("pct") else "naira" if (m.group("cur") or m.group("naira")) else "count"
        if unit == "count" and not scale and 1900 <= v <= 2100 and "." not in m.group("n"):
            continue                                         # a bare year is not a quantity
        nums.append(Slot("num", (unit, v), m.start(), m.end()))
    return dates + nums


def ctx(text: str, s: Slot) -> list[str]:
    left = toks(text[max(0, s.start - 90):s.start])[-9:]
    right = toks(text[s.end:s.end + 40])[:3]
    return [stem(t) for t in left + right if t not in STOP]


def aligned(ct: list[str], cd: list[str], kind: str) -> bool:
    if kind == "date":
        gc, gd = {G_OF[t] for t in ct if t in G_OF}, {G_OF[t] for t in cd if t in G_OF}
        topic = {t for t in ct if t not in G_OF} & {t for t in cd if t not in G_OF}
        return bool(gc & gd) and bool(topic)
    return len(set(ct) & set(cd)) >= 2


def same(a: Slot, b: Slot) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == "date":
        (m1, d1, y1), (m2, d2, y2) = a.value, b.value
        return (m1, d1) == (m2, d2) and (y1 is None or y2 is None or y1 == y2)
    (u1, v1), (u2, v2) = a.value, b.value
    return u1 == u2 and abs(v1 - v2) <= 1e-9 * max(abs(v1), abs(v2), 1)


def split_sentences(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+|\n{2,}", text):
        if text[start:m.start()].strip():
            spans.append((start, m.start()))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def compare(claim_text: str, doc_text: str):
    """Returns [(sentence, 'SUPPORTS'|'CONTRADICTS')] for the claim's dates/numbers against one document."""
    cslots = extract(claim_text)
    results = []
    sents = [(doc_text[a:b], extract(doc_text[a:b])) for a, b in split_sentences(doc_text)]
    for cs in cslots:
        ct = ctx(claim_text, cs)
        cand = []                                            # (sentence, slot) pairs that talk about the same thing
        for sent, slots in sents:
            for ds in slots:
                if ds.kind == cs.kind and aligned(ct, ctx(sent, ds), cs.kind):
                    cand.append((sent.strip(), ds))
        hit = next((c for c in cand if same(cs, c[1])), None)
        if hit:
            results.append((hit[0], "SUPPORTS"))
        elif len({c[1].value for c in cand}) == 1:           # exactly one conflicting value: a clear contradiction
            results.append((cand[0][0], "CONTRADICTS"))
        # zero candidates, or several different values: say nothing
    if any(r[1] == "CONTRADICTS" for r in results):          # one wrong central fact is enough
        results = [r for r in results if r[1] == "CONTRADICTS"]
    seen, uniq = set(), []
    for r in results:
        if r[0] not in seen:
            seen.add(r[0])
            uniq.append(r)
    return uniq[:2]
