import sqlite3
import os
import secrets
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "IPAM", "users.db")
)

ROLES = ("admin", "operator", "viewer")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables and seed a default admin if none exists."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role         TEXT NOT NULL DEFAULT 'viewer',
                created_at   TEXT DEFAULT (datetime('now')),
                last_login   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invite_tokens (
                token      TEXT PRIMARY KEY,
                role       TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                used       INTEGER DEFAULT 0
            )
        """)
        # Migration: drop expires_at column if it still exists from old schema
        cols = [r[1] for r in conn.execute("PRAGMA table_info(invite_tokens)").fetchall()]
        if "expires_at" in cols:
            conn.execute("""
                CREATE TABLE invite_tokens_new (
                    token      TEXT PRIMARY KEY,
                    role       TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    used       INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                INSERT INTO invite_tokens_new (token, role, created_by, created_at, used)
                SELECT token, role, created_by, created_at, used FROM invite_tokens
            """)
            conn.execute("DROP TABLE invite_tokens")
            conn.execute("ALTER TABLE invite_tokens_new RENAME TO invite_tokens")
        conn.commit()

    # Seed default admin if table is empty
    if not get_user_by_username("admin"):
        create_user("admin", "admin", "admin")
        print("[✔] Default admin user created (username: admin, password: admin) — change this immediately.")


def get_all_users():
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, username, role, created_at, last_login FROM users ORDER BY id"
        ).fetchall()]


def get_user_by_id(user_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def create_user(username, password, role="viewer"):
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), role),
        )
        conn.commit()


def update_user_password(user_id, new_password):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        conn.commit()


def update_user_role(user_id, role):
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    with get_db() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()


def delete_user(user_id):
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


def create_invite(role, created_by):
    """Generate a single-use invite token with no time expiry. Returns the token string."""
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    token = secrets.token_urlsafe(32)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO invite_tokens (token, role, created_by) VALUES (?, ?, ?)",
            (token, role, created_by),
        )
        conn.commit()
    return token


def get_invite(token):
    """Return invite row if token exists and unused. Else None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM invite_tokens WHERE token = ? AND used = 0",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def consume_invite(token):
    """Mark a token as used."""
    with get_db() as conn:
        conn.execute("UPDATE invite_tokens SET used = 1 WHERE token = ?", (token,))
        conn.commit()


def get_all_invites():
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM invite_tokens ORDER BY created_at DESC"
        ).fetchall()]


def revoke_invite(token):
    with get_db() as conn:
        conn.execute("DELETE FROM invite_tokens WHERE token = ?", (token,))
        conn.commit()


def verify_password(username, password):
    """Returns user dict if credentials are valid, else None."""
    user = get_user_by_username(username)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


def record_login(user_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), user_id),
        )
        conn.commit()
