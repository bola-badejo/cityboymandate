"""Tone scoring for QUEUE ORDERING ONLY. It is never passed to the evidence analyst, the editor, or the
publishing gate. Criticism is not misinformation: a critical post whose claim is TRUE gets a SUPPORTED
verdict and no reply. This lexicon is a placeholder; replace with an evaluated classifier."""
from __future__ import annotations
from dataclasses import dataclass
from cityboymandate.monitor.keywords import norm

CRITICAL = ["thief", "thieves", "corrupt", "fraud", "rigged", "incompetent", "failure", "failed", "wicked",
            "dictator", "impeach", "resign", "useless", "scam", "shame", "hunger", "nonsense", "liar", "lies"]
CRITICAL += ["dey thief", "mumu", "ole", "yeye", "hunger dey", "suffer", "na lie", "dem dey lie", "must go", "fall hand", "shege", "barawo", "onye ohi"]
SUPPORTIVE = ["congratulat", "thank", "praise", "achievement", "progress", "great", "well done", "proud", "commend"]
SUPPORTIVE += ["well done", "e good", "dem dey try", "e dey try", "god bless", "we dey with", "sai baba"]


@dataclass
class Tone:
    label: str            # CRITICAL | NEUTRAL | SUPPORTIVE
    indicators: list[str]


def score_tone(text: str) -> Tone:
    n = " " + norm(text) + " "
    neg = [w for w in CRITICAL if f" {w}" in n]
    pos = [w for w in SUPPORTIVE if f" {w}" in n]
    if len(neg) > len(pos):
        return Tone("CRITICAL", neg)
    if len(pos) > len(neg):
        return Tone("SUPPORTIVE", pos)
    return Tone("NEUTRAL", neg + pos)
