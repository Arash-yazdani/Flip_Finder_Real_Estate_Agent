"""
SQLite-backed user + run accounting for the dashboard.

Tables:
  users(email PK, password_hash, password_salt, daily_cap, is_admin, created_at, disabled)
  runs(id PK, user_email, city, intent, count_requested,
       enrich_attempted, enrich_from_cache, enrich_fresh,
       cost_bright_data_usd, cost_rapidapi_usd, total_cost_usd,
       status, error, started_at, finished_at)

No third-party deps — stdlib sqlite3 + hashlib.pbkdf2 for password hashing.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "dashboard" / "admin.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_DAILY_CAP = int(os.environ.get("DEFAULT_DAILY_CAP", "5"))

_LOCK = threading.Lock()  # serialize writes; sqlite handles its own readers


# ---- connection ----

@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)  # autocommit
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
    finally:
        c.close()


# ---- schema ----

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email          TEXT PRIMARY KEY,
    password_hash  TEXT NOT NULL,
    password_salt  TEXT NOT NULL,
    daily_cap      INTEGER NOT NULL DEFAULT 5,
    is_admin       INTEGER NOT NULL DEFAULT 0,
    disabled       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email            TEXT NOT NULL,
    city                  TEXT NOT NULL,
    intent                TEXT NOT NULL,
    count_requested       INTEGER NOT NULL,
    enrich_attempted      INTEGER NOT NULL DEFAULT 0,
    enrich_from_cache     INTEGER NOT NULL DEFAULT 0,
    enrich_fresh          INTEGER NOT NULL DEFAULT 0,
    cost_bright_data_usd  REAL NOT NULL DEFAULT 0,
    cost_rapidapi_usd     REAL NOT NULL DEFAULT 0,
    total_cost_usd        REAL NOT NULL DEFAULT 0,
    status                TEXT NOT NULL DEFAULT 'pending',
    error                 TEXT,
    started_at            TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at           TEXT
);

CREATE INDEX IF NOT EXISTS runs_user_started ON runs(user_email, started_at);
CREATE INDEX IF NOT EXISTS runs_started ON runs(started_at);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS run_events_run ON run_events(run_id, id);
"""


def init_db() -> None:
    """Create tables. Idempotent."""
    with _LOCK, _conn() as c:
        c.executescript(_SCHEMA)


# ---- password hashing ----

_PBKDF2_ITERATIONS = 200_000


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return dk.hex(), salt


def _verify_password(password: str, password_hash: str, salt: str) -> bool:
    h, _ = _hash_password(password, salt)
    return secrets.compare_digest(h, password_hash)


# ---- users ----

@dataclass
class User:
    email: str
    daily_cap: int
    is_admin: bool
    disabled: bool
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            email=row["email"],
            daily_cap=row["daily_cap"],
            is_admin=bool(row["is_admin"]),
            disabled=bool(row["disabled"]),
            created_at=row["created_at"],
        )


def create_user(
    email: str,
    password: str,
    daily_cap: int = DEFAULT_DAILY_CAP,
    is_admin: bool = False,
) -> User:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("invalid email")
    if not password or len(password) < 4:
        raise ValueError("password too short (min 4 chars)")
    pw_hash, salt = _hash_password(password)
    with _LOCK, _conn() as c:
        try:
            c.execute(
                "INSERT INTO users(email, password_hash, password_salt, daily_cap, is_admin) "
                "VALUES(?, ?, ?, ?, ?)",
                (email, pw_hash, salt, daily_cap, 1 if is_admin else 0),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"user already exists: {email}")
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    return User.from_row(row)


def authenticate(email: str, password: str) -> Optional[User]:
    email = (email or "").strip().lower()
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row:
        return None
    if row["disabled"]:
        return None
    if not _verify_password(password, row["password_hash"], row["password_salt"]):
        return None
    return User.from_row(row)


def get_user(email: str) -> Optional[User]:
    email = (email or "").strip().lower()
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    return User.from_row(row) if row else None


def list_users() -> List[User]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [User.from_row(r) for r in rows]


def update_user_cap(email: str, daily_cap: int) -> bool:
    email = email.strip().lower()
    if daily_cap < 0:
        raise ValueError("cap must be >= 0")
    with _LOCK, _conn() as c:
        cur = c.execute("UPDATE users SET daily_cap=? WHERE email=?", (daily_cap, email))
    return cur.rowcount > 0


def reset_password(email: str, new_password: str) -> bool:
    if not new_password or len(new_password) < 4:
        raise ValueError("password too short (min 4 chars)")
    pw_hash, salt = _hash_password(new_password)
    email = email.strip().lower()
    with _LOCK, _conn() as c:
        cur = c.execute(
            "UPDATE users SET password_hash=?, password_salt=? WHERE email=?",
            (pw_hash, salt, email),
        )
    return cur.rowcount > 0


def set_disabled(email: str, disabled: bool) -> bool:
    email = email.strip().lower()
    with _LOCK, _conn() as c:
        cur = c.execute(
            "UPDATE users SET disabled=? WHERE email=?",
            (1 if disabled else 0, email),
        )
    return cur.rowcount > 0


def delete_user(email: str) -> bool:
    email = email.strip().lower()
    with _LOCK, _conn() as c:
        cur = c.execute("DELETE FROM users WHERE email=?", (email,))
    return cur.rowcount > 0


# ---- runs ----

def runs_today(email: str) -> int:
    """How many runs has this user logged today (server-local midnight reset)?"""
    email = email.strip().lower()
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE user_email=? AND started_at >= ?",
            (email, midnight.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchone()
    return int(row["n"])


def remaining_runs(email: str) -> Optional[int]:
    """None for admin (no cap). Otherwise: max(0, cap - used_today)."""
    user = get_user(email)
    if not user:
        return 0
    if user.is_admin:
        return None
    return max(0, user.daily_cap - runs_today(email))


def log_run_start(user_email: str, city: str, intent: str, count_requested: int) -> int:
    with _LOCK, _conn() as c:
        cur = c.execute(
            "INSERT INTO runs(user_email, city, intent, count_requested) VALUES(?,?,?,?)",
            (user_email.strip().lower(), city, intent, count_requested),
        )
        return int(cur.lastrowid)


def log_run_finish(
    run_id: int,
    enrich_attempted: int,
    enrich_from_cache: int,
    enrich_fresh: int,
    cost_bright_data_usd: float,
    cost_rapidapi_usd: float,
    status: str = "ok",
    error: Optional[str] = None,
) -> None:
    total = cost_bright_data_usd + cost_rapidapi_usd
    with _LOCK, _conn() as c:
        c.execute(
            """UPDATE runs SET
                 enrich_attempted=?, enrich_from_cache=?, enrich_fresh=?,
                 cost_bright_data_usd=?, cost_rapidapi_usd=?, total_cost_usd=?,
                 status=?, error=?, finished_at=datetime('now')
               WHERE id=?""",
            (
                enrich_attempted, enrich_from_cache, enrich_fresh,
                cost_bright_data_usd, cost_rapidapi_usd, total,
                status, error, run_id,
            ),
        )


def recent_runs(limit: int = 100) -> List[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def aggregate_stats() -> dict:
    """Return total runs, total cost, avg cost per run, # users."""
    with _conn() as c:
        runs_row = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total_cost_usd), 0) AS total_cost "
            "FROM runs WHERE status='ok'"
        ).fetchone()
        users_row = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        # Cache savings: enrich_from_cache * cost_per_record (rough)
        cache_row = c.execute(
            "SELECT COALESCE(SUM(enrich_from_cache), 0) AS hits, "
            "COALESCE(SUM(enrich_fresh), 0) AS fresh FROM runs"
        ).fetchone()
    total_runs = int(runs_row["n"])
    total_cost = float(runs_row["total_cost"])
    return {
        "total_runs": total_runs,
        "total_cost_usd": round(total_cost, 4),
        "avg_cost_usd": round(total_cost / total_runs, 4) if total_runs else 0,
        "user_count": int(users_row["n"]),
        "cache_hits": int(cache_row["hits"]),
        "fresh_fetches": int(cache_row["fresh"]),
    }


def add_run_event(run_id: int, event_type: str, data: Optional[str] = None) -> None:
    """Append an event for a run (small JSON or string)."""
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO run_events(run_id, event_type, data) VALUES(?,?,?)",
            (run_id, event_type, data),
        )


def get_run_events(run_id: int, since_id: int = 0) -> List[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id ASC",
            (run_id, since_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run_by_id(run_id: int) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_runs_for_user(email: str, limit: int = 50) -> List[dict]:
    email = (email or "").strip().lower()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE user_email=? ORDER BY started_at DESC LIMIT ?",
            (email, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---- bootstrap ----

def bootstrap_admin_if_empty() -> Optional[str]:
    """Create the initial admin user if `users` is empty. Returns admin email if created."""
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if n > 0:
        return None
    admin_email = os.environ.get("ADMIN_EMAIL", "arashyazd@gmail.com").strip().lower()
    env_pw = os.environ.get("ADMIN_PASSWORD") or os.environ.get("DASHBOARD_PASSWORD")
    # SECURITY: never fall back to a well-known default ('letmein'). If no password is
    # provided via env, generate a strong random one and log it ONCE so the operator
    # can capture it, then set ADMIN_PASSWORD in the environment going forward.
    generated = False
    if env_pw:
        admin_pw = env_pw
    else:
        admin_pw = secrets.token_urlsafe(16)
        generated = True
    try:
        create_user(admin_email, admin_pw, daily_cap=10_000, is_admin=True)
        if generated:
            logger.error(
                "Bootstrapped admin '%s' with a RANDOM password (ADMIN_PASSWORD not set): %s\n"
                "  -> Save it now and set ADMIN_PASSWORD in your environment. "
                "Change it from the admin panel.",
                admin_email, admin_pw,
            )
        else:
            logger.info(
                "Bootstrapped admin '%s' from ADMIN_PASSWORD/DASHBOARD_PASSWORD env.",
                admin_email,
            )
        return admin_email
    except Exception as e:
        logger.error("Admin bootstrap failed: %s", e)
        return None
