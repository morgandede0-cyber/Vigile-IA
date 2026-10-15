"""SQLite persistant. Les écritures sont sérialisées dans le processus Discord."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

class Storage:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS cases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL UNIQUE, image_sha256 TEXT NOT NULL,
          image_url TEXT NOT NULL, proposed_name TEXT,
          confidence REAL NOT NULL, reason TEXT NOT NULL,
          status TEXT NOT NULL, reviewer_id INTEGER,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_cases_user ON cases(guild_id,user_id,status);
        CREATE INDEX IF NOT EXISTS ix_cases_hash ON cases(guild_id,image_sha256);
        CREATE TABLE IF NOT EXISTS approvals (
          guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          nickname TEXT NOT NULL, image_sha256 TEXT NOT NULL,
          approved_at TEXT NOT NULL,
          PRIMARY KEY(guild_id,user_id)
        );
        ''')
        self.db.commit()

    def create_case(self, guild, user, message, digest, url, name, confidence, reason):
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            cur = self.db.execute('''INSERT OR IGNORE INTO cases
            (guild_id,user_id,message_id,image_sha256,image_url,proposed_name,confidence,reason,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (guild,user,message,digest,url,name,confidence,reason,"pending",now,now))
            return cur.lastrowid if cur.rowcount else None

    def get(self, case_id):
        return self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()

    def pending_for_user(self, guild, user):
        return self.db.execute("SELECT id FROM cases WHERE guild_id=? AND user_id=? AND status='pending' LIMIT 1", (guild,user)).fetchone()

    def approved_user(self, guild, user):
        return self.db.execute("SELECT * FROM approvals WHERE guild_id=? AND user_id=?", (guild,user)).fetchone()

    def existing_name(self, guild, name, excluding_user):
        return self.db.execute("SELECT user_id FROM approvals WHERE guild_id=? AND nickname=? COLLATE NOCASE AND user_id!=? LIMIT 1", (guild,name,excluding_user)).fetchone()

    def reused_image(self, guild, digest, excluding_user):
        return self.db.execute("SELECT user_id FROM approvals WHERE guild_id=? AND image_sha256=? AND user_id!=? LIMIT 1", (guild,digest,excluding_user)).fetchone()

    def set_status(self, case_id, status, reviewer=None):
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute("UPDATE cases SET status=?, reviewer_id=?, updated_at=? WHERE id=? AND status='pending'", (status,reviewer,now,case_id))

    def approve(self, case_id, name, reviewer=None):
        case = self.get(case_id)
        if not case or case['status'] != 'pending':
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute("INSERT INTO approvals(guild_id,user_id,nickname,image_sha256,approved_at) VALUES (?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET nickname=excluded.nickname,image_sha256=excluded.image_sha256,approved_at=excluded.approved_at", (case['guild_id'],case['user_id'],name,case['image_sha256'],now))
            self.db.execute("UPDATE cases SET status='approved',reviewer_id=?,updated_at=? WHERE id=?", (reviewer,now,case_id))
        return True
