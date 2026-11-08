"""SQLite journal: every signal, order, fill and P&L snapshot is recorded."""
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (ts REAL, kind TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS baskets (
  id TEXT PRIMARY KEY, name TEXT, opened_ts REAL, closed_ts REAL,
  legs TEXT, entry_cost REAL, exit_value REAL, pnl REAL, status TEXT
);
"""


class Journal:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def event(self, kind, **data):
        self.db.execute("INSERT INTO events VALUES (?,?,?)", (time.time(), kind, json.dumps(data, default=str)))
        self.db.commit()

    def open_basket(self, bid, name, legs, entry_cost):
        self.db.execute("INSERT INTO baskets VALUES (?,?,?,?,?,?,?,?,?)",
                        (bid, name, time.time(), None, json.dumps(legs), entry_cost, None, None, "open"))
        self.db.commit()

    def close_basket(self, bid, exit_value, pnl, status="closed"):
        self.db.execute("UPDATE baskets SET closed_ts=?, exit_value=?, pnl=?, status=? WHERE id=?",
                        (time.time(), exit_value, pnl, status, bid))
        self.db.commit()

    def open_baskets(self):
        rows = self.db.execute("SELECT id,name,legs,entry_cost FROM baskets WHERE status='open'").fetchall()
        return [{"id": r[0], "name": r[1], "legs": json.loads(r[2]), "entry_cost": r[3]} for r in rows]

    def realized_pnl_since(self, ts):
        r = self.db.execute("SELECT COALESCE(SUM(pnl),0) FROM baskets WHERE closed_ts>=?", (ts,)).fetchone()
        return float(r[0])
