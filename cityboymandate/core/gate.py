"""System configuration and the publishing gate. Default: monitoring + draft-only.
The gate fails closed and collects every reason it refuses."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from cityboymandate.core.audit import AuditLog
from cityboymandate.core.evidence import EvidenceRegistry
from cityboymandate.core.models import (CORRECTABLE, ClaimVerdict, DraftResponse, ExtractedClaim,
                                     HumanReview, Principal)
from cityboymandate.core.rbac import REVIEW_ROLES, require
from cityboymandate.platforms.capabilities import PLATFORMS

HIGH_IMPACT = {"synthetic_media", "election_results", "voting_procedure", "security_incident", "public_emergency",
               "allegation_individual", "ethnic_religious"}


@dataclass(frozen=True)
class SystemConfiguration:
    publishing_enabled: bool = False
    platform_approvals: dict = field(default_factory=dict)  # {"x": "<written X approval ref>", "facebook": ...}
    opt_in_mechanism_confirmed: bool = False
    opt_out_mechanism_confirmed: bool = False
    halted: bool = False                       # emergency shutdown
    max_replies_per_hour: int = 5
    reviews_required_default: int = 1
    reviews_required_high_impact: int = 2
    evaluated_languages: tuple = ("en",)   # languages native speakers have tested; others are held for a human who reads them
    reviewed_languages: tuple = ("en",)    # languages replies may be written in (wording signed off by a fluent reviewer)
    disclosure_text: str = "Automated reply from APC communications desk. Reply STOP to opt out."   # shown on every public reply


class ConfigStore:
    def __init__(self, audit: AuditLog, cfg: SystemConfiguration | None = None):
        self.cfg = cfg or SystemConfiguration()
        self.audit = audit
        self.on_change = lambda: None     # Service sets this to persist settings

    def update(self, who: Principal, **changes) -> SystemConfiguration:
        require(who, "settings.write")           # AGENT role can never reach this
        new = replace(self.cfg, **changes)
        if new.publishing_enabled and not (new.platform_approvals and new.opt_in_mechanism_confirmed
                                           and new.opt_out_mechanism_confirmed):
            raise ValueError("publishing requires a platform approval reference and confirmed opt-in/opt-out mechanisms")
        self.audit.record(who.id, "config.update", "config", "system", {k: ("<set>" if k == "platform_approvals" else v) for k, v in changes.items()})
        self.cfg = new
        self.on_change()
        return new

    def emergency_stop(self, who: Principal) -> None:
        require(who, "emergency_stop")
        self.cfg = replace(self.cfg, halted=True, publishing_enabled=False)
        self.audit.record(who.id, "config.emergency_stop", "config", "system", {})
        self.on_change()

    def clear_halt(self, who: Principal) -> None:
        require(who, "emergency_clear")
        self.cfg = replace(self.cfg, halted=False)
        self.audit.record(who.id, "config.clear_halt", "config", "system", {})
        self.on_change()


@dataclass
class PublishContext:
    post_still_exists: bool
    recipient_opted_in: bool
    replies_last_hour: int
    already_replied_to_post: bool
    platform: str = "x"
    target_is_own_asset: bool = False
    invoked: bool = False        # the reply answers someone who @-mentioned us (a user interaction)


@dataclass
class GateDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def required_reviews(cfg: SystemConfiguration, claim: ExtractedClaim) -> int:
    return cfg.reviews_required_high_impact if claim.category in HIGH_IMPACT else cfg.reviews_required_default


def check_publish(cfg: SystemConfiguration, draft: DraftResponse, claim: ExtractedClaim,
                  verdict: ClaimVerdict, reviews: list[HumanReview], ctx: PublishContext,
                  registry: EvidenceRegistry) -> GateDecision:
    r: list[str] = []
    if cfg.halted:
        r.append("system is halted (emergency shutdown)")
    if not cfg.publishing_enabled:
        r.append("publishing disabled: draft-only mode")
    caps = PLATFORMS.get(ctx.platform)
    if caps is None:
        r.append(f"unknown platform {ctx.platform}")
    else:
        if not cfg.platform_approvals.get(ctx.platform):
            r.append(f"no {ctx.platform} approval reference on file")
        if not caps.reply_to_third_party_posts and not ctx.target_is_own_asset:
            r.append(f"{ctx.platform} does not permit replies to posts on assets we do not operate")
    if not (cfg.opt_in_mechanism_confirmed and cfg.opt_out_mechanism_confirmed):
        r.append("opt-in/opt-out mechanisms not confirmed")
    if ctx.platform == "x" and not (ctx.invoked or ctx.target_is_own_asset):
        r.append("X does not allow automated replies triggered by keyword search; reply must answer a mention or a reply to our own post")
    if verdict.verdict not in CORRECTABLE and not ctx.invoked:
        r.append(f"verdict {verdict.verdict.value} is research-only; no public correction")
    if not (ctx.invoked and verdict.verdict.value in ("UNVERIFIED", "NOT_FACT_CHECKABLE")):
        r += registry.validate_citations(draft.source_urls)
    r += registry.quoted_text_is_real(draft.text)
    if cfg.disclosure_text and cfg.disclosure_text not in draft.text:
        r.append("draft is missing the required disclosure label")
    if len(draft.text) > 280:
        r.append("draft exceeds 280 characters")
    mine = [x for x in reviews if x.draft_id == draft.id]
    if any(x.decision == "REJECT" for x in mine):
        r.append("a human reviewer rejected this draft")
    approvers = {x.reviewer_id for x in mine if x.decision == "APPROVE" and x.reviewer_role in REVIEW_ROLES}
    need = required_reviews(cfg, claim)
    if len(approvers) < need:
        r.append(f"needs {need} distinct human approval(s); has {len(approvers)}")
    if not ctx.post_still_exists:
        r.append("original post no longer exists")
    if not ctx.recipient_opted_in:
        r.append("recipient has not opted in")
    if ctx.already_replied_to_post:
        r.append("one-reply-per-interaction limit reached")
    if ctx.replies_last_hour >= cfg.max_replies_per_hour:
        r.append("hourly reply limit reached")
    return GateDecision(allowed=not r, reasons=r)
