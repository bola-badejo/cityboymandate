"""Starter keyword set for mentions of President Bola Ahmed Tinubu and APC. Reviewer-editable via rules.
VERIFY every handle/hashtag against the live accounts before launch; these are from memory.
Ambiguous short terms only count when a stronger term also matches (e.g. 'BAT' alone is a cricket bat)."""
from __future__ import annotations
import re
import unicodedata

DEFAULT_KEYWORDS = {
    "names": ["Bola Ahmed Tinubu", "Bola Tinubu", "Tinubu", "President Tinubu", "Asiwaju"],
    "handles": ["@officialABAT", "@NGRPresident"],
    "slogans_and_tags": ["Renewed Hope", "#RenewedHope", "#Tinubu2027", "All Progressives Congress"],
    "local_expressions": ["Jagaban", "Emilokan", "Shettima"],   # nickname, 2022 slogan, VP: posts that name him without his name
    "misspellings": ["Tinibu", "Tinubo", "Tunubu"],
    "policy_topics": ["fuel subsidy removal", "naira exchange rate", "tax reform bill", "student loan", "minimum wage"],
    "ambiguous": ["BAT", "APC"],
}
RULE_PURPOSES = {
    "names": "Find claims that name the President so factual errors can be checked against primary records",
    "handles": "Direct mentions of official accounts",
    "slogans_and_tags": "Campaign slogans and hashtags where claims circulate",
    "local_expressions": "Nicknames and slogans used in Pidgin, Yoruba and Hausa posts that omit his name",
    "misspellings": "Common misspellings of the name",
    "policy_topics": "Policy areas where statistics and legal claims are often wrong",
    "ambiguous": "Short or ambiguous terms: only counted alongside a stronger match",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def _pat(term: str):
    t = re.escape(norm(term))
    return re.compile(r"(?<![a-z0-9_])" + t + r"(?![a-z0-9_])")


class KeywordMatcher:
    def __init__(self, groups: dict[str, list[str]] | None = None):
        self.groups = groups or DEFAULT_KEYWORDS
        self.strong = [(t, _pat(t)) for g, ts in self.groups.items() if g != "ambiguous" for t in ts]
        self.weak = [(t, _pat(t)) for t in self.groups.get("ambiguous", [])]

    def match(self, text: str) -> list[str]:
        n = norm(text)
        hits = [t for t, p in self.strong if p.search(n)]
        if hits:                                   # ambiguous terms only count next to a strong hit
            hits += [t for t, p in self.weak if p.search(n)]
        return hits

    def x_queries(self, max_len: int = 480, extra: str = "-is:retweet") -> list[str]:
        """Chunk OR-queries to fit X's query length limit. Ambiguous terms are matched locally, not searched."""
        terms = [f'"{t}"' if " " in t else t for g, ts in self.groups.items() if g != "ambiguous" for t in ts]
        out, cur = [], []
        for t in terms:
            trial = "(" + " OR ".join(cur + [t]) + ") " + extra
            if len(trial) > max_len and cur:
                out.append("(" + " OR ".join(cur) + ") " + extra)
                cur = [t]
            else:
                cur.append(t)
        if cur:
            out.append("(" + " OR ".join(cur) + ") " + extra)
        return out
