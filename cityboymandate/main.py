"""Entrypoint. DEMO_MODE=1 (default): fictional sample data, safe to click around.
DEMO_MODE=0: real platform clients from env tokens; evidence retrieval is NullSearch (every claim UNVERIFIED,
research-only) until a real evidence provider is added. Sample evidence is never used on real posts."""
import os
from cityboymandate import sample_data as S
from cityboymandate.api import create_app
from cityboymandate.core.audit import AuditLog
from cityboymandate.core.evidence import EvidenceRegistry
from cityboymandate.core.gate import ConfigStore
from cityboymandate.evidence.judges import CompositeJudge, LLMJudge, RuleJudge, anthropic_complete
from cityboymandate.evidence.library import DocumentLibrary, LibrarySearch
from cityboymandate.evidence.web import CompositeSearch, OfficialWebSearch, brave_search, parse_domains, safe_fetch
from cityboymandate.pipeline.writer import VOICE_DEFAULT, LLMWriter
from cityboymandate.platforms.usage import UsageMeter
from cityboymandate.monitor.cycle import CycleRunner, MetaOwnCommentsDiscoverer, MonitorCycle, StaticDiscoverer, XDiscoverer, InstagramHashtagDiscoverer, XMentionDiscoverer
from cityboymandate.monitor.keywords import KeywordMatcher
from cityboymandate.pipeline.orchestrator import Orchestrator
from cityboymandate.pipeline.providers import MockX, NullJudge, NullSearch, RuleBasedExtractor
from cityboymandate.platforms.meta import MetaGraphClient, MetaPublisher
from cityboymandate.platforms.x import XPublisher, XSearchClient, urllib_get, urllib_post, urllib_post_form
from cityboymandate.platforms.x_auth import TokenManager, TokenStore
from cityboymandate.persist import StateStore
from cityboymandate.service import PublisherRouter, Service

env = os.environ.get
demo = env("DEMO_MODE", "1") == "1"
audit, reg = AuditLog(env("AUDIT_DB", ":memory:")), EvidenceRegistry()
cfg = ConfigStore(audit)
matcher = KeywordMatcher({"demo": ["commission"]}) if demo else KeywordMatcher()   # demo posts are fictional and never mention real names
providers, discoverers, library = {}, [], None
meter = UsageMeter(float(env("X_DAILY_BUDGET_USD", "5")), {k: float(env(e)) for k, e in (("read", "X_COST_READ"), ("user_read", "X_COST_USER_READ"), ("post", "X_COST_POST"), ("post_url", "X_COST_POST_URL")) if env(e)})
writer = None
if env("RESPONDER") == "llm" and env("ANTHROPIC_API_KEY"):        # natural replies, validated; default is the plain template
    writer = LLMWriter(anthropic_complete(urllib_post, env("ANTHROPIC_API_KEY"), env("LLM_MODEL", "claude-sonnet-5-5")), env("VOICE_STYLE") or VOICE_DEFAULT)

if demo:
    x, search, judge = S.build()
    providers = {"x": x, "facebook": MockX([]), "instagram": MockX([])}
    discoverers = [StaticDiscoverer("x", S.POSTS)]
else:
    searches = []
    if env("LIBRARY_DIR"):
        library = DocumentLibrary(env("LIBRARY_DIR"))            # sample/fictional documents are refused here
        for p in library.problems:
            print("LIBRARY PROBLEM:", p)
        searches.append(LibrarySearch(library))
    if env("WEB_EVIDENCE") == "1" and env("BRAVE_API_KEY") and env("EVIDENCE_DOMAINS"):      # live lookups on official sites only
        domains = parse_domains(env("EVIDENCE_DOMAINS"))
        searches.append(OfficialWebSearch(brave_search(urllib_get, env("BRAVE_API_KEY")), lambda u: safe_fetch(u, domains), domains,
                                          daily_search_cap=int(env("SEARCH_DAILY_CAP", "200"))))
    search = CompositeSearch(searches) if searches else NullSearch()
    judge = NullJudge()
    if searches:
        judge = RuleJudge()
        if env("ANTHROPIC_API_KEY"):
            judge = CompositeJudge([RuleJudge(), LLMJudge(anthropic_complete(urllib_post, env("ANTHROPIC_API_KEY"), env("LLM_MODEL", "claude-sonnet-5-5")))])
    if env("X_BEARER_TOKEN"):
        xc = XSearchClient(urllib_get, env("X_BEARER_TOKEN"), meter)
        providers["x"] = xc
        if env("X_BOT_USER_ID") and env("X_BOT_HANDLE"):
            discoverers.append(XMentionDiscoverer(xc, env("X_BOT_USER_ID"), env("X_BOT_HANDLE")))
        if env("X_KEYWORD_SEARCH", "1") == "1":      # 0 = mentions only: by far the cheapest way to run
            discoverers.append(XDiscoverer(xc, matcher, int(env("X_RESULTS_PER_QUERY", "25"))))
    if env("META_ACCESS_TOKEN") and env("META_API_VERSION"):
        mc = MetaGraphClient(lambda u, p: urllib_get(u, p), env("META_ACCESS_TOKEN"), env("META_API_VERSION"))
        from cityboymandate.platforms.meta import MetaPlatformProvider
        providers["facebook"] = MetaPlatformProvider(mc, "facebook")
        providers["instagram"] = MetaPlatformProvider(mc, "instagram")
        for plat, key, owner in (("facebook", "FB_OWN_POST_IDS", "FB_PAGE_ID"), ("instagram", "IG_OWN_MEDIA_IDS", "IG_USER_ID")):
            ids = [i for i in env(key, "").split(",") if i]
            if ids or env(owner):
                discoverers.append(MetaOwnCommentsDiscoverer(mc, plat, ids, env(owner) or None))
        if env("IG_USER_ID") and env("IG_HASHTAGS"):
            discoverers.append(InstagramHashtagDiscoverer(mc, env("IG_USER_ID"), env("IG_HASHTAGS").split(",")))

orch = Orchestrator(providers, search, RuleBasedExtractor(), judge, reg, audit, cfg, writer=writer, thread_limit=0 if demo else int(env("X_THREAD_MAX", "10")))
token_mgr = None
if env("TOKEN_DB") and env("X_CLIENT_ID"):      # auto-refreshing sign-in (run scripts/x_authorize.py once)
    token_mgr = TokenManager(urllib_post_form, env("X_CLIENT_ID"), TokenStore(env("TOKEN_DB")), env("X_CLIENT_SECRET") or None, audit)
publisher = None     # default: drafts only, nothing is ever sent
token_src = token_mgr or env("X_USER_ACCESS_TOKEN")
by_platform = {}
if env("ENABLE_PUBLISHER") == "1":
    if token_src:
        by_platform["x"] = XPublisher(urllib_post, token_src, meter)
    if env("META_ACCESS_TOKEN") and env("META_API_VERSION"):
        by_platform["facebook"] = by_platform["instagram"] = MetaPublisher(urllib_post_form, env("META_ACCESS_TOKEN"), env("META_API_VERSION"))
if by_platform:
    publisher = PublisherRouter(by_platform)   # still behind the gate: nothing sends until an admin enables publishing
store = StateStore(env("STATE_DB")) if env("STATE_DB") else None      # unset = in-memory (demo, free hosts)
service = Service(orch, reg, audit, cfg, publisher=publisher, store=store)
service.library = library
meter.bind(service)
service.meter = meter
service.token_manager = token_mgr
if env("BOOTSTRAP_ADMIN_PASSWORD") and not service.users:
    admin = service.bootstrap_admin("admin", env("BOOTSTRAP_ADMIN_PASSWORD"))
    service.seed_default_rules(admin)
if discoverers:
    cycle = MonitorCycle(service, discoverers, matcher, int(env("MAX_INVESTIGATIONS_PER_CYCLE", "20")))
    interval = int(env("SCAN_INTERVAL_SECONDS", "600"))       # e.g. 3600 = hourly, to keep X API credits small
    service.runner = CycleRunner(cycle, interval_s=interval, min_gap_s=int(interval * 0.8))
    if demo:
        service.runner.tick()          # fill the queue with the fictional sample posts so a fresh start is never empty
    if env("ENABLE_TIMER", "0") == "1":      # on hosts that never sleep; otherwise use /cron/tick
        service.runner.start()
app = create_app(service)
