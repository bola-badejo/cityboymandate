"""SSRF guard and prompt-injection scanning for untrusted retrieved content."""
from __future__ import annotations
import ipaddress
import re
import socket
from urllib.parse import urlparse

BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan")


class UnsafeURL(Exception):
    pass


def _check_ip(ip: str) -> None:
    a = ipaddress.ip_address(ip)
    if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified:
        raise UnsafeURL(f"non-public address blocked: {ip}")


def validate_public_url(url: str, resolve: bool = False) -> str:
    """Return canonical URL or raise UnsafeURL. Use resolve=True in production fetchers
    (and re-check at connect time to defeat DNS rebinding)."""
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsafeURL("only absolute http(s) URLs allowed")
    if p.username or p.password:
        raise UnsafeURL("credentials in URL not allowed")
    host = p.hostname.lower()
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURL("internal hostname blocked")
    try:
        _check_ip(host)
    except ValueError:
        if resolve:
            for info in socket.getaddrinfo(host, None):
                _check_ip(info[4][0])
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{host}{port}{p.path.rstrip('/') or ''}" + (f"?{p.query}" if p.query else "")


_INJECTION = [
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts?)",
    r"disregard (the )?(system|previous) (prompt|instructions)",
    r"you are now\b", r"new (role|instructions?)\s*:", r"system prompt",
    r"(mark|label|rate|classify) (this|it|the claim) as (true|false|supported)",
    r"reveal (your|the) (secrets?|api key|prompt)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION), re.I)


def scan_untrusted(text: str) -> list[str]:
    """Flag suspected embedded instructions. Flags go to reviewers; the text is still treated
    purely as data and has no code path into verdict logic."""
    return sorted({m.group(0).lower() for m in _INJECTION_RE.finditer(text)})
