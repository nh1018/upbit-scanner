"""Local-only dispatch simulation. No network send function exists here."""
from pathlib import Path
import sqlite3
from contextlib import closing


class SimulationLedger:
    """A durable reservation is never automatically cleared after ambiguity.

    This is NOT a production approval/activation registry. Tests use temp paths.
    Exactly-once dispatch against native schedules is not provided by this lock.
    """
    def __init__(self, path):
        self.path = Path(path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, strategy TEXT, at INTEGER, state TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, key TEXT, state TEXT)")

    def reserve(self, proposal, now):
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM attempts WHERE state IN ('RESERVED','UNKNOWN','SIMULATED_ACCEPTED')").fetchone():
                return False
            if db.execute("SELECT 1 FROM attempts WHERE strategy=? AND at>?",
                          (proposal["strategy"], now - 3_600_000)).fetchone():
                return False
            try:
                db.execute("INSERT INTO attempts VALUES (?,?,?,'RESERVED')",
                           (proposal["key"], proposal["strategy"], now))
            except sqlite3.IntegrityError:
                return False
            db.execute("INSERT INTO audit(key,state) VALUES (?,'RESERVED')", (proposal["key"],))
            return True

    def simulated_result(self, key, state):
        if state not in {"UNKNOWN", "SIMULATED_ACCEPTED"}:
            raise ValueError("invalid simulation outcome")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE attempts SET state=? WHERE key=? AND state='RESERVED'", (state, key))
            if changed.rowcount != 1:
                raise ValueError("reservation not found or already completed")
            db.execute("INSERT INTO audit(key,state) VALUES (?,?)", (key, state))
