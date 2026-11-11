"""SQLite identity mapping, without protocol transport or QQ UI."""

import sqlite3
from pathlib import Path

from quitbot_backend._engine.domain.onebot import numeric_id


class MessageIndex:
    """Stable positive int32 IDs, scoped by account + conversation + UI ID."""

    def __init__(self, path=":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, ui_id TEXT NOT NULL, UNIQUE(account,kind,target,ui_id))"
        )
        self.db.commit()

    def register(self, account, target, ui_id):
        params = (str(account), target.kind, target.id, str(ui_id))
        existing = self.db.execute(
            "SELECT id FROM messages WHERE account=? AND kind=? AND target=? AND ui_id=?",
            params,
        ).fetchone()
        if existing is not None:
            return existing[0]
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO messages(account,kind,target,ui_id) VALUES (?,?,?,?)",
                (str(account), target.kind, target.id, str(ui_id)),
            )
        number = self.db.execute(
            "SELECT id FROM messages WHERE account=? AND kind=? AND target=? AND ui_id=?",
            (str(account), target.kind, target.id, str(ui_id)),
        ).fetchone()[0]
        if number > 2**31 - 1:
            raise OverflowError("OneBot int32 message ID space exhausted")
        return number

    def resolve(self, account, number):
        row = self.db.execute(
            "SELECT kind,target,ui_id FROM messages WHERE account=? AND id=?",
            (str(account), numeric_id(number)),
        ).fetchone()
        if row is None:
            raise ValueError("Unknown message ID for this account/index")
        return row

    def close(self):
        self.db.close()
