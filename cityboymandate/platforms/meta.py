"""Meta (Facebook/Instagram) read adapters. Official Graph API only: no scraping, no browser automation.
HTTP is injected (`http_get(url, params) -> dict`) so this is testable offline and the token never
appears in URLs or logs. There is deliberately NO publishing code here: replies stay draft-only until the
gate, Meta App Review, and a real Publisher implementation exist."""
from __future__ import annotations
import datetime as dt
from cityboymandate.core.models import SocialPost


class HashtagBudgetExceeded(Exception):
    pass


class HashtagBudget:
    """Instagram allows 30 unique hashtags per rolling 7 days; re-querying a tag in-window is free."""
    def __init__(self, limit: int = 30, window_days: int = 7):
        self.limit, self.window = limit, dt.timedelta(days=window_days)
        self.first_used: dict[str, dt.datetime] = {}

    def use(self, tag: str, now: dt.datetime) -> None:
        tag = tag.lower().lstrip("#")
        self.first_used = {t: ts for t, ts in self.first_used.items() if now - ts < self.window}
        if tag in self.first_used:
            return
        if len(self.first_used) >= self.limit:
            raise HashtagBudgetExceeded(f"{self.limit} unique hashtags already used in the last 7 days")
        self.first_used[tag] = now


def _ts(s: str | None) -> dt.datetime:
    if not s:
        return dt.datetime.now(dt.timezone.utc)
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace("+0000", "+00:00"))


class MetaGraphClient:
    def __init__(self, http_get, access_token: str, api_version: str, budget: HashtagBudget | None = None,
                 clock=lambda: dt.datetime.now(dt.timezone.utc)):
        # api_version is required (no default) so it is set deliberately to a currently supported version.
        self.get, self.token, self.v = http_get, access_token, api_version
        self.budget, self.clock = budget or HashtagBudget(), clock
        self.base = "https://graph.facebook.com"

    def _call(self, path: str, **params) -> dict:
        return self.get(f"{self.base}/{self.v}/{path}", {**params, "access_token": self.token})

    def own_comments(self, platform: str, object_id: str) -> list[SocialPost]:
        """Comments on a Page post / Instagram media that WE operate. These are reply-eligible."""
        assert platform in ("facebook", "instagram")
        data = self._call(f"{object_id}/comments", fields="id,message,text,from,username,timestamp,created_time")
        out = []
        for c in data.get("data", []):
            handle = c.get("username") or (c.get("from") or {}).get("name") or "unknown"
            out.append(SocialPost(
                platform_post_id=c["id"], url=f"https://www.{platform}.com/{c['id']}",
                text=c.get("message") or c.get("text") or "", author_handle=handle,
                created_at=_ts(c.get("timestamp") or c.get("created_time")), parent_id=None,
                platform=platform, own_asset=True))
        return out

    def own_recent_object_ids(self, platform: str, owner_id: str, limit: int = 10) -> list[str]:
        """Latest posts of the Page / media of the Instagram account WE operate, so their comments can be read."""
        d = self._call(f"{owner_id}/{'posts' if platform == 'facebook' else 'media'}", fields="id", limit=limit)
        return [x["id"] for x in d.get("data", [])]

    def instagram_hashtag_recent(self, ig_user_id: str, tag: str) -> list[SocialPost]:
        """Read-only discovery. Results are NEVER reply-eligible (own_asset=False) and carry no username."""
        self.budget.use(tag, self.clock())
        hid = self._call("ig_hashtag_search", user_id=ig_user_id, q=tag.lstrip("#"))["data"][0]["id"]
        data = self._call(f"{hid}/recent_media", user_id=ig_user_id, fields="id,caption,permalink,timestamp,media_type")
        return [SocialPost(
            platform_post_id=m["id"], url=m.get("permalink", ""), text=m.get("caption", ""),
            author_handle="(not provided by API)", created_at=_ts(m.get("timestamp")),
            limitations=["Instagram hashtag search: author not available; reply not permitted"],
            platform="instagram", own_asset=False) for m in data.get("data", [])]


class MetaPlatformProvider:
    """get_post() for an object on an account we operate; None if it was deleted or is inaccessible."""
    def __init__(self, client: MetaGraphClient, platform: str):
        self.c, self.platform = client, platform

    def get_post(self, post_id: str):
        from cityboymandate.platforms.x import NotFound
        try:
            c = self.c._call(post_id, fields="id,message,text,from,username,timestamp,created_time")
        except NotFound:
            return None
        if "id" not in c:
            return None
        return SocialPost(platform_post_id=c["id"], url=f"https://www.{self.platform}.com/{c['id']}",
                          text=c.get("message") or c.get("text") or "", author_handle=c.get("username") or (c.get("from") or {}).get("name") or "unknown",
                          created_at=_ts(c.get("timestamp") or c.get("created_time")), platform=self.platform, own_asset=True)


class MetaPublisher:
    """Replies to ONE comment on a Page / Instagram account we operate. Only called after the publishing gate passes.
    The token goes in the request body, never in the URL. Defence in depth: refuses anything that is not a human-approved
    reply to a comment on our own asset."""
    def __init__(self, http_form, access_token: str, api_version: str):
        self.post, self._tok, self.v = http_form, access_token, api_version

    def publish(self, draft, post):
        if post.platform not in ("facebook", "instagram"):
            raise ValueError("MetaPublisher only handles Facebook and Instagram")
        if not post.own_asset:
            raise ValueError("refusing to post: this is not a comment on an account we operate")
        if draft.status != "APPROVED_BY_HUMAN":
            raise ValueError("refusing to post: draft is not human-approved")
        edge = "comments" if post.platform == "facebook" else "replies"
        return self.post(f"https://graph.facebook.com/{self.v}/{post.platform_post_id}/{edge}",
                         {"message": draft.text, "access_token": self._tok}, {})
