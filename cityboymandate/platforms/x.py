"""X API v2 read adapter (official API only). Costs money on current plans (pay-per-read), so every call is
budgeted by the monitor cycle. HTTP is injected for tests; `urllib_get` is the real transport."""
from __future__ import annotations
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from cityboymandate.core.models import SocialPost

ALLOWED_HOSTS = {"api.x.com", "graph.facebook.com", "api.anthropic.com", "api.search.brave.com"}


class RateLimited(Exception):
    def __init__(self, reset_epoch: int = 0):
        super().__init__(f"rate limited until {reset_epoch}")
        self.reset_epoch = reset_epoch


class NotFound(Exception):
    pass


def urllib_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 15) -> dict:
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"host not allowed: {host}")
    q = urllib.parse.urlencode(params or {})
    req = urllib.request.Request(url + ("?" + q if q else ""), headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(int(e.headers.get("x-rate-limit-reset", 0) or 0))
        if e.code == 404 or (e.code == 400 and host == "graph.facebook.com"):   # Graph API returns 400 for missing objects
            raise NotFound(url)
        raise


def urllib_post(url: str, body: dict, headers: dict | None = None, timeout: int = 15) -> dict:
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"host not allowed: {host}")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(int(e.headers.get("x-rate-limit-reset", 0) or 0))
        raise


class HTTPFailure(Exception):
    """Non-success reply from a token endpoint: status code and parsed JSON body (e.g. {"error": "invalid_grant"})."""
    def __init__(self, status: int, body: dict | None = None):
        super().__init__(f"HTTP {status}")
        self.status, self.body = status, body or {}


def urllib_post_form(url: str, data: dict, headers: dict | None = None, timeout: int = 15) -> dict:
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"host not allowed: {host}")
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or "{}")
        except ValueError:
            body = {}
        raise HTTPFailure(e.code, body)


def _ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else dt.datetime.now(dt.timezone.utc)


class XSearchClient:
    BASE = "https://api.x.com/2"
    FIELDS = "created_at,lang,conversation_id,referenced_tweets,author_id"

    def __init__(self, http_get, bearer_token: str, meter=None):
        self.get, self._tok, self.meter = http_get, bearer_token, meter

    def _count(self, d: dict):
        if self.meter:
            self.meter.add_reads(len(d.get("data") or []) if isinstance(d.get("data"), list) else (1 if d.get("data") else 0),
                                 len(d.get("includes", {}).get("users", [])))

    def _h(self):
        return {"Authorization": f"Bearer {self._tok}"}

    def _post(self, t: dict, users: dict) -> SocialPost:
        refs = {r["type"]: r["id"] for r in t.get("referenced_tweets", [])}
        h = users.get(t.get("author_id"), "unknown")
        return SocialPost(platform_post_id=t["id"], url=f"https://x.com/{h}/status/{t['id']}", text=t["text"],
                          author_handle="@" + h, created_at=_ts(t.get("created_at")), lang=t.get("lang", "und"),
                          parent_id=refs.get("replied_to"), quoted_id=refs.get("quoted"), platform="x",
                          conversation_id=t.get("conversation_id"))

    def search_recent(self, query: str, since_id: str | None = None, max_results: int = 25):
        p = {"query": query, "max_results": max(10, min(100, max_results)), "tweet.fields": self.FIELDS,
             "expansions": "author_id", "user.fields": "username"}
        if since_id:
            p["since_id"] = since_id
        d = self.get(f"{self.BASE}/tweets/search/recent", p, self._h())
        self._count(d)
        users = {u["id"]: u["username"] for u in d.get("includes", {}).get("users", [])}
        return [self._post(t, users) for t in d.get("data", [])], d.get("meta", {}).get("newest_id")

    def mentions(self, user_id: str, since_id: str | None = None, max_results: int = 25):
        p = {"max_results": max(5, min(100, max_results)), "tweet.fields": self.FIELDS, "expansions": "author_id", "user.fields": "username"}
        if since_id:
            p["since_id"] = since_id
        d = self.get(f"{self.BASE}/users/{user_id}/mentions", p, self._h())
        self._count(d)
        users = {u["id"]: u["username"] for u in d.get("includes", {}).get("users", [])}
        return [self._post(t, users) for t in d.get("data", [])], d.get("meta", {}).get("newest_id")

    def conversation(self, conversation_id: str, max_results: int = 10):
        """Other posts in the same conversation (last 7 days only). Every post returned is a billed read."""
        return self.search_recent(f"conversation_id:{conversation_id}", None, max_results)[0]

    def get_post(self, post_id: str):
        try:
            d = self.get(f"{self.BASE}/tweets/{post_id}", {"tweet.fields": self.FIELDS, "expansions": "author_id", "user.fields": "username"}, self._h())
        except NotFound:
            return None
        self._count(d)
        if "data" not in d:
            return None
        return self._post(d["data"], {u["id"]: u["username"] for u in d.get("includes", {}).get("users", [])})


class XPublisher:
    """Posts ONE reply to ONE interaction, using a user-context token. Only ever called after the publishing
    gate passes. Defence in depth: refuses anything that is not a human-approved reply to a mention."""
    def __init__(self, http_post, token, meter=None):
        """token: a fixed access-token string, or a TokenManager (auto-refreshing)."""
        self.post, self._src, self.meter = http_post, token, meter

    def _token(self, force: bool = False) -> str:
        if hasattr(self._src, "get_token"):
            return self._src.force_refresh() if force else self._src.get_token()
        return self._src

    def publish(self, draft, post):
        if not post.reply_to_id:
            raise ValueError("refusing to post: there is no user interaction to reply to")
        if draft.status != "APPROVED_BY_HUMAN":
            raise ValueError("refusing to post: draft is not human-approved")
        url, body = f"{XSearchClient.BASE}/tweets", {"text": draft.text, "reply": {"in_reply_to_tweet_id": post.reply_to_id}}
        has_url = "http" in draft.text
        if self.meter and not self.meter.can_afford_post(has_url):
            from cityboymandate.service import PublishingNotConfigured
            raise PublishingNotConfigured("today's X spending cap would be exceeded; nothing was posted")
        try:
            r = self.post(url, body, {"Authorization": f"Bearer {self._token()}"})
        except urllib.error.HTTPError as e:
            if e.code == 401 and hasattr(self._src, "force_refresh"):    # token died early: nothing was posted, so one retry is safe
                r = self.post(url, body, {"Authorization": f"Bearer {self._token(force=True)}"})
            else:
                raise
        if self.meter:
            self.meter.add_post(has_url)
        return r
