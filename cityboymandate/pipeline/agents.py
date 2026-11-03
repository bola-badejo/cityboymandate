"""Evidence analyst, editor, and independent reviewer. The analyst and editor never receive the
author's identity: verdicts depend on claim + evidence only (partisan symmetry by construction)."""
from __future__ import annotations
from cityboymandate.core.evidence import EvidenceRegistry
from cityboymandate.lang.i18n import t
from cityboymandate.core.models import (CORRECTABLE, ClaimVerdict, DraftResponse, ExtractedClaim,
                                     Investigation, Relation, ReviewOutcome, Verdict)

MAX_LEN = 280


def analyse(claim: ExtractedClaim, reg: EvidenceRegistry, context_limited: bool = False) -> ClaimVerdict:
    ps = reg.passages_for(claim.id)
    by = lambda rel: [p for p in ps if p.relation == rel]
    sup, con, out, ctx = by(Relation.SUPPORTS), by(Relation.CONTRADICTS), by(Relation.OUTDATES), by(Relation.MISSING_CONTEXT)
    primary = lambda L: any(reg.sources[p.source_id].primary and not reg.is_excluded(reg.sources[p.source_id]) for p in L)
    strong = lambda L: bool(L) and (primary(L) or len(reg.independent_roots(L)) >= 2)

    def mk(v, why, used):
        basis = {
            "source_quality": "primary source present" if primary(used) else ("secondary only" if used else "none"),
            "corroboration": f"{len(reg.independent_roots(used))} independent root source(s)" if used else "none",
            "directness": "excerpt taken verbatim from retrieved source" if used else "n/a",
            "remaining_uncertainty": "thread context incomplete; reviewer must check" if context_limited else "none identified",
        }
        return ClaimVerdict(claim.id, v, why, [p.id for p in used], basis)

    if not claim.checkable:
        return mk(Verdict.NOT_FACT_CHECKABLE, f"{claim.kind} statement with no separable factual claim", [])
    if not ps:
        return mk(Verdict.UNVERIFIED, "no relevant evidence retrieved", [])
    if sup and con:
        return mk(Verdict.DISPUTED, "retrieved sources materially disagree", sup + con)
    if out and not con and strong(out):
        return mk(Verdict.OUTDATED, "evidence shows the information has been superseded", out)
    if ctx and not con and strong(ctx):
        return mk(Verdict.MISLEADING, "accurate element presented without material context", ctx + sup)
    if con:
        if primary(con):
            return mk(Verdict.FALSE, "primary evidence contradicts the central assertion", con)
        if strong(con):
            return mk(Verdict.MOSTLY_FALSE, "independent secondary sources contradict the central assertion", con)
        return mk(Verdict.UNVERIFIED, "contradicting evidence is weak or not independent", con)
    if sup:
        if primary(sup):
            return mk(Verdict.SUPPORTED, "primary evidence supports the central assertion", sup)
        if strong(sup):
            return mk(Verdict.MOSTLY_SUPPORTED, "independent secondary sources support the claim", sup)
        return mk(Verdict.UNVERIFIED, "supporting evidence is weak or not independent", sup)
    return mk(Verdict.UNVERIFIED, "evidence insufficient", ps)


_LEAD = {
    Verdict.FALSE: "Context matters here.",
    Verdict.MOSTLY_FALSE: "Context matters here.",
    Verdict.MISLEADING: "Some context is missing here.",
    Verdict.OUTDATED: "This information may be out of date.",
    Verdict.SUPPORTED: "This checks out.",
    Verdict.MOSTLY_SUPPORTED: "This is mostly accurate.",
}
_KEY = {Verdict.FALSE: "false", Verdict.MOSTLY_FALSE: "false", Verdict.MISLEADING: "misleading", Verdict.OUTDATED: "outdated",
        Verdict.SUPPORTED: "supported", Verdict.MOSTLY_SUPPORTED: "mostly_supported"}
_ORDER = ["FALSE", "MOSTLY_FALSE", "MISLEADING", "OUTDATED", "DISPUTED", "SUPPORTED", "MOSTLY_SUPPORTED", "UNVERIFIED", "NOT_FACT_CHECKABLE"]


def edit_draft(inv_id: str, claim: ExtractedClaim, v: ClaimVerdict, reg: EvidenceRegistry, disclosure: str = "",
               invoked: bool = False, lang: str = "en", writer=None) -> DraftResponse | None:
    """Unrequested posts only ever get a draft for correctable verdicts. When someone ASKED (a mention), the
    answer is honest whatever the verdict: confirmed, wrong, disputed, or not verifiable. Never repeats the
    claim and never mentions the author."""
    if v.verdict not in CORRECTABLE and not invoked:
        return None
    d = f" {disclosure}" if disclosure else ""
    ok = [reg.passages[i] for i in v.passage_ids if not reg.is_excluded(reg.sources[reg.passages[i].source_id])]
    if v.verdict == Verdict.NOT_FACT_CHECKABLE:
        return DraftResponse(inv_id, claim.id, t(lang, "opinion") + d, [])
    if v.verdict == Verdict.DISPUTED and ok:
        urls = list(dict.fromkeys(reg.sources[p.source_id].url for p in ok))[:2]
        return DraftResponse(inv_id, claim.id, t(lang, "disputed") + " " + " ".join(urls) + d, urls)
    if v.verdict in (Verdict.UNVERIFIED, Verdict.DISPUTED) or not ok:
        return DraftResponse(inv_id, claim.id, t(lang, "unverified") + d, [])
    p = sorted(ok, key=lambda x: not reg.sources[x.source_id].primary)[0]   # prefer primary documents
    src = reg.sources[p.source_id]
    if writer is not None and lang == "en":                     # a natural reply, only if it passes every check; otherwise the plain template
        core = writer.write(claim.text, v.verdict, p.text, src.publisher, src.url, disclosure)
        if core:
            return DraftResponse(inv_id, claim.id, f"{core} Source: {src.url}{d}", [src.url])
    head = f'{t(lang, _KEY[v.verdict])} {src.publisher} {t(lang, "states")} "'
    tail = f'" {t(lang, "source")} {src.url}' + d
    room = MAX_LEN - len(head) - len(tail)
    ex = p.text if len(p.text) <= room else p.text[: room - 1].rstrip() + "\u2026"
    return DraftResponse(inv_id, claim.id, head + ex + tail, [src.url])


def review(inv: Investigation, claim: ExtractedClaim, v: ClaimVerdict, d: DraftResponse | None,
           reg: EvidenceRegistry, context_limited: bool, invoked: bool = False) -> tuple[ReviewOutcome, list[str]]:
    """Independent check. It re-derives the verdict and re-verifies every excerpt and link, and can
    overrule the first pass. (In production this should be a separate model/prompt/config.)"""
    notes: list[str] = []
    again = analyse(claim, reg, context_limited)
    if again.verdict != v.verdict:
        return ReviewOutcome.REVISE, [f"independent re-derivation gave {again.verdict.value}, not {v.verdict.value}"]
    for pid in v.passage_ids:
        p = reg.passages[pid]
        if p.text not in reg.sources[p.source_id].content:
            return ReviewOutcome.REVISE, ["a cited passage is not verbatim in its source"]
    if any("PAST_REVIEW_DATE" in reg.sources[reg.passages[i].source_id].limitations for i in v.passage_ids):
        return ReviewOutcome.REVISE, ["a cited library document is past its review date; confirm it is still current"]
    if v.verdict in {Verdict.FALSE, Verdict.MOSTLY_FALSE} and not any(
            reg.sources[reg.passages[i].source_id].primary for i in v.passage_ids) and v.verdict == Verdict.FALSE:
        return ReviewOutcome.INSUFFICIENT_EVIDENCE, ["FALSE requires primary evidence"]
    if v.verdict in {Verdict.UNVERIFIED, Verdict.DISPUTED} and not invoked:
        return ReviewOutcome.INSUFFICIENT_EVIDENCE, ["research-only: evidence does not support a public verdict"]
    if d is not None:
        need_cites = bool(d.source_urls) or v.verdict not in {Verdict.UNVERIFIED, Verdict.NOT_FACT_CHECKABLE, Verdict.DISPUTED}
        probs = (reg.validate_citations(d.source_urls) if need_cites else []) + reg.quoted_text_is_real(d.text)
        if probs:
            return ReviewOutcome.REVISE, probs
        if len(d.text) > MAX_LEN:
            return ReviewOutcome.REVISE, ["draft too long"]
    if context_limited and v.verdict in {Verdict.FALSE, Verdict.MOSTLY_FALSE}:
        return ReviewOutcome.REVISE, ["thread context incomplete; human must confirm the post was not misread"]
    if inv.safety_flags:
        notes.append("embedded-instruction patterns were flagged in untrusted input; verdict logic unaffected")
    return ReviewOutcome.APPROVE_FOR_HUMAN_REVIEW, notes
