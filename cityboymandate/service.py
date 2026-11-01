"""Application service: auth, rules, investigations, human review, gated publishing.
Everything the HTTP layer does goes through here, so it is testable without a web framework."""
from __future__ import annotations
import datetime as dt
import hashlib
import hmac
import os
import jwt
from cityboymandate.core.audit import AuditLog
from cityboymandate.core.evidence import EvidenceRegistry
from cityboymandate.core.gate import ConfigStore, PublishContext, check_publish
from cityboymandate.core.models import (HumanReview, Investigation, MonitoringRule, Principal, Role, Stage,
                                     utcnow)
from cityboymandate.core.rbac import PermissionDenied, require
from cityboymandate.core.sources import SourcePolicy
from cityboymandate.monitor.keywords import DEFAULT_KEYWORDS, RULE_PURPOSES
from cityboymandate.pipeline.orchestrator import Orchestrator


class AuthError(Exception):
    pass


class PublishingNotConfigured(Exception):
    pass


class PublisherRouter:
    def __init__(self, by_platform: dict):
        self.by = by_platform

    def publish(self, draft, post):
        if post.platform not in self.by:
            raise PublishingNotConfigured(f"no publisher for {post.platform}")
        return self.by[post.platform].publish(draft, post)


class DisabledPublisher:
    """Default. Even if the gate allows, nothing is ever sent until a real Publisher is deliberately wired in."""
    def publish(self, draft, post):
        raise PublishingNotConfigured("no publisher configured: draft-only mode")


def _hash(pw: str, salt: bytes) -> str:
    return hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()


class Service:
    def __init__(self, orchestrator: Orchestrator, registry: EvidenceRegistry, audit: AuditLog,
                 config: ConfigStore, jwt_secret: str | None = None, publisher=None, token_minutes: int = 60, store=None):
        secret = jwt_secret or os.environ.get("JWT_SECRET", "")
        if len(secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self.o, self.reg, self.audit, self.config = orchestrator, registry, audit, config
        self.secret, self.publisher, self.ttl = secret, publisher or DisabledPublisher(), token_minutes
        self.users: dict[str, tuple[str, bytes, str, Role]] = {}   # username -> (id, salt, hash, role)
        self.invs: dict[str, Investigation] = {}
        self.rules: dict[str, MonitoringRule] = {}
        self.reviews: list[HumanReview] = []
        self.pubs: list[dict] = []
        self.sources = SourcePolicy(audit)
        registry.policy = self.sources
        self.token_manager = None             # TokenManager when X OAuth refresh is configured
        self.library = None                   # DocumentLibrary, set by main
        self.runner = None                    # CycleRunner, set by main
        self.opted_in: set[str] = set()       # "platform:handle" who opted in to automated replies
        self.opted_out: set[str] = set()
        self.extra: dict = {}                 # cycle dedupe set and cursors, so a restart does not re-process old posts
        self.store = store
        config.on_change = self._save
        if store:
            self._load()

    # ---- persistence
    def _save(self):
        if not self.store:
            return
        self.store.save({"users": self.users, "invs": self.invs, "rules": self.rules, "reviews": self.reviews, "pubs": self.pubs,
                         "opted_in": self.opted_in, "opted_out": self.opted_out, "extra": self.extra, "cfg": self.config.cfg,
                         "sources": self.sources.entries, "ev_sources": self.reg.sources, "ev_passages": self.reg.passages,
                         "ev_by_url": self.reg._by_url})

    def _load(self):
        s = self.store.load()
        if not s:
            return
        self.users, self.invs, self.rules, self.reviews, self.pubs = s["users"], s["invs"], s["rules"], s["reviews"], s["pubs"]
        self.opted_in, self.opted_out, self.extra = s["opted_in"], s["opted_out"], s["extra"]
        self.config.cfg, self.sources.entries = s["cfg"], s["sources"]
        self.reg.sources, self.reg.passages, self.reg._by_url = s["ev_sources"], s["ev_passages"], s["ev_by_url"]

    # ---- auth
    def bootstrap_admin(self, username: str, password: str) -> Principal:
        if self.users:
            raise PermissionDenied("bootstrap only allowed when no users exist")
        return self._add_user("system", username, password, Role.ADMIN)

    def create_user(self, actor: Principal, username: str, password: str, role: Role) -> Principal:
        require(actor, "users.write")
        if role == Role.AGENT:
            raise PermissionDenied("the AGENT role is not assignable to login users")
        return self._add_user(actor.id, username, password, role)

    def _add_user(self, by, username, password, role):
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        salt = os.urandom(16)
        uid = f"usr_{username}"
        self.users[username] = (uid, salt, _hash(password, salt), role)
        self.audit.record(by, "user.create", "user", uid, {"role": role.value})
        self._save()
        return Principal(uid, role)

    def login(self, username: str, password: str) -> str:
        u = self.users.get(username)
        if not u or not hmac.compare_digest(u[2], _hash(password, u[1])):
            self.audit.record("anonymous", "login.failed", "user", username, {})
            raise AuthError("invalid credentials")
        exp = utcnow() + dt.timedelta(minutes=self.ttl)
        return jwt.encode({"sub": u[0], "role": u[3].value, "exp": exp}, self.secret, algorithm="HS256")

    def principal_from_token(self, token: str) -> Principal:
        try:
            c = jwt.decode(token, self.secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
            role = Role(c["role"])
            if role == Role.AGENT:
                raise AuthError("agent tokens not accepted")
            return Principal(c["sub"], role)
        except (jwt.PyJWTError, ValueError) as e:
            raise AuthError(str(e))

    # ---- monitoring rules
    def create_rule(self, who: Principal, **kw) -> MonitoringRule:
        require(who, "rules.write")
        rule = MonitoringRule(owner=who.id, **kw)
        self.rules[rule.id] = rule
        self.audit.record(who.id, "rule.create", "rule", rule.id, {"pattern": rule.pattern, "purpose": rule.purpose})
        self._save()
        return rule

    def set_rule_active(self, who: Principal, rule_id: str, active: bool) -> MonitoringRule:
        require(who, "rules.write")
        self.rules[rule_id].active = active
        self.audit.record(who.id, "rule.pause" if not active else "rule.resume", "rule", rule_id, {})
        self._save()
        return self.rules[rule_id]

    def seed_default_rules(self, who: Principal) -> int:
        if self.rules:
            return 0
        for group, terms in DEFAULT_KEYWORDS.items():
            self.create_rule(who, pattern=" | ".join(terms), kind="exact", category="general",
                             purpose=RULE_PURPOSES[group], platforms=["x", "facebook", "instagram"])
        return len(DEFAULT_KEYWORDS)

    # ---- source policy (audited, reversible, reason-coded)
    def downgrade_source(self, who: Principal, domain: str, reason_code: str, explanation: str, evidence_url: str):
        e = self.sources.downgrade(who, domain, reason_code, explanation, evidence_url)
        self._save()
        return e

    def restore_source(self, who: Principal, domain: str, note: str):
        self.sources.restore(who, domain, note)
        self._save()

    def list_sources(self, who: Principal):
        require(who, "read")
        return self.sources.listing()

    # ---- investigations
    def investigate(self, who: Principal, post_id: str | None = None, platform: str = "x", post=None) -> Investigation:
        require(who, "investigate")
        inv = self.o.run_post(post) if post is not None else self.o.run(post_id, platform)
        self.invs[inv.id] = inv
        self.audit.record(who.id, "investigate.request", "investigation", inv.id, {"post": inv.post.platform_post_id, "platform": inv.post.platform})
        self._save()
        return inv

    def queue(self, who: Principal) -> list[Investigation]:
        require(who, "read")
        return [i for i in self.invs.values() if i.stage in (Stage.AWAITING_HUMAN, Stage.HELD_FOR_HUMAN)]

    def _find_draft(self, draft_id):
        for inv in self.invs.values():
            for d in inv.drafts:
                if d.id == draft_id:
                    claim = next(c for c in inv.claims if c.id == d.claim_id)
                    verdict = next(v for v in inv.verdicts if v.claim_id == claim.id)
                    return inv, claim, verdict, d
        raise KeyError(draft_id)

    # ---- human review
    def review(self, who: Principal, draft_id: str, decision: str, note: str = "") -> HumanReview:
        require(who, "review")
        if decision not in ("APPROVE", "REJECT", "REQUEST_RESEARCH"):
            raise ValueError("bad decision")
        inv, _, _, d = self._find_draft(draft_id)
        r = HumanReview(d.id, who.id, who.role, decision, note)
        self.reviews.append(r)
        d.status = {"APPROVE": "APPROVED_BY_HUMAN", "REJECT": "REJECTED", "REQUEST_RESEARCH": "NEEDS_RESEARCH"}[decision]
        self.audit.record(who.id, "human_review", "draft", d.id, {"decision": decision, "note": note})
        self._save()
        return r

    # ---- opt-in / opt-out
    def record_opt_in(self, who: Principal, platform: str, handle: str):
        require(who, "review")
        self.opted_in.add(f"{platform}:{handle}")
        self.audit.record(who.id, "opt_in", "recipient", f"{platform}:{handle}", {})
        self._save()

    def record_opt_out(self, platform: str, handle: str):
        """Anyone can opt out; takes effect immediately and overrides opt-in."""
        self.opted_out.add(f"{platform}:{handle}")
        self.audit.record("recipient", "opt_out", "recipient", f"{platform}:{handle}", {})
        self._save()

    # ---- gated publishing
    def _evaluate(self, draft_id: str):
        inv, claim, verdict, d = self._find_draft(draft_id)
        post, now = inv.post, utcnow()
        invoked = bool(post.invoked_by)
        key = f"{post.platform}:{post.invoked_by or post.author_handle}"
        target = post.reply_to_id or post.platform_post_id
        prov = self.o.providers[post.platform]
        live = prov.get_post(post.platform_post_id)   # does it still exist?
        if post.reply_to_id:
            live = live and prov.get_post(post.reply_to_id)   # and the mention we are answering
        recent = [p for p in self.pubs if p["platform"] == post.platform and now - p["at"] < dt.timedelta(hours=1)]
        ctx = PublishContext(
            post_still_exists=live is not None,
            recipient_opted_in=((invoked or post.own_asset or key in self.opted_in) and key not in self.opted_out),   # a mention or a comment on our own asset is an interaction; STOP still wins
            replies_last_hour=len(recent),
            already_replied_to_post=any(p["post"] == target and p["platform"] == post.platform for p in self.pubs),
            platform=post.platform, target_is_own_asset=post.own_asset, invoked=invoked)
        return d, post, now, check_publish(self.config.cfg, d, claim, verdict, self.reviews, ctx, self.reg)

    def gate_preview(self, who: Principal, draft_id: str):
        """Dry run for the dashboard: same checks as publishing, zero side effects."""
        require(who, "review")
        return self._evaluate(draft_id)[3]

    def attempt_publish(self, who: Principal, draft_id: str):
        require(who, "publish")
        d, post, now, decision = self._evaluate(draft_id)
        self.audit.record(who.id, "publish.attempt", "draft", d.id,
                          {"allowed": decision.allowed, "reasons": decision.reasons, "platform": post.platform})
        if decision.allowed:
            try:
                resp = self.publisher.publish(d, post)          # raises PublishingNotConfigured by default
                rid = (resp or {}).get("id") or ((resp or {}).get("data") or {}).get("id") if isinstance(resp, dict) else None
                if rid:
                    self.extra["own_reply_ids"] = (self.extra.get("own_reply_ids", []) + [str(rid)])[-2000:]
            except Exception as e:
                self.audit.record(who.id, "publish.failed", "draft", d.id, {"error": type(e).__name__})
                raise
            self.pubs.append({"platform": post.platform, "post": post.reply_to_id or post.platform_post_id, "at": now, "draft": d.id})
            d.status = "PUBLISHED"
            self.audit.record(who.id, "publish.sent", "draft", d.id, {})
            self._save()
        return decision

    # ---- read models for the dashboard
    def list_rules(self, who: Principal):
        require(who, "rules.read")
        return list(self.rules.values())

    def evidence_for_claim(self, who: Principal, claim_id: str) -> list[dict]:
        require(who, "read")
        out = []
        for ps in self.reg.passages_for(claim_id):
            s = self.reg.sources[ps.source_id]
            out.append({"passage": ps.text, "relation": ps.relation.value, "publisher": s.publisher, "title": s.title,
                        "url": s.url, "primary": s.primary, "source_type": s.source_type, "published": s.published,
                        "derived_from": s.derived_from, "content": s.content, "retrieved_at": s.retrieved_at.isoformat()})
        return out

    def library_status(self, who: Principal) -> dict:
        require(who, "read")
        return self.library.status() if self.library else {"configured": False, "note": "no LIBRARY_DIR set: every real claim is UNVERIFIED"}

    def config_view(self, who: Principal) -> dict:
        from dataclasses import asdict
        from cityboymandate.platforms.capabilities import PLATFORMS
        require(who, "read")
        cfg = asdict(self.config.cfg)
        cfg["platform_approvals"] = {k: bool(v) for k, v in cfg["platform_approvals"].items()}   # never expose refs
        return {"config": cfg, "platforms": {k: asdict(v) for k, v in PLATFORMS.items()}}

    def scan_now(self, who: Principal):
        """Run one scan immediately (a reviewer clicked 'Scan now'). Still one at a time; still only reads and drafts."""
        require(who, "review")
        if self.runner is None:
            raise LookupError("monitoring is not configured on this server")
        rep = self.runner.tick(force=True)
        self.audit.record(who.id, "monitor.scan_now", "monitor", "cycle", {"ran": rep is not None})
        return rep

    def emergency_stop(self, who: Principal):
        self.config.emergency_stop(who)
