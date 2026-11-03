"""Go-live checklist from local settings only (no network calls, nothing is created or sent).
  python -m cityboymandate.preflight      exits 1 if anything FAILs."""
from __future__ import annotations
import datetime as dt
import os
import sys


def check(env, today: dt.date | None = None) -> list[tuple[str, str, str]]:
    r: list[tuple[str, str, str]] = []
    add = lambda lvl, name, msg: r.append((lvl, name, msg))
    g = env.get
    add("PASS" if len(g("JWT_SECRET", "")) >= 32 else "FAIL", "JWT_SECRET", "at least 32 random characters" if len(g("JWT_SECRET", "")) < 32 else "set")
    add("PASS" if g("CRON_SECRET") or g("ENABLE_TIMER") == "1" else "FAIL", "scan trigger", "set CRON_SECRET (external cron) or ENABLE_TIMER=1, otherwise nothing is ever scanned" if not (g("CRON_SECRET") or g("ENABLE_TIMER") == "1") else "scans will run")
    add("PASS" if g("BOOTSTRAP_ADMIN_PASSWORD") else "WARN", "admin account", "BOOTSTRAP_ADMIN_PASSWORD not set: fine only if the admin already exists in STATE_DB")
    for k in ("STATE_DB", "AUDIT_DB"):
        v = g(k, "")
        add("PASS" if v and v != ":memory:" else "FAIL", k, "set" if v and v != ":memory:" else "unset: everything is lost on restart")
    add("PASS" if g("DEMO_MODE", "1") == "0" else "FAIL", "DEMO_MODE", "0 (real platforms)" if g("DEMO_MODE", "1") == "0" else "still 1: fictional demo data, not live")
    # evidence
    ld = g("LIBRARY_DIR")
    if not ld:
        add("FAIL", "evidence library", "LIBRARY_DIR unset: every real claim will be UNVERIFIED")
    else:
        from cityboymandate.evidence.library import DocumentLibrary
        st = DocumentLibrary(ld).status(today)
        if st["documents"] == 0:
            add("FAIL", "evidence library", f"no usable documents ({'; '.join(st['problems'][:2]) or 'empty'})")
        else:
            add("PASS", "evidence library", f"{st['documents']} documents, {st['primary']} primary")
            if st["primary"] == 0:
                add("WARN", "primary sources", "no primary documents: a 'false' verdict needs one")
            if st["problems"]:
                add("WARN", "library problems", f"{len(st['problems'])} entries skipped: python -m cityboymandate.evidence check {ld}")
            if st["past_review_date"]:
                add("WARN", "stale documents", "past review date: " + ", ".join(st["past_review_date"][:3]))
    # X
    x_read = bool(g("X_BEARER_TOKEN"))
    if x_read and not (g("X_BOT_HANDLE") and g("X_BOT_USER_ID")):
        add("FAIL", "X mentions", "X_BEARER_TOKEN set but X_BOT_HANDLE / X_BOT_USER_ID missing: the bot cannot see mentions")
    add("PASS" if x_read else "WARN", "X monitoring", "configured" if x_read else "no X_BEARER_TOKEN: X is not monitored")
    # Meta
    meta = bool(g("META_ACCESS_TOKEN"))
    if meta and not g("META_API_VERSION"):
        add("FAIL", "Meta", "META_ACCESS_TOKEN set but META_API_VERSION missing")
    elif meta and not (g("FB_PAGE_ID") or g("FB_OWN_POST_IDS") or g("IG_USER_ID") or g("IG_OWN_MEDIA_IDS")):
        add("FAIL", "Meta", "token set but no FB_PAGE_ID / IG_USER_ID: nothing to watch")
    add("PASS" if meta else "WARN", "Facebook/Instagram", "configured (own Page/account comments only)" if meta else "no META_ACCESS_TOKEN: Facebook/Instagram not monitored")
    # posting
    if g("ENABLE_PUBLISHER") == "1":
        add("WARN", "publishing wiring", "ON: still blocked until an admin enables publishing with written platform approvals")
        if x_read:
            if g("TOKEN_DB") and g("X_CLIENT_ID"):
                p = g("TOKEN_DB")
                if not os.path.exists(p):
                    add("FAIL", "X sign-in", "TOKEN_DB has no tokens yet: run scripts/x_authorize.py")
                else:
                    from cityboymandate.platforms.x_auth import TokenStore
                    s = TokenStore(p).load()
                    add("PASS" if s and s["status"] == "ok" else "FAIL", "X sign-in", "authorized, auto-refreshing" if s and s["status"] == "ok" else "needs authorization: run scripts/x_authorize.py")
            elif g("X_USER_ACCESS_TOKEN"):
                add("WARN", "X sign-in", "static token: it expires in about 2 hours. Use TOKEN_DB + X_CLIENT_ID for auto-refresh")
            else:
                add("FAIL", "X sign-in", "ENABLE_PUBLISHER=1 but no X posting credentials")
    else:
        add("INFO", "publishing", "OFF: drafts only. Staff copy approved drafts and post by hand")
    if x_read:
        add("PASS" if g("X_DAILY_BUDGET_USD") else "WARN", "X spending cap", f"daily cap ${g('X_DAILY_BUDGET_USD')}" if g("X_DAILY_BUDGET_USD") else "X_DAILY_BUDGET_USD unset: defaults to $5 a day. ALSO set a spending limit in the X console")
    if g("RESPONDER") == "llm":
        add("PASS" if g("ANTHROPIC_API_KEY") else "FAIL", "natural replies", "model writer on, every reply validated" if g("ANTHROPIC_API_KEY") else "RESPONDER=llm needs ANTHROPIC_API_KEY")
    if g("WEB_EVIDENCE") == "1":
        ok = bool(g("BRAVE_API_KEY") and g("EVIDENCE_DOMAINS"))
        add("PASS" if ok else "FAIL", "live official-site evidence", "enabled" if ok else "WEB_EVIDENCE=1 needs BRAVE_API_KEY and EVIDENCE_DOMAINS")
    if g("ANTHROPIC_API_KEY"):
        add("WARN", "language-model judge", "enabled but never trialled on live claims: read 50 results before relying on it")
    add("INFO", "audio/video", "not analysed: clips and voice notes get a 'could not confirm' answer (no transcription provider yet)")
    add("INFO", "languages", "only English is evaluated; other languages are held for a fluent reviewer (docs/LANGUAGES.md)")
    return r


if __name__ == "__main__":
    rows = check(os.environ)
    for lvl, name, msg in rows:
        print(f"{lvl:5} {name:22} {msg}")
    fails = sum(1 for r in rows if r[0] == "FAIL")
    print(f"\n{'NOT READY' if fails else 'READY (read the WARN lines)'}: {fails} blocking problem(s)")
    sys.exit(1 if fails else 0)
