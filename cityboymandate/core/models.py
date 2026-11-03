"""Domain models. Verdicts are stored per claim, never per conversation."""
from __future__ import annotations
import datetime as dt
import enum
import uuid
from dataclasses import dataclass, field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Verdict(str, enum.Enum):
    SUPPORTED = "SUPPORTED"
    MOSTLY_SUPPORTED = "MOSTLY_SUPPORTED"
    MISLEADING = "MISLEADING"
    MOSTLY_FALSE = "MOSTLY_FALSE"
    FALSE = "FALSE"
    UNVERIFIED = "UNVERIFIED"
    DISPUTED = "DISPUTED"
    OUTDATED = "OUTDATED"
    NOT_FACT_CHECKABLE = "NOT_FACT_CHECKABLE"


# Only these verdicts may ever produce a public correction draft.
CORRECTABLE = {Verdict.FALSE, Verdict.MOSTLY_FALSE, Verdict.MISLEADING, Verdict.OUTDATED}


class ReviewOutcome(str, enum.Enum):
    APPROVE_FOR_HUMAN_REVIEW = "APPROVE_FOR_HUMAN_REVIEW"
    REVISE = "REVISE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class Relation(str, enum.Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    OUTDATES = "OUTDATES"
    MISSING_CONTEXT = "MISSING_CONTEXT"


class Stage(str, enum.Enum):
    DISCOVERED = "DISCOVERED"
    CONTEXT_BUILT = "CONTEXT_BUILT"
    CLAIMS_EXTRACTED = "CLAIMS_EXTRACTED"
    RESEARCHED = "RESEARCHED"
    ANALYSED = "ANALYSED"
    DRAFTED = "DRAFTED"
    REVIEWED = "REVIEWED"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    HELD_FOR_HUMAN = "HELD_FOR_HUMAN"      # reviewer flagged a limitation: a person must look before anything is sent
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"
    FAILED = "FAILED"


class Role(str, enum.Enum):
    VIEWER = "VIEWER"
    ANALYST = "ANALYST"
    REVIEWER = "REVIEWER"
    ADMIN = "ADMIN"
    AGENT = "AGENT"   # the pipeline; can never review, publish, or change settings


@dataclass(frozen=True)
class Principal:
    id: str
    role: Role


@dataclass
class SocialPost:
    platform_post_id: str
    url: str
    text: str
    author_handle: str
    created_at: dt.datetime
    lang: str = "en"
    parent_id: str | None = None
    quoted_id: str | None = None
    conversation_id: str | None = None
    limitations: list[str] = field(default_factory=list)
    platform: str = "x"          # x | facebook | instagram
    own_asset: bool = False     # True only for comments on Pages/accounts we operate
    invoked_by: str | None = None   # handle of the person who @-mentioned us to ask about this post
    reply_to_id: str | None = None  # the mention/interaction our reply answers (never the original author unprompted)


@dataclass
class MonitoringRule:
    pattern: str
    kind: str                   # exact | semantic
    category: str
    owner: str
    purpose: str
    platforms: list[str] = field(default_factory=lambda: ["x"])
    active: bool = True
    created_at: dt.datetime = field(default_factory=utcnow)
    id: str = field(default_factory=lambda: new_id("rul"))


@dataclass
class ExtractedClaim:
    text: str
    kind: str                 # factual | interpretive | predictive | opinion
    checkable: bool
    category: str = "general"
    evidence_needed: str = ""
    id: str = field(default_factory=lambda: new_id("clm"))


@dataclass
class EvidenceSource:
    title: str
    publisher: str
    url: str
    source_type: str
    primary: bool
    content: str
    retrieved_at: dt.datetime
    published: str | None = None
    derived_from: str | None = None   # root URL if this merely repeats another source
    limitations: str = ""
    content_sha256: str = ""
    id: str = field(default_factory=lambda: new_id("src"))


@dataclass
class EvidencePassage:
    source_id: str
    claim_id: str
    text: str
    relation: Relation
    id: str = field(default_factory=lambda: new_id("psg"))


@dataclass
class ClaimVerdict:
    claim_id: str
    verdict: Verdict
    rationale: str
    passage_ids: list[str]
    confidence_basis: dict[str, str]   # structured reasons, deliberately not a bare number
    id: str = field(default_factory=lambda: new_id("vrd"))


@dataclass
class DraftResponse:
    investigation_id: str
    claim_id: str
    text: str
    source_urls: list[str]
    status: str = "DRAFT"
    id: str = field(default_factory=lambda: new_id("drf"))


@dataclass
class HumanReview:
    draft_id: str
    reviewer_id: str
    reviewer_role: Role
    decision: str             # APPROVE | REJECT | REQUEST_RESEARCH
    note: str = ""
    at: dt.datetime = field(default_factory=utcnow)


@dataclass
class Investigation:
    post: SocialPost
    thread: list[SocialPost] = field(default_factory=list)
    claims: list[ExtractedClaim] = field(default_factory=list)
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    drafts: list[DraftResponse] = field(default_factory=list)
    reviewer_outcomes: dict[str, ReviewOutcome] = field(default_factory=dict)
    safety_flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stage: Stage = Stage.DISCOVERED
    language: str = "en"
    tone: str = "UNSCORED"          # CRITICAL | NEUTRAL | SUPPORTIVE: queue ordering only, never a verdict input
    priority: int = 0
    matched_keywords: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("inv"))
