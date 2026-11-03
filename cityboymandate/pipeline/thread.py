"""Reads a conversation the way a careful person would before answering: is this a joke, a hypothetical, someone
repeating a rumour, or a fragment that only makes sense after an earlier post? None of this decides truth. It decides
whether a person must look first."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

SARCASM = re.compile(r"(/s\b|\blol\b|\blmao+\b|\bha(ha)+\b|\byeah,? right\b|\bas if\b|\bsure,|\bwhat a joke\b|[\U0001F602\U0001F923\U0001F644\U0001F643])", re.I)
HYPO = re.compile(r"\b(imagine|what if|suppose|hypothetically|if only)\b", re.I)
REPORTED = re.compile(r"(\"[^\"]{15,}\"|\u201c[^\u201d]{15,}\u201d|\bthey say\b|\bpeople are saying\b|\brumou?rs?\b|\ballegedly\b|\bsources say\b)", re.I)
FRAGMENT = re.compile(r"^\W*(this|that|these|those|he|she|they|it)\b", re.I)


@dataclass
class ThreadAnalysis:
    limitations: list[str] = field(default_factory=list)   # a person must confirm before anything is sent
    notes: list[str] = field(default_factory=list)         # context for the reviewer
    participants: int = 1


def analyse_thread(post, thread) -> ThreadAnalysis:
    t = ThreadAnalysis()
    texts = [post.text] + [p.text for p in thread]
    t.participants = len({post.author_handle, *[p.author_handle for p in thread]})
    if SARCASM.search(post.text):
        t.limitations.append("possible sarcasm or a joke: a person must confirm what was meant before anyone answers it as a factual claim")
    if HYPO.search(post.text):
        t.limitations.append("reads as a hypothetical rather than a statement of fact")
    if post.quoted_id or REPORTED.search(post.text):
        t.notes.append("the claim may be reported from someone else: answer the claim, not the person posting it")
    if FRAGMENT.match(post.text) and len(post.text) < 50 and not thread:
        t.limitations.append("short fragment whose meaning depends on an earlier post that could not be retrieved")
    if post.parent_id and not any(p.platform_post_id == post.parent_id for p in thread):
        t.limitations.append("the post it replies to could not be retrieved")
    if len(texts) > 1:
        t.notes.append(f"conversation read: {len(texts)} posts from {t.participants} people")
    return t
