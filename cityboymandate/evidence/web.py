"""Live evidence from an ALLOWLIST of official sites. Search finds candidate pages; only pages on allowlisted domains are
fetched (HTTPS, HTML or text only, size-capped, no redirects off the allowlist, no private addresses); the text is then judged
exactly like library documents (verbatim sentences only). Anything that fails means no evidence, which means UNVERIFIED.
Each domain is mapped to a source type, and only types legislation/official_record/court_judgment/dataset count as primary."""
from __future__ import annotations
import datetime as dt
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from cityboymandate.core.security import validate_public_url
from cityboymandate.evidence.library import PRIMARY_TYPES, TYPES
from cityboymandate.evidence.slots import STOP, toks
from cityboymandate.pipeline.providers import RetrievedDoc

MAX_BYTES, MAX_CHARS = 1_500_000, 80_000
UA = "cityboymandate-factcheck/1.0 (automated fact-check desk; low volume)"


def parse_domains(spec: str) -> dict[str, tuple[str, bool]]:
    """'inecnigeria.org:official_record,africacheck.org:fact_checker' -> {domain: (type, is_primary)}"""
    out = {}
    for part in filter(None, (s.strip() for s in spec.split(","))):
        dom, _, typ = part.partition(":")
        typ = typ or "news"
        if typ not in TYPES:
            raise ValueError(f"{dom}: source type must be one of {sorted(TYPES)}")
        out[dom.lower().lstrip(".")] = (typ, typ in PRIMARY_TYPES)
    return out


def match_domain(host: str, domains: dict):
    host = (host or "").lower()
    for d, v in domains.items():
        if host == d or host.endswith("." + d):
            return d, v
    return None


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "form", "svg", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "table", "section", "article"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()[:MAX_CHARS]


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, domains):
        self.domains = domains

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl, resolve=True)
        if not match_domain(urllib.parse.urlparse(newurl).hostname, self.domains):
            raise urllib.error.URLError("redirect to a site that is not on the allowlist was blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_fetch(url: str, domains: dict, opener=None, timeout: int = 15, resolve: bool = True) -> str:
    validate_public_url(url, resolve=resolve)      # resolve=False only for offline tests
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("https only")
    if not match_domain(urllib.parse.urlparse(url).hostname, domains):
        raise ValueError("not on the allowlist")
    opener = opener or urllib.request.build_opener(_SafeRedirect(domains))
    with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if not ctype.startswith(("text/html", "text/plain")):
            raise ValueError(f"unsupported content type: {ctype[:40]}")
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("page too large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def brave_search(http_get, api_key: str, count: int = 8):
    def search(query: str) -> list[dict]:
        d = http_get("https://api.search.brave.com/res/v1/web/search", {"q": query, "count": count},
                     {"X-Subscription-Token": api_key, "Accept": "application/json"})
        return [{"url": r["url"], "title": r.get("title", "")} for r in d.get("web", {}).get("results", []) if r.get("url")]
    return search


class OfficialWebSearch:
    def __init__(self, search_fn, fetch_fn, domains: dict, max_docs: int = 3, daily_search_cap: int = 200,
                 today=dt.date.today, clock=lambda: dt.datetime.now(dt.timezone.utc), cache_s: int = 6 * 3600, mono=time.monotonic):
        self.search_fn, self.fetch_fn, self.domains, self.max_docs, self.cap = search_fn, fetch_fn, domains, max_docs, daily_search_cap
        self.today, self.clock, self.cache_s, self.mono = today, clock, cache_s, mono
        self.count, self.day, self.cache, self.errors = 0, None, {}, []

    def _page(self, url: str) -> str:
        hit = self.cache.get(url)
        if hit and self.mono() - hit[0] < self.cache_s:
            return hit[1]
        raw = self.fetch_fn(url)
        text = html_to_text(raw) if "<" in raw[:2000] else raw[:MAX_CHARS]
        self.cache[url] = (self.mono(), text)
        return text

    def search(self, claim) -> list[RetrievedDoc]:
        if self.day != self.today():
            self.day, self.count = self.today(), 0
        if self.count >= self.cap or not self.domains:
            return []
        words = list(dict.fromkeys(w for w in toks(claim.text) if w not in STOP))[:8]
        if len(words) < 2:
            return []
        self.count += 1
        try:
            results = self.search_fn(" ".join(words) + " (" + " OR ".join(f"site:{d}" for d in self.domains) + ")")
        except Exception as e:
            self.errors.append(f"search failed: {type(e).__name__}")
            return []
        docs = []
        for r in results:
            if len(docs) >= self.max_docs:
                break
            m = match_domain(urllib.parse.urlparse(r["url"]).hostname, self.domains)
            if not m:
                continue                                              # search engines return other sites too: ignore them
            try:
                url = validate_public_url(r["url"])
                text = self._page(url)
            except Exception as e:
                self.errors.append(f"fetch failed {m[0]}: {type(e).__name__}")
                continue
            if len(text) < 200:
                continue
            typ, primary = m[1]
            docs.append(RetrievedDoc(r.get("title") or url, m[0], url, typ, primary, text, None, None,
                                     f"Fetched live on {self.clock().date().isoformat()}; check the page's own date before relying on it.", self.clock()))
        return docs


class CompositeSearch:
    def __init__(self, searches):
        self.searches = searches

    def search(self, claim):
        seen, out = set(), []
        for s in self.searches:
            for d in s.search(claim):
                if d.url not in seen:
                    seen.add(d.url)
                    out.append(d)
        return out
