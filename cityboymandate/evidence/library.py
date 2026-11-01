"""A library of official documents loaded ahead of time (election timetable, Electoral Act, budget, statistics,
saved fact-checks). Real documents are supplied by you: nothing is fabricated or fetched here. Each file is plain text
(convert PDFs first, e.g. `pdftotext`), described in manifest.json."""
from __future__ import annotations
import datetime as dt
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from cityboymandate.core.security import UnsafeURL, validate_public_url
from cityboymandate.evidence.slots import STOP, split_sentences, stem, toks
from cityboymandate.pipeline.providers import RetrievedDoc

TYPES = {"legislation", "official_record", "court_judgment", "dataset", "fact_checker", "news", "expert"}
PRIMARY_TYPES = {"legislation", "official_record", "court_judgment", "dataset"}
SAMPLE_HOSTS = ("example.org", "example.com", "example.net")
MAX_BYTES = 2_000_000


@dataclass
class LibDoc:
    title: str
    publisher: str
    url: str
    source_type: str
    primary: bool
    published: str
    retrieved_on: dt.date
    review_by: dt.date | None
    derived_from: str | None
    text: str
    file: str


class DocumentLibrary:
    def __init__(self, root, allow_sample: bool = False):
        self.root, self.allow_sample = Path(root), allow_sample
        self.docs: list[LibDoc] = []
        self.problems: list[str] = []
        self._load()

    def _load(self):
        mf = self.root / "manifest.json"
        if not mf.exists():
            self.problems.append("manifest.json not found")
            return
        try:
            entries = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError as e:
            self.problems.append(f"manifest.json is not valid JSON: {e}")
            return
        for i, e in enumerate(entries):
            tag = f"entry {i} ({e.get('title', '?')})"
            try:
                self.docs.append(self._doc(e))
            except Exception as ex:
                self.problems.append(f"{tag}: {ex}")

    def _doc(self, e: dict) -> LibDoc:
        for k in ("file", "title", "publisher", "url", "source_type", "published", "retrieved_on"):
            if not e.get(k):
                raise ValueError(f"missing '{k}'")
        if e["source_type"] not in TYPES:
            raise ValueError(f"source_type must be one of {sorted(TYPES)}")
        primary = bool(e.get("primary", False))
        if primary and e["source_type"] not in PRIMARY_TYPES:
            raise ValueError("only legislation, official records, court judgments and datasets can be primary")
        url = validate_public_url(e["url"])
        host = url.split("/")[2]
        is_sample = bool(e.get("sample")) or host.endswith(SAMPLE_HOSTS)
        if is_sample and not self.allow_sample:
            raise ValueError("sample/fictional document refused outside demo mode")
        p = (self.root / e["file"]).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("file must be inside the library folder")
        if not p.is_file():
            raise ValueError(f"file not found: {e['file']}")
        if p.stat().st_size > MAX_BYTES:
            raise ValueError("file larger than 2 MB: split it")
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("file is empty")
        rev = dt.date.fromisoformat(e["review_by"]) if e.get("review_by") else None
        return LibDoc(e["title"], e["publisher"], url, e["source_type"], primary, e["published"],
                      dt.date.fromisoformat(e["retrieved_on"]), rev, e.get("derived_from"), text, e["file"])

    def status(self, today: dt.date | None = None) -> dict:
        today = today or dt.date.today()
        stale = [d.title for d in self.docs if d.review_by and today > d.review_by]
        return {"configured": True, "documents": len(self.docs), "primary": sum(d.primary for d in self.docs),
                "by_type": dict(Counter(d.source_type for d in self.docs)), "past_review_date": stale,
                "problems": self.problems, "newest_retrieved": max((d.retrieved_on.isoformat() for d in self.docs), default=None)}


class LibrarySearch:
    """BM25 over sentences; a document's score is its best sentence. Needs 2+ shared content words."""
    K1, B = 1.5, 0.75

    def __init__(self, lib: DocumentLibrary, top_k: int = 4, min_score: float = 2.0, clock=dt.date.today):
        self.lib, self.k, self.min, self.clock = lib, top_k, min_score, clock
        self.rows = []                                               # (doc index, token Counter, length)
        for di, d in enumerate(lib.docs):
            for a, b in split_sentences(d.text):
                t = [stem(w) for w in toks(d.text[a:b]) if w not in STOP]
                if t:
                    self.rows.append((di, Counter(t), len(t)))
        n = len(self.rows) or 1
        self.df = Counter(w for _, c, _ in self.rows for w in c)
        self.avg = (sum(r[2] for r in self.rows) / n) or 1
        self.n = n

    def _score(self, q: list[str]):
        best = {}
        for di, c, ln in self.rows:
            matched = [w for w in q if w in c]
            if len(matched) < 2:
                continue
            s = 0.0
            for w in matched:
                idf = math.log(1 + (self.n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                s += idf * (c[w] * (self.K1 + 1)) / (c[w] + self.K1 * (1 - self.B + self.B * ln / self.avg))
            best[di] = max(best.get(di, 0.0), s)
        return best

    def search(self, claim) -> list[RetrievedDoc]:
        q = list({stem(w) for w in toks(claim.text) if w not in STOP})
        ranked = sorted(((s, di) for di, s in self._score(q).items() if s >= self.min), reverse=True)[: self.k]
        today, out = self.clock(), []
        for _, di in ranked:
            d = self.lib.docs[di]
            lim = f"Library copy retrieved {d.retrieved_on.isoformat()}."
            if d.review_by and today > d.review_by:
                lim = f"PAST_REVIEW_DATE: library copy was due for re-checking on {d.review_by.isoformat()}. " + lim
            out.append(RetrievedDoc(d.title, d.publisher, d.url, d.source_type, d.primary, d.text, d.published, d.derived_from, lim,
                                    dt.datetime.combine(d.retrieved_on, dt.time(), tzinfo=dt.timezone.utc)))
        return out
