"""Provider interfaces + MOCK implementations. Mocks use clearly fictional SAMPLE data.
Nothing here talks to X, a search engine, or an LLM; real adapters implement the same Protocols."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Protocol
from cityboymandate.core.models import ExtractedClaim, Relation, SocialPost


@dataclass
class RetrievedDoc:
    title: str
    publisher: str
    url: str
    source_type: str
    primary: bool
    content: str
    published: str | None = None
    derived_from: str | None = None
    limitations: str = ""
    retrieved_at: object = None      # datetime; library documents carry their real snapshot date


class XProvider(Protocol):
    def get_post(self, post_id: str) -> SocialPost | None: ...


class SearchProvider(Protocol):
    def search(self, claim: ExtractedClaim) -> list[RetrievedDoc]: ...


class ClaimExtractor(Protocol):
    def extract(self, post: SocialPost, thread: list[SocialPost]) -> list[ExtractedClaim]: ...


class PassageJudge(Protocol):
    def judge(self, claim: ExtractedClaim, doc: RetrievedDoc) -> list[tuple[str, Relation]]: ...


class MockX:
    def __init__(self, posts: list[SocialPost]):
        self.posts = {p.platform_post_id: p for p in posts}

    def get_post(self, post_id):
        return self.posts.get(post_id)   # None models deleted/inaccessible posts


class MockSearch:
    """corpus: list of (keywords, RetrievedDoc). A doc is returned if any keyword is in the claim."""
    def __init__(self, corpus):
        self.corpus = corpus

    def search(self, claim):
        t = claim.text.lower()
        return [d for kws, d in self.corpus if any(k in t for k in kws)]


class MockJudge:
    """annotations: {url: [(claim_keyword, excerpt, Relation)]}. Stands in for an LLM that must
    return verbatim excerpts; the registry rejects any excerpt that is not in the source text."""
    def __init__(self, annotations):
        self.ann = annotations

    def judge(self, claim, doc):
        t = claim.text.lower()
        return [(ex, rel) for kw, ex, rel in self.ann.get(doc.url, []) if kw in t]


_OPINION = re.compile(r"\b(i think|i believe|should|must resign|worst|best|useless|incompetent|shame|i tink say|i think say|e suppose|dem suppose|mumu|nonsense|wicked|na shame|shege|ina ganin)\b", re.I)
_PREDICT = re.compile(r"\b(will (win|lose|lead|fail|succeed)|go (win|lose|fail|pass|happen))\b", re.I)
_FACTISH = re.compile(r"(\d|according to|announced|signed|deadline|closes|opens|law|act\b|results?\b|declared|killed|attack|explosion|talk say|tok say|don announce|don sign|don close|don open|dem don|announce say|ya ce|ta ce|sun ce|cewa|ya sanar|kwuru na|kwuputara)", re.I)


class RuleBasedExtractor:
    """Deterministic stand-in for LLM claim extraction. Author identity is never an input."""
    def extract(self, post, thread):
        out = []
        for s in re.split(r"(?<=[.!?])\s+", post.text.strip()):
            if not s:
                continue
            if _OPINION.search(s):
                kind, ok = "opinion", False
            elif _PREDICT.search(s):
                kind, ok = "predictive", False
            elif _FACTISH.search(s):
                kind, ok = "factual", True
            else:
                kind, ok = "interpretive", False
            low = s.lower()
            cat = ("synthetic_media" if re.search(r"deepfake|ai[- ]generated|fake (video|audio)|voice (clone|note)|cloned voice", low) else "voting_procedure" if re.search(r"deadline|regist|polling|voter|pvc|bvas|collect.*card", low) else
                   "election_results" if re.search(r"result|declared|won the|irev|collat|don win", low) else
                   "security_incident" if re.search(r"killed|attack|explosion|shooting", low) else "general")
            out.append(ExtractedClaim(text=s, kind=kind, checkable=ok, category=cat,
                                      evidence_needed="primary document or official record" if ok else ""))
        return out


class NullSearch:
    """Used for every non-demo run until a real evidence provider exists. With no evidence, every claim
    comes out UNVERIFIED (research only). Sample/fictional evidence must NEVER touch real posts."""
    def search(self, claim):
        return []


class NullJudge:
    def judge(self, claim, doc):
        return []
