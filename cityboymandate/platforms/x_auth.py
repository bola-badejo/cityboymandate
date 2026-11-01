"""X OAuth 2.0 user-token management (authorization code + PKCE, then refresh).
Facts that shape the design (X docs, checked 2026-10-27): access tokens last ~2 hours; a refresh token is only issued
with the offline.access scope; EVERY refresh returns a NEW refresh token and the old one stops working. So:
  * the new tokens are written to disk BEFORE the access token is used or returned (crash-safe);
  * refreshes are serialised (one at a time) and compare-and-swap against the stored refresh token;
  * a revoked/expired grant is recorded as needs_reauth, retries stop, and a person is told. No retry storms.
Run ONE server process (no --workers): two processes racing a rotating refresh token will fight over it.
Tokens are never logged, never put in the audit log, and the store file is chmod 600."""
from __future__ import annotations
import base64
import hashlib
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse
from cityboymandate.platforms.x import HTTPFailure
from cityboymandate.service import PublishingNotConfigured

TOKEN_URL = "https://api.x.com/2/oauth2/token"
AUTH_URL = "https://x.com/i/oauth2/authorize"
SCOPES = "tweet.read tweet.write users.read offline.access"


class TokenUnavailable(PublishingNotConfigured):
    pass


class TokenStore:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)     # autocommit; we BEGIN explicitly
        self.db.execute("CREATE TABLE IF NOT EXISTS tokens(id INTEGER PRIMARY KEY CHECK(id=1), access TEXT NOT NULL, "
                        "refresh TEXT NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'ok')")
        if path != ":memory:":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def load(self):
        r = self.db.execute("SELECT access, refresh, expires_at, status FROM tokens WHERE id=1").fetchone()
        return dict(zip(("access", "refresh", "expires_at", "status"), r)) if r else None

    def save(self, access: str, refresh: str, expires_at: float) -> None:
        """Unconditional write (used after a person re-authorizes). Clears needs_reauth."""
        self.db.execute("INSERT OR REPLACE INTO tokens(id, access, refresh, expires_at, status) VALUES (1, ?, ?, ?, 'ok')", (access, refresh, expires_at))

    def cas(self, old_refresh: str, access: str, refresh: str, expires_at: float) -> bool:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            n = self.db.execute("UPDATE tokens SET access=?, refresh=?, expires_at=?, status='ok' WHERE id=1 AND refresh=?",
                                (access, refresh, expires_at, old_refresh)).rowcount
            self.db.execute("COMMIT")
            return n == 1
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def set_status(self, status: str, only_if_refresh: str) -> None:
        self.db.execute("UPDATE tokens SET status=? WHERE id=1 AND refresh=?", (status, only_if_refresh))


class TokenManager:
    def __init__(self, http_form, client_id: str, store: TokenStore, client_secret: str | None = None, audit=None,
                 clock=time.time, sleep=time.sleep, skew: int = 300, retries: int = 3):
        self.http, self.cid, self.secret, self.store, self.audit = http_form, client_id, client_secret, store, audit
        self.clock, self.sleep, self.skew, self.retries = clock, sleep, skew, retries
        self._lock, self._degraded = threading.Lock(), False

    def __repr__(self):
        return "TokenManager(<redacted>)"

    def _audit(self, action: str, detail: dict | None = None):
        if self.audit:
            self.audit.record("x-auth", action, "x_auth", "token", detail or {})

    def status(self) -> str:
        st = self.store.load()
        if not st:
            return "not_authorized"
        if st["status"] == "needs_reauth":
            return "needs_reauth"
        return "degraded" if self._degraded else "ok"

    def get_token(self) -> str:
        return self._get(False)

    def force_refresh(self) -> str:
        return self._get(True)

    def _get(self, force: bool) -> str:
        with self._lock:
            st = self.store.load()                      # always re-read: another process or the authorize script may have updated it
            if not st:
                raise TokenUnavailable("X is not authorized yet: run scripts/x_authorize.py")
            if st["status"] == "needs_reauth":
                raise TokenUnavailable("X authorization was revoked or expired: run scripts/x_authorize.py again")
            if not force and st["expires_at"] - self.clock() > self.skew:
                self._degraded = False
                return st["access"]
            return self._refresh(st)

    def _refresh(self, st: dict) -> str:
        form = {"grant_type": "refresh_token", "refresh_token": st["refresh"], "client_id": self.cid}
        headers = {}
        if self.secret:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
        resp, delay = None, 1
        for attempt in range(self.retries):
            try:
                resp = self.http(TOKEN_URL, form, headers)
                break
            except HTTPFailure as e:
                if e.status in (400, 401):              # permanent: grant revoked, expired or already rotated
                    cur = self.store.load()
                    if cur and cur["refresh"] != st["refresh"] and cur["expires_at"] > self.clock():
                        return cur["access"]            # someone else refreshed first; use theirs
                    self.store.set_status("needs_reauth", st["refresh"])
                    self._audit("x.auth.needs_reauth", {"http_status": e.status, "error": str((e.body or {}).get("error", ""))[:60]})
                    raise TokenUnavailable("X rejected the refresh token: run scripts/x_authorize.py again")
            except OSError:
                pass                                    # network trouble: transient
            if attempt < self.retries - 1:
                self.sleep(delay)
                delay *= 2
        if resp is None or "access_token" not in resp:
            self._degraded = True
            self._audit("x.auth.refresh_failed_temporarily", {})
            raise TokenUnavailable("could not refresh the X token right now; it will be retried on the next request")
        access = resp["access_token"]
        refresh = resp.get("refresh_token") or st["refresh"]
        exp = self.clock() + int(resp.get("expires_in", 7200))
        if not self.store.cas(st["refresh"], access, refresh, exp):       # the new refresh token is on disk before we hand out the access token
            cur = self.store.load()
            if cur and cur["expires_at"] > self.clock():
                return cur["access"]
            self.store.save(access, refresh, exp)
        self._degraded = False
        self._audit("x.token_refreshed", {"expires_in": int(resp.get("expires_in", 7200))})
        return access


# ---- one-time authorization (authorization code + PKCE), used by scripts/x_authorize.py
def make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str, scopes: str = SCOPES) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
                                                    "scope": scopes, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def parse_redirect(url: str, state: str) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url.strip()).query)
    if q.get("state", [None])[0] != state:
        raise ValueError("state mismatch: this redirect did not come from the sign-in you started")
    if "error" in q:
        raise ValueError(f"X returned an error: {q['error'][0]}")
    if "code" not in q:
        raise ValueError("no authorization code in that URL")
    return q["code"][0]


def exchange_code(http_form, client_id: str, client_secret: str | None, redirect_uri: str, code: str, verifier: str) -> dict:
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier, "client_id": client_id}
    headers = {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()} if client_secret else {}
    r = http_form(TOKEN_URL, form, headers)
    if "access_token" not in r or "refresh_token" not in r:
        raise ValueError("X did not return a refresh token: the app must request the offline.access scope")
    return r
