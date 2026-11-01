"""State snapshots in SQLite (a file you can back up). Swap StateStore for a Postgres-backed class with the same
two methods for hosts without a disk. The audit log is separate and append-only; this holds everything else.
A snapshot is rewritten after every change, which is fine at pilot scale (hundreds of investigations a day)."""
from __future__ import annotations
import dataclasses
import datetime as dt
import enum
import json
import os
import sqlite3
import threading
from cityboymandate.core import models as M
from cityboymandate.core.gate import SystemConfiguration
from cityboymandate.core.sources import SourceEntry

CLASSES = {c.__name__: c for c in [M.SocialPost, M.ExtractedClaim, M.EvidenceSource, M.EvidencePassage, M.ClaimVerdict,
                                   M.DraftResponse, M.HumanReview, M.Investigation, M.MonitoringRule, SourceEntry, SystemConfiguration]}
ENUMS = {e.__name__: e for e in [M.Verdict, M.ReviewOutcome, M.Relation, M.Stage, M.Role]}


def enc(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {"__dc": type(o).__name__, **{f.name: enc(getattr(o, f.name)) for f in dataclasses.fields(o)}}
    if isinstance(o, enum.Enum):
        return {"__en": type(o).__name__, "v": o.value}
    if isinstance(o, dt.datetime):
        return {"__dt": o.isoformat()}
    if isinstance(o, bytes):
        return {"__b": o.hex()}
    if isinstance(o, tuple):
        return {"__tu": [enc(x) for x in o]}
    if isinstance(o, (set, frozenset)):
        return {"__s": [enc(x) for x in o]}
    if isinstance(o, list):
        return [enc(x) for x in o]
    if isinstance(o, dict):
        return {"__d": [[enc(k), enc(v)] for k, v in o.items()]}
    return o


def dec(o):
    if isinstance(o, list):
        return [dec(x) for x in o]
    if not isinstance(o, dict):
        return o
    if "__dc" in o:
        cls = CLASSES[o["__dc"]]
        return cls(**{k: dec(v) for k, v in o.items() if k != "__dc"})
    if "__en" in o:
        return ENUMS[o["__en"]](o["v"])
    if "__dt" in o:
        return dt.datetime.fromisoformat(o["__dt"])
    if "__b" in o:
        return bytes.fromhex(o["__b"])
    if "__tu" in o:
        return tuple(dec(x) for x in o["__tu"])
    if "__s" in o:
        return {dec(x) for x in o["__s"]}
    if "__d" in o:
        return {dec(k): dec(v) for k, v in o["__d"]}
    return o


class StateStore:
    def __init__(self, path: str):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        if path != ":memory:":
            try:
                os.chmod(path, 0o600)       # holds password hashes and approval references
            except OSError:
                pass

    def save(self, snapshot: dict) -> None:
        blob = json.dumps(enc(snapshot))
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('snapshot', ?)", (blob,))

    def load(self) -> dict | None:
        row = self.db.execute("SELECT v FROM state WHERE k='snapshot'").fetchone()
        return dec(json.loads(row[0])) if row else None
