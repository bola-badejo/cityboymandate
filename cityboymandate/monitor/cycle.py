"""The 10-minute monitoring cycle. Discovery -> dedupe -> keyword + tone -> budgeted investigation.
Neutrality safeguard: tone orders the queue, but a fixed share of every cycle's budget is reserved for
non-critical posts, so false claims that FLATTER the government are checked too."""
from __future__ import annotations
import dataclasses
import math
import re
import random
import threading
import time
from dataclasses import dataclass, field
from cityboymandate.core.models import Principal, Role, utcnow
from cityboymandate.monitor.keywords import KeywordMatcher
from cityboymandate.monitor.tone import score_tone
from cityboymandate.platforms.x import RateLimited

SYSTEM = Principal("monitor", Role.AGENT)
OPTOUT = re.compile(r"^\W*(stop|unsubscribe|opt[ -]?out)\W*$", re.I)
PRIORITY = {"INVOKED": 3, "CRITICAL": 2, "NEUTRAL": 1, "SUPPORTIVE": 1, "UNSCORED": 0}


@dataclass
class CycleReport:
    at: str
    fetched: int = 0
    new: int = 0
    investigated: int = 0
    deferred: int = 0
    est_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    by_tone: dict = field(default_factory=dict)
    skipped: str = ""


class XDiscoverer:
    platform = "x"
    def __init__(self, client, matcher: KeywordMatcher, per_query: int = 25):
        self.c, self.m, self.n, self.cursor = client, matcher, per_query, None
    def discover(self):
        out, newest = [], self.cursor
        for q in self.m.x_queries():
            posts, nid = self.c.search_recent(q, self.cursor, self.n)
            out += posts
            newest = max(filter(None, [newest, nid]), key=int, default=newest)
        self.cursor = newest
        return out


class XMentionDiscoverer:
    """People who @-mention OUR account asking about a post. Each mention is one user interaction and gets at most
    one reply, posted under the mention. The claim checked is in the post they replied to (or the mention itself)."""
    platform = "x"
    def __init__(self, client, bot_user_id: str, bot_handle: str, per_call: int = 25):
        self.c, self.uid, self.handle, self.n, self.cursor = client, bot_user_id, bot_handle.lstrip("@"), per_call, None
    def discover(self):
        mentions, nid = self.c.mentions(self.uid, self.cursor, self.n)
        self.cursor = nid or self.cursor
        out = []
        for m in mentions:
            if m.author_handle.lstrip("@").lower() == self.handle.lower():
                continue                                          # never answer ourselves
            parent = self.c.get_post(m.parent_id) if m.parent_id else None
            if m.parent_id and parent is None:
                continue                                          # the post asked about was deleted
            if parent is not None and parent.author_handle.lstrip("@").lower() != self.handle.lower():
                target = parent
            else:                                                 # a direct question, or a follow-up to one of our replies
                target = m
                target.text = re.sub("@" + re.escape(self.handle), "", m.text, flags=re.I).strip()
            target = dataclasses.replace(target, limitations=list(target.limitations),   # never mutate a cached object
                                         invoked_by=m.author_handle, reply_to_id=m.platform_post_id)
            out.append(target)
        return out


class MetaOwnCommentsDiscoverer:
    """Comments on Pages / Instagram media WE operate. These are the only reply-eligible Meta posts."""
    def __init__(self, client, platform: str, object_ids: list[str], owner_id: str | None = None, recent: int = 10):
        self.platform, self.c, self.ids, self.owner, self.recent = platform, client, list(object_ids), owner_id, recent
    def discover(self):
        ids = list(self.ids)
        if self.owner:
            ids += [i for i in self.c.own_recent_object_ids(self.platform, self.owner, self.recent) if i not in ids]
        return [p for oid in ids for p in self.c.own_comments(self.platform, oid)]


class InstagramHashtagDiscoverer:
    """Read-only discovery; never reply-eligible; limited to 30 unique hashtags / 7 days (enforced in client)."""
    platform = "instagram"
    def __init__(self, client, ig_user_id: str, hashtags: list[str]):
        self.c, self.u, self.tags = client, ig_user_id, hashtags
    def discover(self):
        return [p for t in self.tags for p in self.c.instagram_hashtag_recent(self.u, t)]


class StaticDiscoverer:
    def __init__(self, platform, posts):
        self.platform, self.posts = platform, list(posts)
    def discover(self):
        out, self.posts = self.posts, []
        return out


class MonitorCycle:
    def __init__(self, service, discoverers: list, matcher: KeywordMatcher | None = None, max_investigations: int = 20,
                 reserve_non_critical: float = 0.3, cost_per_read: float = 0.005, max_invoked: int = 30):
        self.svc, self.ds, self.m = service, discoverers, matcher or KeywordMatcher()
        self.budget, self.reserve, self.cost, self.max_invoked = max_investigations, reserve_non_critical, cost_per_read, max_invoked
        self.seen: set[tuple] = {tuple(k) for k in service.extra.get("seen", [])}
        for d in discoverers:
            if hasattr(d, "cursor"):
                d.cursor = service.extra.get("cursors", {}).get(type(d).__name__, d.cursor)
        self.last: CycleReport | None = None

    def _select(self, cands):
        crit = [c for c in cands if c[0] == "CRITICAL"]
        other = [c for c in cands if c[0] != "CRITICAL"]
        n_other = min(len(other), math.ceil(self.budget * self.reserve))
        n_crit = min(len(crit), self.budget - n_other)
        n_other = min(len(other), self.budget - n_crit)          # unused critical slots flow to the others
        return crit[:n_crit] + other[:n_other], len(cands) - n_crit - n_other

    def run_once(self) -> CycleReport:
        rep = CycleReport(at=utcnow().isoformat())
        if self.svc.config.cfg.halted:
            rep.skipped = "system halted"
            self.last = rep
            return rep
        meter = getattr(self.svc, "meter", None)
        if meter is not None and meter.exceeded():
            rep.skipped = f"daily X spending cap reached (~${meter.spent_today()} of ${meter.cap}); resumes tomorrow"
            self.last = rep
            return rep
        tm = getattr(self.svc, "token_manager", None)
        if tm is not None and tm.status() != "not_authorized":
            try:
                tm.get_token()                    # keeps the X sign-in warm and finds a revoked grant within minutes, not weeks
            except Exception as e:
                rep.errors.append(f"x sign-in: {e}")
        cands = []
        for d in self.ds:
            try:
                posts = d.discover()
            except RateLimited as e:
                rep.errors.append(f"{d.platform}: rate limited until {e.reset_epoch}")
                continue
            except Exception as e:
                rep.errors.append(f"{d.platform}: {e!r}")
                continue
            rep.fetched += len(posts)
            disclosure = self.svc.config.cfg.disclosure_text
            own_ids = set(self.svc.extra.get("own_reply_ids", []))
            for p in posts:
                if p.platform_post_id in own_ids or (disclosure and disclosure.split(".")[0] in p.text):
                    continue                                   # our own reply: never answer ourselves (prevents reply loops)
                if OPTOUT.match(re.sub(r"@\w+", "", p.text)):
                    self.svc.record_opt_out(p.platform, p.author_handle)       # someone said STOP: honoured immediately
                    continue
                key = (p.platform, p.platform_post_id, p.reply_to_id)
                if key in self.seen:
                    continue
                self.seen.add(key)
                hits = self.m.match(p.text) if not p.own_asset else self.m.match(p.text) or ["(own page comment)"]
                if p.invoked_by:
                    cands.append(("INVOKED", p, ["(mention)"]))
                    continue
                if not hits and p.platform == "x":
                    continue
                t = score_tone(p.text)
                cands.append((t.label, p, hits))
        rep.new = len(cands)
        asked = [x for x in cands if x[0] == "INVOKED"][: self.max_invoked]       # someone asked: always answered first
        chosen, rep.deferred = self._select([x for x in cands if x[0] != "INVOKED"])
        chosen = asked + chosen
        for label, p, hits in chosen:
            try:
                inv = self.svc.investigate(SYSTEM, post=p)
                inv.tone, inv.priority, inv.matched_keywords = label, PRIORITY[label], hits
                rep.investigated += 1
                rep.by_tone[label] = rep.by_tone.get(label, 0) + 1
            except Exception as e:
                rep.errors.append(f"investigate {p.platform}:{p.platform_post_id}: {e!r}")
        rep.est_cost_usd = round(meter.spent_today(), 4) if meter is not None else round(rep.fetched * self.cost, 4)
        self.svc.extra["seen"] = [list(k) for k in self.seen][-5000:]
        self.svc.extra["cursors"] = {type(d).__name__: d.cursor for d in self.ds if hasattr(d, "cursor")}
        self.svc.audit.record(SYSTEM.id, "monitor.cycle", "monitor", "cycle", rep.__dict__)
        self.svc._save()
        self.last = rep
        return rep


class CycleRunner:
    """Runs cycles on a timer thread AND/OR when an external cron calls tick(). A lock plus a minimum gap
    prevents overlap and double-billing when both are active (free hosts sleep, so cron is the reliable trigger)."""
    def __init__(self, cycle: MonitorCycle, interval_s: int = 600, min_gap_s: int = 480):
        self.cycle, self.interval, self.gap = cycle, interval_s, min_gap_s
        self.lock, self.last_run, self._stop = threading.Lock(), 0.0, threading.Event()

    def tick(self, now=time.monotonic, force=False):
        if not self.lock.acquire(blocking=False):
            return None
        try:
            if not force and self.last_run and now() - self.last_run < self.gap:
                return None
            self.last_run = now()
            return self.cycle.run_once()
        finally:
            self.lock.release()

    def start(self):
        def loop():
            while not self._stop.wait(self.interval + random.uniform(-30, 30)):
                try:
                    self.tick()
                except Exception:
                    pass
        threading.Thread(target=loop, daemon=True, name="monitor-cycle").start()

    def stop(self):
        self._stop.set()
