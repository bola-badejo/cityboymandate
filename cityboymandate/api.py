"""Thin FastAPI layer over Service. NOT TESTED in the authoring environment (no network to install
fastapi); only syntax-checked. Run the service-level tests, then smoke-test these routes locally."""
from __future__ import annotations
from dataclasses import asdict
import hmac
import os
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from cityboymandate.core.models import Principal, Role
from cityboymandate.core.rbac import PERMISSIONS, PermissionDenied
from cityboymandate.service import AuthError, PublishingNotConfigured, Service

bearer = HTTPBearer(auto_error=False)


class Login(BaseModel):
    username: str
    password: str


class NewUser(BaseModel):
    username: str
    password: str = Field(min_length=12)
    role: Role


class InvestigateReq(BaseModel):
    post_id: str
    platform: str = "x"


class ReviewReq(BaseModel):
    decision: str
    note: str = ""


class DowngradeReq(BaseModel):
    domain: str
    reason_code: str
    explanation: str
    evidence_url: str


class RuleReq(BaseModel):
    pattern: str
    kind: str = "exact"
    category: str = "general"
    purpose: str
    platforms: list[str] = ["x"]


def create_app(svc: Service) -> FastAPI:
    app = FastAPI(title="@cityboymandate", version="0.2.0")

    def me(c: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
        if c is None:
            raise HTTPException(401, "missing bearer token")
        try:
            return svc.principal_from_token(c.credentials)
        except AuthError as e:
            raise HTTPException(401, str(e))

    def guarded(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except PermissionDenied as e:
            raise HTTPException(403, str(e))
        except PublishingNotConfigured as e:
            raise HTTPException(503, str(e))
        except (KeyError, LookupError) as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/health")
    def health():
        return {"status": "ok", "publishing_enabled": svc.config.cfg.publishing_enabled,
                "halted": svc.config.cfg.halted, "audit_chain_intact": svc.audit.verify(), "monitor": svc.runner is not None,
                "x_auth": svc.token_manager.status() if svc.token_manager else "static_or_unconfigured",
                "x_usage": svc.meter.summary() if svc.meter else None}

    @app.post("/auth/login")
    def login(b: Login):
        try:
            return {"access_token": svc.login(b.username, b.password), "token_type": "bearer"}
        except AuthError:
            raise HTTPException(401, "invalid credentials")

    @app.post("/users")
    def new_user(b: NewUser, p: Principal = Depends(me)):
        u = guarded(svc.create_user, p, b.username, b.password, b.role)
        return {"id": u.id, "role": u.role}

    @app.post("/rules")
    def new_rule(b: RuleReq, p: Principal = Depends(me)):
        return asdict(guarded(svc.create_rule, p, **b.model_dump()))

    @app.post("/rules/{rule_id}/pause")
    def pause(rule_id: str, p: Principal = Depends(me)):
        return asdict(guarded(svc.set_rule_active, p, rule_id, False))

    @app.post("/investigations")
    def investigate(b: InvestigateReq, p: Principal = Depends(me)):
        return asdict(guarded(svc.investigate, p, b.post_id, b.platform))

    @app.get("/investigations/queue")
    def queue(p: Principal = Depends(me)):
        return [asdict(i) for i in guarded(svc.queue, p)]

    @app.post("/drafts/{draft_id}/review")
    def review(draft_id: str, b: ReviewReq, p: Principal = Depends(me)):
        return asdict(guarded(svc.review, p, draft_id, b.decision, b.note))

    @app.post("/drafts/{draft_id}/publish")
    def publish(draft_id: str, p: Principal = Depends(me)):
        d = guarded(svc.attempt_publish, p, draft_id)
        if not d.allowed:
            raise HTTPException(409, {"allowed": False, "reasons": d.reasons})
        return {"allowed": True}

    @app.get("/rules")
    def rules(p: Principal = Depends(me)):
        return [asdict(r) for r in guarded(svc.list_rules, p)]

    @app.post("/rules/{rule_id}/resume")
    def resume(rule_id: str, p: Principal = Depends(me)):
        return asdict(guarded(svc.set_rule_active, p, rule_id, True))

    @app.get("/evidence/claim/{claim_id}")
    def evidence(claim_id: str, p: Principal = Depends(me)):
        return guarded(svc.evidence_for_claim, p, claim_id)

    @app.get("/drafts/{draft_id}/gate")
    def gate(draft_id: str, p: Principal = Depends(me)):
        d = guarded(svc.gate_preview, p, draft_id)
        return {"allowed": d.allowed, "reasons": d.reasons}

    @app.get("/library")
    def library(p: Principal = Depends(me)):
        return guarded(svc.library_status, p)

    @app.get("/config")
    def config(p: Principal = Depends(me)):
        return guarded(svc.config_view, p)

    @app.post("/cron/tick")
    def cron_tick(x_cron_secret: str = Header(default="")):
        """Called every ~10 min by an external free cron (hosts that sleep cannot run their own timer).
        Protected by a shared secret; a lock + minimum gap in the runner prevents double runs and double billing."""
        secret = os.environ.get("CRON_SECRET", "")
        if not secret or svc.runner is None:
            raise HTTPException(404)
        if not hmac.compare_digest(secret.encode(), x_cron_secret.encode()):
            raise HTTPException(401, "bad cron secret")
        rep = svc.runner.tick()
        return {"ran": rep is not None, "report": rep.__dict__ if rep else None}

    @app.post("/monitor/scan")
    def monitor_scan(p: Principal = Depends(me)):
        rep = guarded(svc.scan_now, p)
        return {"ran": rep is not None, "report": rep.__dict__ if rep else None}

    @app.get("/monitor/status")
    def monitor_status(p: Principal = Depends(me)):
        last = svc.runner.cycle.last if svc.runner else None
        return {"configured": svc.runner is not None, "last_cycle": last.__dict__ if last else None}

    @app.get("/sources")
    def sources(p: Principal = Depends(me)):
        return [asdict(s) for s in guarded(svc.list_sources, p)]

    @app.post("/sources/downgrade")
    def downgrade(b: DowngradeReq, p: Principal = Depends(me)):
        from cityboymandate.core.sources import PolicyError
        try:
            return asdict(guarded(svc.downgrade_source, p, b.domain, b.reason_code, b.explanation, b.evidence_url))
        except PolicyError as e:
            raise HTTPException(422, str(e))

    @app.post("/admin/emergency-stop")
    def stop(p: Principal = Depends(me)):
        guarded(svc.emergency_stop, p)
        return {"halted": True}

    @app.get("/audit")
    def audit(p: Principal = Depends(me)):
        if "read" not in PERMISSIONS[p.role]:
            raise HTTPException(403)
        return svc.audit.events()

    dash = os.path.join(os.path.dirname(__file__), "..", "dashboard")
    has_dash = os.path.isdir(dash)
    if has_dash:
        app.mount("/dashboard", StaticFiles(directory=dash, html=True), name="dashboard")
    else:
        @app.get("/dashboard/")
        def no_dashboard():
            raise HTTPException(404, "The server is running but the 'dashboard' folder was not uploaded to GitHub next to 'cityboymandate'. Upload it and Render will redeploy.")

    @app.get("/")
    def root():
        """The plain address: send people to the dashboard instead of a bare 404."""
        if has_dash:
            return RedirectResponse("/dashboard/")
        return {"status": "api running", "dashboard": "NOT DEPLOYED: upload the dashboard folder", "health": "/health", "api_docs": "/docs"}

    return app
