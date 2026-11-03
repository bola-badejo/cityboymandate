"""Spend guard for pay-per-use X credits. ESTIMATES only (the X console is the source of truth), deliberately pessimistic:
every post returned counts as a read, every user object returned beside it counts as a user read, and a reply containing a
link is priced at the highest figure published ($0.20). Confirm real prices in your console and override with env vars."""
from __future__ import annotations
import datetime as dt

DEFAULT_COSTS = {"read": 0.005, "user_read": 0.010, "post": 0.015, "post_url": 0.20}


class UsageMeter:
    def __init__(self, daily_cap_usd: float = 5.0, costs: dict | None = None, today=dt.date.today):
        self.cap, self.costs, self.today, self.state = daily_cap_usd, {**DEFAULT_COSTS, **(costs or {})}, today, {}

    def bind(self, svc) -> None:
        """Keep the counters in the service's saved state so a restart does not reset the day's spend."""
        self.state = svc.extra.setdefault("usage", {})

    def _day(self) -> dict:
        key = self.today().isoformat()
        for old in sorted(self.state)[:-14]:
            self.state.pop(old, None)
        return self.state.setdefault(key, {"reads": 0, "user_reads": 0, "posts": 0, "posts_url": 0})

    def add_reads(self, posts: int, users: int = 0) -> None:
        d = self._day()
        d["reads"] += posts
        d["user_reads"] += users

    def add_post(self, has_url: bool) -> None:
        self._day()["posts_url" if has_url else "posts"] += 1

    def spent_today(self) -> float:
        d, c = self._day(), self.costs
        return round(d["reads"] * c["read"] + d["user_reads"] * c["user_read"] + d["posts"] * c["post"] + d["posts_url"] * c["post_url"], 4)

    def exceeded(self) -> bool:
        return self.spent_today() >= self.cap

    def can_afford_post(self, has_url: bool) -> bool:
        return self.spent_today() + self.costs["post_url" if has_url else "post"] <= self.cap

    def summary(self) -> dict:
        return {"spent_today_usd": self.spent_today(), "cap_usd": self.cap, "estimate": True, **self._day()}
