"""SQLite persistence for match results and feed metadata.

Deliberately tiny: the stdlib :mod:`sqlite3` is plenty for a sweepstake and
keeps the Replit deployment dependency-free.  All results live in two tables
(group + knockout) plus a feed-run log.  A :class:`Store` is bound to a database
path, so tests can point at a throwaway file or ``:memory:``.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "sweepstake.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_results (
    match      TEXT PRIMARY KEY,
    home       INTEGER NOT NULL,
    away       INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ko_results (
    match_no   INTEGER PRIMARY KEY,
    score1     INTEGER,
    score2     INTEGER,
    override   TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at     TEXT NOT NULL,
    source     TEXT NOT NULL,
    updated    INTEGER NOT NULL,
    ok         INTEGER NOT NULL,
    message    TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = str(path or os.environ.get("WC_DB_PATH") or DEFAULT_DB)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` so Flask's dev server threads can share it;
        # access is short-lived and serialised by SQLite's own locking.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ----- group results --------------------------------------------------- #
    def set_group_result(self, match: str, home: int, away: int) -> None:
        self._conn.execute(
            "INSERT INTO group_results(match, home, away, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(match) DO UPDATE SET home=excluded.home, away=excluded.away, "
            "updated_at=excluded.updated_at",
            (match, int(home), int(away), _now()),
        )
        self._conn.commit()

    def clear_group_result(self, match: str) -> None:
        self._conn.execute("DELETE FROM group_results WHERE match=?", (match,))
        self._conn.commit()

    def group_results(self) -> dict:
        rows = self._conn.execute("SELECT match, home, away FROM group_results").fetchall()
        return {r["match"]: {"home": r["home"], "away": r["away"]} for r in rows}

    # ----- knockout results ------------------------------------------------ #
    def set_ko_result(self, match_no: int, score1=None, score2=None, override=None) -> None:
        self._conn.execute(
            "INSERT INTO ko_results(match_no, score1, score2, override, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(match_no) DO UPDATE SET "
            "score1=excluded.score1, score2=excluded.score2, override=excluded.override, "
            "updated_at=excluded.updated_at",
            (int(match_no),
             None if score1 is None else int(score1),
             None if score2 is None else int(score2),
             override or None, _now()),
        )
        self._conn.commit()

    def clear_ko_result(self, match_no: int) -> None:
        self._conn.execute("DELETE FROM ko_results WHERE match_no=?", (int(match_no),))
        self._conn.commit()

    def ko_results(self) -> dict:
        rows = self._conn.execute(
            "SELECT match_no, score1, score2, override FROM ko_results").fetchall()
        return {
            r["match_no"]: {
                "score1": r["score1"], "score2": r["score2"], "override": r["override"],
            } for r in rows
        }

    # ----- feed log -------------------------------------------------------- #
    def log_feed(self, source: str, updated: int, ok: bool, message: str = "") -> None:
        self._conn.execute(
            "INSERT INTO feed_log(ran_at, source, updated, ok, message) VALUES (?,?,?,?,?)",
            (_now(), source, int(updated), 1 if ok else 0, message),
        )
        self._conn.commit()

    def last_feed(self) -> dict | None:
        row = self._conn.execute(
            "SELECT ran_at, source, updated, ok, message FROM feed_log "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {
            "ran_at": row["ran_at"], "source": row["source"], "updated": row["updated"],
            "ok": bool(row["ok"]), "message": row["message"],
        }

    def clear_all(self) -> None:
        self._conn.executescript(
            "DELETE FROM group_results; DELETE FROM ko_results; DELETE FROM feed_log;")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
