"""Media authenticity rules for suspected AI-generated or edited video/audio/images.

Hard-won lessons from the last election cycle: detectors are unreliable (false positives on real footage, false
negatives on good fakes), and calling a REAL recording 'fake' is just as damaging as sharing a fake. So:
  * A detector score is a weak hint. It can prompt a closer look but NEVER moves a verdict.
  * 'Manipulated/synthetic' is only asserted with direct evidence (original located and different, or valid
    content credentials showing generation) or two independent non-detector sources. Anything less is UNVERIFIED.
  * The function never receives who the clip helps or hurts. Identical signals give identical verdicts.
  * Real transcription, keyframe and reverse-search providers plug in via the interfaces below (not built yet)."""
from __future__ import annotations
import enum
from dataclasses import dataclass, field
from typing import Protocol


class MediaVerdict(str, enum.Enum):
    MEDIA_AUTHENTICITY_UNVERIFIED = "MEDIA_AUTHENTICITY_UNVERIFIED"
    AUTHENTIC = "AUTHENTIC"
    AUTHENTIC_BUT_MISLEADING_CONTEXT = "AUTHENTIC_BUT_MISLEADING_CONTEXT"   # real clip, wrong date/place/caption
    MANIPULATED_CORROBORATED = "MANIPULATED_CORROBORATED"
    SYNTHETIC_CORROBORATED = "SYNTHETIC_CORROBORATED"


@dataclass
class Signal:
    kind: str        # detector | content_credentials | source_comparison | reverse_search | official_statement | fact_checker
    supports: str    # authentic | manipulated | synthetic | recycled
    independent: bool = True
    detail: str = ""


@dataclass
class MediaAssessment:
    verdict: MediaVerdict
    reasons: list[str]
    may_say_fake_publicly: bool = False     # still requires two human reviewers (category synthetic_media)
    limitations: list[str] = field(default_factory=lambda: ["automated analysis; a human must review before any public statement"])


DIRECT = {"content_credentials", "source_comparison"}          # evidence that speaks to the file itself
SECONDARY = {"official_statement", "fact_checker", "reverse_search"}


class MediaProvider(Protocol):          # to implement in Phase E
    def transcribe(self, media_url: str) -> list[dict]: ...
    def keyframes(self, media_url: str) -> list[bytes]: ...
    def reverse_search(self, media_url: str) -> list[dict]: ...


def assess(signals: list[Signal]) -> MediaAssessment:
    real = [s for s in signals if s.kind != "detector"]
    why = []
    if any(s.kind == "detector" for s in signals):
        why.append("detector output noted as a weak hint only; it does not count as evidence")
    fake = [s for s in real if s.supports in ("manipulated", "synthetic")]
    auth = [s for s in real if s.supports == "authentic"]
    recycled = [s for s in real if s.supports == "recycled"]
    if fake and auth:
        return MediaAssessment(MediaVerdict.MEDIA_AUTHENTICITY_UNVERIFIED, why + ["credible evidence points both ways; no verdict"])
    direct_fake = [s for s in fake if s.kind in DIRECT]
    indep_fake = {s.kind + s.detail for s in fake if s.independent and s.kind in SECONDARY | DIRECT}
    if direct_fake or len(indep_fake) >= 2:
        synthetic = any(s.supports == "synthetic" for s in fake)
        v = MediaVerdict.SYNTHETIC_CORROBORATED if synthetic else MediaVerdict.MANIPULATED_CORROBORATED
        return MediaAssessment(v, why + ["corroborated by direct evidence or two independent sources"], may_say_fake_publicly=True)
    if recycled and any(s.kind in DIRECT | {"reverse_search"} for s in recycled):
        return MediaAssessment(MediaVerdict.AUTHENTIC_BUT_MISLEADING_CONTEXT, why + ["genuine footage presented with a different date, place or event"])
    if auth and any(s.kind in DIRECT | {"official_statement"} for s in auth):
        return MediaAssessment(MediaVerdict.AUTHENTIC, why + ["origin confirmed by provenance or a matching original"])
    return MediaAssessment(MediaVerdict.MEDIA_AUTHENTICITY_UNVERIFIED, why + ["insufficient evidence either way"])


def safe_reply(a: MediaAssessment) -> str:
    """Wording that never overstates. 'Fake' is used only when corroborated."""
    return {
        MediaVerdict.MEDIA_AUTHENTICITY_UNVERIFIED: "We could not confirm where this clip came from or whether it was edited. Please treat it with caution until an original source appears.",
        MediaVerdict.AUTHENTIC: "This clip matches its original source.",
        MediaVerdict.AUTHENTIC_BUT_MISLEADING_CONTEXT: "This footage is real but it is not from the event described. The original source is linked below.",
        MediaVerdict.MANIPULATED_CORROBORATED: "This clip has been edited and does not match the original recording. The original is linked below.",
        MediaVerdict.SYNTHETIC_CORROBORATED: "This clip was generated or altered with AI, according to the evidence linked below.",
    }[a.verdict]
