"""Append-only, hash-chained audit log backed by SQLite (swap for Postgres in production)."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from cityboymandate.core.models import utcnow

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
            action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
            detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
        """)

    @staticmethod
    def _digest(prev, ts, actor, action, entity, entity_id, detail) -> str:
        blob = json.dumps([prev, ts, actor, action, entity, entity_id, detail], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def record(self, actor: str, action: str, entity: str, entity_id: str, detail: dict | None = None) -> str:
        detail_s = json.dumps(detail or {}, sort_keys=True, default=str)
        row = self.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        ts = utcnow().isoformat()
        h = self._digest(prev, ts, actor, action, entity, entity_id, detail_s)
        self.db.execute(
            "INSERT INTO events(ts,actor,action,entity,entity_id,detail,prev_hash,hash) VALUES (?,?,?,?,?,?,?,?)",
            (ts, actor, action, entity, entity_id, detail_s, prev, h))
        self.db.commit()
        return h

    def events(self, entity_id: str | None = None) -> list[dict]:
        q = "SELECT seq,ts,actor,action,entity,entity_id,detail FROM events"
        args: tuple = ()
        if entity_id:
            q += " WHERE entity_id=?"
            args = (entity_id,)
        cols = ["seq", "ts", "actor", "action", "entity", "entity_id", "detail"]
        return [dict(zip(cols, r)) for r in self.db.execute(q + " ORDER BY seq", args)]

    def verify(self) -> bool:
        prev = GENESIS
        for seq, ts, actor, action, entity, eid, detail, p, h in self.db.execute(
                "SELECT seq,ts,actor,action,entity,entity_id,detail,prev_hash,hash FROM events ORDER BY seq"):
            if p != prev or self._digest(p, ts, actor, action, entity, eid, detail) != h:
                return False
            prev = h
        return True
