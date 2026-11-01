"""Conversation limits. The desk never starts conversations with strangers and never sends cold DMs.
It may continue a thread only where the person is on an account we operate, or has opted in, and
a human can take over at any time. Every turn is still a draft needing human approval and carries the disclosure label."""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass

MAX_BOT_TURNS = 3
DM_WINDOW_HOURS = 24      # confirm against the current platform messaging policy before enabling any DM reply


@dataclass
class ConvState:
    thread_id: str
    turns: int = 0
    human_takeover: bool = False


def may_reply(post, state: ConvState, opted_in: bool, opted_out: bool) -> tuple[bool, str]:
    if opted_out:
        return False, "recipient opted out"
    if state.human_takeover:
        return False, "a human has taken over this conversation"
    if state.turns >= MAX_BOT_TURNS:
        return False, f"turn limit ({MAX_BOT_TURNS}) reached; hand over to a person"
    if not (post.own_asset or opted_in):
        return False, "not on an account we operate and no opt-in: no unsolicited conversation"
    return True, "ok"


def may_reply_to_inbound_dm(last_inbound_at: dt.datetime, now: dt.datetime, opted_out: bool) -> tuple[bool, str]:
    """Replies to a message the person sent us, inside the window. Cold outbound DMs are not supported."""
    if opted_out:
        return False, "recipient opted out"
    if now - last_inbound_at > dt.timedelta(hours=DM_WINDOW_HOURS):
        return False, "outside the reply window; wait for them to message us again"
    return True, "ok"
