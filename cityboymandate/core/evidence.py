"""Evidence registry. Nothing can be cited unless it was retrieved and stored here, and every
stored passage must be a verbatim excerpt of the retrieved source text."""
from __future__ import annotations
import hashlib
import re
from cityboymandate.core.models import EvidencePassage, EvidenceSource, Relation, utcnow
from cityboymandate.core.security import validate_public_url


class EvidenceError(Exception):
    pass


class EvidenceRegistry:
    def __init__(self):
        self.sources: dict[str, EvidenceSource] = {}
        self.passages: dict[str, EvidencePassage] = {}
        self._by_url: dict[str, str] = {}
        self.policy = None   # SourcePolicy; set by the service

    def add_source(self, **kw) -> EvidenceSource:
        kw["url"] = validate_public_url(kw["url"])
        if not kw.get("content", "").strip():
            raise EvidenceError("cannot register a source with no retrieved content")
        if kw["url"] in self._by_url:
            return self.sources[self._by_url[kw["url"]]]
        kw.setdefault("retrieved_at", utcnow())
        src = EvidenceSource(**kw)
        src.content_sha256 = hashlib.sha256(src.content.encode()).hexdigest()
        self.sources[src.id] = src
        self._by_url[src.url] = src.id
        return src

    def add_passage(self, source_id: str, claim_id: str, text: str, relation: Relation) -> EvidencePassage:
        src = self.sources[source_id]
        if text.strip() not in src.content:
            raise EvidenceError("passage is not a verbatim excerpt of the retrieved source")
        p = EvidencePassage(source_id, claim_id, text.strip(), relation)
        self.passages[p.id] = p
        return p

    def passages_for(self, claim_id: str) -> list[EvidencePassage]:
        return [p for p in self.passages.values() if p.claim_id == claim_id]

    def root_of(self, source_id: str) -> str:
        s = self.sources[source_id]
        return s.derived_from or s.url

    def is_excluded(self, src: EvidenceSource) -> bool:
        if not self.policy:
            return False
        return self.policy.is_excluded(src.url) or bool(src.derived_from and self.policy.is_excluded(src.derived_from))

    def independent_roots(self, passages: list[EvidencePassage]) -> set[str]:
        """Articles repeating one original source count once; downgraded sources count zero."""
        return {self.root_of(p.source_id) for p in passages if not self.is_excluded(self.sources[p.source_id])}

    def validate_citations(self, urls: list[str]) -> list[str]:
        problems = []
        if not urls:
            problems.append("draft cites no source")
        for u in urls:
            try:
                canon = validate_public_url(u)
            except Exception as e:
                problems.append(f"unsafe or invalid URL {u}: {e}")
                continue
            if canon not in self._by_url:
                problems.append(f"URL was never retrieved/registered: {u}")
            elif self.is_excluded(self.sources[self._by_url[canon]]):
                problems.append(f"cited source is currently downgraded: {u}")
        return problems

    def quoted_text_is_real(self, text: str) -> list[str]:
        """Every quoted segment in a draft must appear verbatim in some registered source."""
        problems = []
        for seg in re.findall(r'"([^"]+)"', text):
            seg = seg.rstrip("…").strip()
            if seg and not any(seg in s.content for s in self.sources.values()):
                problems.append(f"quoted text not found in any retrieved source: {seg[:60]}")
        return problems
