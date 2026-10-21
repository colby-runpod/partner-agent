"""Outbox storage boundary. SQLite remains the only enabled production backend."""
import sqlite3


class SQLiteOutboxStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS outbox_delivery (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, state TEXT NOT NULL, ts TEXT, decision TEXT, actor TEXT, at TEXT, error TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS outbox_ts ON outbox_delivery(ts)')
            db.execute('CREATE TABLE IF NOT EXISTS outbox_events (key TEXT PRIMARY KEY, payload TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db
