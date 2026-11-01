"""Explicit state machine. Bounded loops, every transition audited, failures isolated."""
from __future__ import annotations
from cityboymandate.core.audit import AuditLog
from cityboymandate.core.evidence import EvidenceError, EvidenceRegistry
from cityboymandate.core.gate import ConfigStore
from cityboymandate.core.models import Investigation, Principal, Role, Stage
from cityboymandate.core.rbac import require
from cityboymandate.core.security import scan_untrusted
from cityboymandate.lang.detect import LANG_NAMES, detect
from cityboymandate.pipeline import agents

AGENT = Principal("pipeline", Role.AGENT)
MAX_CLAIMS, MAX_DOCS_PER_CLAIM = 5, 6

ALLOWED = {
    Stage.DISCOVERED: {Stage.CONTEXT_BUILT, Stage.FAILED},
    Stage.CONTEXT_BUILT: {Stage.CLAIMS_EXTRACTED, Stage.FAILED},
    Stage.CLAIMS_EXTRACTED: {Stage.RESEARCHED, Stage.CLOSED_NO_ACTION, Stage.FAILED},
    Stage.RESEARCHED: {Stage.ANALYSED, Stage.FAILED},
    Stage.ANALYSED: {Stage.DRAFTED, Stage.CLOSED_NO_ACTION, Stage.FAILED},
    Stage.DRAFTED: {Stage.REVIEWED, Stage.FAILED},
    Stage.REVIEWED: {Stage.AWAITING_HUMAN, Stage.HELD_FOR_HUMAN, Stage.CLOSED_NO_ACTION},
}


class Orchestrator:
    def __init__(self, x, search, extractor, judge, registry: EvidenceRegistry,
                 audit: AuditLog, config: ConfigStore):
        self.providers = x if isinstance(x, dict) else {"x": x}
        self.search, self.extractor, self.judge = search, extractor, judge
        self.reg, self.audit, self.config = registry, audit, config

    def _go(self, inv: Investigation, to: Stage, **detail):
        if to not in ALLOWED.get(inv.stage, set()):
            raise RuntimeError(f"illegal transition {inv.stage.value} -> {to.value}")
        self.audit.record(AGENT.id, "stage", "investigation", inv.id, {"from": inv.stage.value, "to": to.value, **detail})
        inv.stage = to

    def run(self, post_id: str, platform: str = "x") -> Investigation:
        require(AGENT, "investigate")
        if self.config.cfg.halted:
            raise RuntimeError("system halted")
        prov = self.providers.get(platform)
        if prov is None:
            raise LookupError(f"no provider configured for {platform}")
        post = prov.get_post(post_id)
        if post is None:
            raise LookupError("post deleted or inaccessible; nothing to investigate")
        return self.run_post(post)

    def run_post(self, post) -> Investigation:
        """Investigate a post already fetched by a discoverer (saves an API read)."""
        require(AGENT, "investigate")
        if self.config.cfg.halted:
            raise RuntimeError("system halted")
        inv = Investigation(post=post)
        self.audit.record(AGENT.id, "discovered", "investigation", inv.id, {"post": post.url})
        try:
            self._context(inv)
            self._claims(inv)
            self._research(inv)
            self._analyse_and_draft(inv)
        except Exception as e:           # fail closed: nothing proceeds toward publication
            self.audit.record(AGENT.id, "failed", "investigation", inv.id, {"error": repr(e)})
            inv.stage = Stage.FAILED
            inv.notes.append(f"failed: {e!r}")
        return inv

    def _context(self, inv):
        limited = False
        for ref in (inv.post.parent_id, inv.post.quoted_id):
            if ref:
                p = self.providers[inv.post.platform].get_post(ref)
                if p:
                    inv.thread.append(p)
                else:
                    limited = True
                    inv.post.limitations.append(f"referenced post {ref} unavailable")
        inv.safety_flags = scan_untrusted(" ".join([inv.post.text] + [t.text for t in inv.thread]))
        if inv.safety_flags:
            self.audit.record(AGENT.id, "injection_flagged", "investigation", inv.id, {"flags": inv.safety_flags})
        det = detect(inv.post.text)
        inv.language = det.lang
        if det.lang not in set(self.config.cfg.evaluated_languages) and det.lang != "und":
            inv.post.limitations.append(f"post appears to be in {LANG_NAMES[det.lang]}; claim extraction is not yet evaluated for it, so a fluent reviewer must confirm the claim was read correctly")
            limited = True
        self._go(inv, Stage.CONTEXT_BUILT, context_limited=limited, language=det.lang)

    @staticmethod
    def context_limited(inv):
        return bool(inv.post.limitations)

    def _claims(self, inv):
        inv.claims = self.extractor.extract(inv.post, inv.thread)[:MAX_CLAIMS]
        self._go(inv, Stage.CLAIMS_EXTRACTED, n=len(inv.claims))
        if not any(c.checkable for c in inv.claims) and not inv.post.invoked_by:
            self._go(inv, Stage.CLOSED_NO_ACTION, reason="NOT_FACT_CHECKABLE")

    def _research(self, inv):
        if inv.stage != Stage.CLAIMS_EXTRACTED:
            return
        for c in (c for c in inv.claims if c.checkable):
            for doc in self.search.search(c)[:MAX_DOCS_PER_CLAIM]:
                inv.safety_flags += [f for f in scan_untrusted(doc.content) if f not in inv.safety_flags]
                try:
                    extra = {"retrieved_at": doc.retrieved_at} if doc.retrieved_at else {}
                    src = self.reg.add_source(title=doc.title, publisher=doc.publisher, url=doc.url,
                                              source_type=doc.source_type, primary=doc.primary, content=doc.content,
                                              published=doc.published, derived_from=doc.derived_from,
                                              limitations=doc.limitations, **extra)
                    for text, rel in self.judge.judge(c, doc):
                        self.reg.add_passage(src.id, c.id, text, rel)
                except EvidenceError as e:
                    inv.notes.append(f"rejected evidence from {doc.url}: {e}")
                    self.audit.record(AGENT.id, "evidence_rejected", "investigation", inv.id, {"url": doc.url, "why": str(e)})
        self._go(inv, Stage.RESEARCHED)

    def _analyse_and_draft(self, inv):
        if inv.stage != Stage.RESEARCHED:
            return
        lim = self.context_limited(inv)
        for c in inv.claims:
            v = agents.analyse(c, self.reg, lim)
            inv.verdicts.append(v)
            self.audit.record(AGENT.id, "verdict", "claim", c.id, {"verdict": v.verdict.value, "why": v.rationale})
        self._go(inv, Stage.ANALYSED)
        invoked = bool(inv.post.invoked_by)
        pairs = [(c, v) for c, v in zip(inv.claims, inv.verdicts)]
        if invoked and pairs:      # one reply per interaction: answer the most material claim
            pairs = [min(pairs, key=lambda cv: agents._ORDER.index(cv[1].verdict.value))]
        for c, v in pairs:
            reply_lang = inv.language if (invoked and inv.language in set(self.config.cfg.reviewed_languages)) else "en"
            d = agents.edit_draft(inv.id, c, v, self.reg, self.config.cfg.disclosure_text, invoked, reply_lang)
            if d:
                inv.drafts.append(d)
        if not inv.drafts:
            self._go(inv, Stage.CLOSED_NO_ACTION, reason="no correctable verdicts; research only")
            return
        self._go(inv, Stage.DRAFTED, n=len(inv.drafts))
        for d in inv.drafts:
            c = next(x for x in inv.claims if x.id == d.claim_id)
            v = next(x for x in inv.verdicts if x.claim_id == c.id)
            out, notes = agents.review(inv, c, v, d, self.reg, lim, bool(inv.post.invoked_by))
            inv.reviewer_outcomes[d.id] = out
            inv.notes += notes
            self.audit.record("independent_reviewer", "review", "draft", d.id, {"outcome": out.value, "notes": notes})
        self._go(inv, Stage.REVIEWED)
        outs = {o.value for o in inv.reviewer_outcomes.values()}
        self._go(inv, Stage.AWAITING_HUMAN if "APPROVE_FOR_HUMAN_REVIEW" in outs else Stage.HELD_FOR_HUMAN if "REVISE" in outs else Stage.CLOSED_NO_ACTION)
