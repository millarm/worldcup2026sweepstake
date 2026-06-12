"""Persistence layer for match results and feed metadata.

Backends
--------
* **PostgreSQL** — used automatically when ``DATABASE_URL`` is set and no
  explicit path is passed to :class:`Store`.  Requires ``psycopg2``.
* **SQLite** — used when an explicit ``path`` argument is supplied (always the
  case for tests) or when ``DATABASE_URL`` is absent.  Keeps the test suite
  dependency-free and fast.

All public methods are identical regardless of backend, so the rest of the
codebase is unaware of which one is in use.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "sweepstake.db"

_SCHEMA_SQLITE = """
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

_SCHEMA_PG = """
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
    id         SERIAL PRIMARY KEY,
    ran_at     TEXT NOT NULL,
    source     TEXT NOT NULL,
    updated    INTEGER NOT NULL,
    ok         INTEGER NOT NULL,
    message    TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  SQLite backend
# --------------------------------------------------------------------------- #
class _SqliteBackend:
    def __init__(self, path: str):
        import sqlite3
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA_SQLITE)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def fetchall(self, sql: str, params: tuple = ()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple = ()):
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def script(self, sql: str):
        with self._lock:
            self._conn.executescript(sql)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # Adapt placeholders: SQLite uses ? but callers may use %s (pg style)
    @staticmethod
    def ph(n: int) -> str:
        return "?"


# --------------------------------------------------------------------------- #
#  PostgreSQL backend
# --------------------------------------------------------------------------- #
class _PgBackend:
    def __init__(self, dsn: str):
        import psycopg2
        import psycopg2.extras
        self._dsn = dsn
        self._lock = threading.Lock()
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = False
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(_SCHEMA_PG)
            self._conn.commit()
            cur.close()

    def _cursor(self):
        import psycopg2
        try:
            if self._conn.closed:
                raise psycopg2.OperationalError("connection closed")
            self._conn.isolation_level  # ping
        except Exception:
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = False
        return self._conn.cursor()

    def execute(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self._cursor()
            cur.execute(sql, params or None)
            self._conn.commit()
            cur.close()

    def fetchall(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self._cursor()
            cur.execute(sql, params or None)
            rows = cur.fetchall()
            cur.close()
            return rows

    def fetchone(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self._cursor()
            cur.execute(sql, params or None)
            row = cur.fetchone()
            cur.close()
            return row

    def script(self, sql: str):
        with self._lock:
            cur = self._cursor()
            cur.execute(sql)
            self._conn.commit()
            cur.close()

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
#  Store — public API
# --------------------------------------------------------------------------- #
class Store:
    """Thin persistence wrapper.  Uses PostgreSQL when available, SQLite otherwise."""

    def __init__(self, path: str | os.PathLike | None = None):
        # Priority: explicit path arg > WC_DB_PATH env var > DATABASE_URL (pg) > default SQLite
        explicit = path or os.environ.get("WC_DB_PATH")
        dsn = os.environ.get("DATABASE_URL") if not explicit else None
        if dsn:
            self._db = _PgBackend(dsn)
            self._pg = True
        else:
            self._db = _SqliteBackend(str(explicit or DEFAULT_DB))
            self._pg = False

    # ----- group results --------------------------------------------------- #
    def set_group_result(self, match: str, home: int, away: int) -> None:
        if self._pg:
            self._db.execute(
                "INSERT INTO group_results(match, home, away, updated_at) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT(match) DO UPDATE SET home=EXCLUDED.home, away=EXCLUDED.away, "
                "updated_at=EXCLUDED.updated_at",
                (match, int(home), int(away), _now()),
            )
        else:
            self._db.execute(
                "INSERT INTO group_results(match, home, away, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(match) DO UPDATE SET home=excluded.home, away=excluded.away, "
                "updated_at=excluded.updated_at",
                (match, int(home), int(away), _now()),
            )

    def clear_group_result(self, match: str) -> None:
        if self._pg:
            self._db.execute("DELETE FROM group_results WHERE match=%s", (match,))
        else:
            self._db.execute("DELETE FROM group_results WHERE match=?", (match,))

    def group_results(self) -> dict:
        rows = self._db.fetchall("SELECT match, home, away FROM group_results")
        return {r[0]: {"home": r[1], "away": r[2]} for r in rows}

    # ----- knockout results ------------------------------------------------ #
    def set_ko_result(self, match_no: int, score1=None, score2=None, override=None) -> None:
        if self._pg:
            self._db.execute(
                "INSERT INTO ko_results(match_no, score1, score2, override, updated_at) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(match_no) DO UPDATE SET "
                "score1=EXCLUDED.score1, score2=EXCLUDED.score2, override=EXCLUDED.override, "
                "updated_at=EXCLUDED.updated_at",
                (int(match_no),
                 None if score1 is None else int(score1),
                 None if score2 is None else int(score2),
                 override or None, _now()),
            )
        else:
            self._db.execute(
                "INSERT INTO ko_results(match_no, score1, score2, override, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(match_no) DO UPDATE SET "
                "score1=excluded.score1, score2=excluded.score2, override=excluded.override, "
                "updated_at=excluded.updated_at",
                (int(match_no),
                 None if score1 is None else int(score1),
                 None if score2 is None else int(score2),
                 override or None, _now()),
            )

    def clear_ko_result(self, match_no: int) -> None:
        if self._pg:
            self._db.execute("DELETE FROM ko_results WHERE match_no=%s", (int(match_no),))
        else:
            self._db.execute("DELETE FROM ko_results WHERE match_no=?", (int(match_no),))

    def ko_results(self) -> dict:
        rows = self._db.fetchall(
            "SELECT match_no, score1, score2, override FROM ko_results")
        return {
            r[0]: {"score1": r[1], "score2": r[2], "override": r[3]}
            for r in rows
        }

    # ----- feed log -------------------------------------------------------- #
    def log_feed(self, source: str, updated: int, ok: bool, message: str = "") -> None:
        if self._pg:
            self._db.execute(
                "INSERT INTO feed_log(ran_at, source, updated, ok, message) VALUES (%s,%s,%s,%s,%s)",
                (_now(), source, int(updated), 1 if ok else 0, message),
            )
        else:
            self._db.execute(
                "INSERT INTO feed_log(ran_at, source, updated, ok, message) VALUES (?,?,?,?,?)",
                (_now(), source, int(updated), 1 if ok else 0, message),
            )

    def last_feed(self) -> dict | None:
        row = self._db.fetchone(
            "SELECT ran_at, source, updated, ok, message FROM feed_log "
            "ORDER BY id DESC LIMIT 1")
        if not row:
            return None
        return {
            "ran_at": row[0], "source": row[1], "updated": row[2],
            "ok": bool(row[3]), "message": row[4],
        }

    def clear_all(self) -> None:
        if self._pg:
            self._db.script(
                "DELETE FROM group_results; DELETE FROM ko_results; DELETE FROM feed_log;")
        else:
            self._db.script(
                "DELETE FROM group_results; DELETE FROM ko_results; DELETE FROM feed_log;")

    def close(self) -> None:
        self._db.close()
