"""Source policy. An outlet can be downgraded only with a documented, specific reason that is audited
and reversible. There is no reason code for politics, tone, ownership or 'bias', and there are no
pre-loaded bans. Downgraded sources stay stored and visible; they just stop counting as corroboration
and cannot be cited in a public reply."""
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlparse
from cityboymandate.core.audit import AuditLog
from cityboymandate.core.models import Principal, utcnow
from cityboymandate.core.rbac import require
from cityboymandate.core.security import validate_public_url

REASON_CODES = {
    "FABRICATION_DOCUMENTED": "Outlet published a story shown to be fabricated, with evidence",
    "RETRACTED_STORY": "Outlet retracted the specific story being relied on",
    "IMPERSONATION_OF_OUTLET": "Site impersonates a real outlet or institution",
    "NO_EDITORIAL_STANDARDS_DOCUMENTED": "No named editors, corrections process or ownership information",
}
PROTECTED_SUFFIXES = (".gov.ng", ".gov")   # official primary records are contested with other primaries (DISPUTED), not banned


class PolicyError(Exception):
    pass


@dataclass
class SourceEntry:
    domain: str
    status: str            # DOWNGRADED | ACTIVE
    reason_code: str
    explanation: str
    evidence_url: str
    set_by: str
    at: str


def _domain(url_or_domain: str) -> str:
    s = url_or_domain.strip().lower()
    host = urlparse(s if "://" in s else "//" + s).hostname or ""
    return host[4:] if host.startswith("www.") else host


class SourcePolicy:
    def __init__(self, audit: AuditLog):
        self.audit, self.entries = audit, {}

    def is_excluded(self, url: str) -> bool:
        d = _domain(url)
        return any(e.status == "DOWNGRADED" and (d == e.domain or d.endswith("." + e.domain)) for e in self.entries.values())

    def downgrade(self, who: Principal, domain: str, reason_code: str, explanation: str, evidence_url: str) -> SourceEntry:
        require(who, "sources.write")
        d = _domain(domain)
        if reason_code not in REASON_CODES:
            raise PolicyError(f"reason must be one of {sorted(REASON_CODES)}")
        if len(explanation.strip()) < 40:
            raise PolicyError("explain the specific problem in at least 40 characters")
        validate_public_url(evidence_url)            # must point at the evidence (retraction notice, comparison, etc.)
        if not d or d.endswith(PROTECTED_SUFFIXES):
            raise PolicyError("official government or court sources are not downgraded by domain")
        e = SourceEntry(d, "DOWNGRADED", reason_code, explanation.strip(), evidence_url, who.id, utcnow().isoformat())
        self.entries[d] = e
        self.audit.record(who.id, "source.downgrade", "source", d, {"reason": reason_code, "evidence": evidence_url, "explanation": e.explanation})
        return e

    def restore(self, who: Principal, domain: str, note: str) -> None:
        require(who, "sources.write")
        d = _domain(domain)
        if d not in self.entries:
            raise KeyError(d)
        self.entries[d].status = "ACTIVE"
        self.audit.record(who.id, "source.restore", "source", d, {"note": note})

    def listing(self) -> list[SourceEntry]:
        return list(self.entries.values())
