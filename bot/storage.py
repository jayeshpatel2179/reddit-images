"""SQLite state: which posts were already sent, plus the last-run checkpoint."""
import sqlite3
import time
from pathlib import Path


class Storage:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sent_posts (
                id TEXT PRIMARY KEY,
                created_utc REAL NOT NULL,
                sent_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent'
            );
            CREATE TABLE IF NOT EXISTS failures (
                id TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self.conn.commit()

    def is_sent(self, post_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sent_posts WHERE id = ?", (post_id,)).fetchone() is not None

    def mark_sent(self, post_id: str, created_utc: float, status: str = "sent") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sent_posts (id, created_utc, sent_at, status) VALUES (?, ?, ?, ?)",
            (post_id, created_utc, time.time(), status),
        )
        self.conn.execute("DELETE FROM failures WHERE id = ?", (post_id,))
        self.conn.commit()

    def record_failure(self, post_id: str) -> int:
        self.conn.execute(
            "INSERT INTO failures (id, attempts) VALUES (?, 1) "
            "ON CONFLICT(id) DO UPDATE SET attempts = attempts + 1",
            (post_id,),
        )
        self.conn.commit()
        return self.conn.execute("SELECT attempts FROM failures WHERE id = ?", (post_id,)).fetchone()[0]

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def prune(self, older_than_days: int = 30) -> None:
        cutoff = time.time() - older_than_days * 86400
        self.conn.execute("DELETE FROM sent_posts WHERE created_utc < ?", (cutoff,))
        self.conn.commit()
