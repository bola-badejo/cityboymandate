"""What each platform's official API lets us do. Encoded as data so the publishing gate can enforce it.
Sources checked 2026-10-24: Meta developer docs (IG hashtag search), Meta Content Library pages.
Re-verify against current Meta/X documentation before go-live; these policies change."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformCapabilities:
    name: str
    keyword_discovery: str                # how public content can be found via official APIs
    reply_to_third_party_posts: bool      # unsolicited replies to posts on assets we don't operate
    notes: str


PLATFORMS = {
    "x": PlatformCapabilities(
        "x", "recent-post search / streaming, subject to API tier and rate limits", True,
        "Automated replies need X's prior written approval and recipient opt-in; kept off by default."),
    "facebook": PlatformCapabilities(
        "facebook", "none via Graph API for public keyword search; own Pages' posts/comments only", False,
        "Meta Content Library is a controlled research environment for qualifying academic/non-profit "
        "researchers (no export); it is not wired in and must not be used to power a reply bot."),
    "instagram": PlatformCapabilities(
        "instagram", "hashtag search only: read-only, needs App Review (Instagram Public Content Access), "
        "30 unique hashtags per rolling 7 days, no usernames returned", False,
        "Cannot comment on media found through hashtag search; replies only to comments on accounts we operate."),
}
