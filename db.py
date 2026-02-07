import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Any

# -------------------- Paths (Render Persistent Disk) --------------------
DEFAULT_DISK_MOUNT = os.environ.get("CPF_DISK_MOUNT", "/var/data")

DB_PATH = Path(os.environ.get("CPF_DB_PATH", str(Path(DEFAULT_DISK_MOUNT) / "cpf.db")))
BACKUP_DIR = Path(os.environ.get("CPF_BACKUP_DIR", str(Path(DEFAULT_DISK_MOUNT) / "backups")))
UPLOAD_DIR = Path(os.environ.get("CPF_UPLOAD_DIR", str(Path(DEFAULT_DISK_MOUNT) / "uploads")))

# Ensure dirs exist
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_SCHEMA_READY = False


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _raw_conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass
    return c


def _table_exists(c: sqlite3.Connection, table: str) -> bool:
    row = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(c: sqlite3.Connection, table: str) -> List[str]:
    try:
        rows = c.execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in rows]
    except Exception:
        return []


def _add_column_if_missing(c: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    cols = _table_columns(c, table)
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    """Create base schema + run gentle migrations. Safe to call many times."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    c = _raw_conn()

    # --- Settings / Logs (for small config and debugging) ---
    c.execute(
        """CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            msg TEXT NOT NULL
        )"""
    )

    # --- Chambers ---
    c.execute(
        """CREATE TABLE IF NOT EXISTS chambers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            province TEXT,
            city TEXT,
            created_at TEXT NOT NULL
        )"""
    )

    # --- Users ---
    c.execute(
        """CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            company TEXT,
            phone TEXT,
            chamber_id INTEGER,
            role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(chamber_id) REFERENCES chambers(id)
        )"""
    )

    # --- Requirements ---
    c.execute(
        """CREATE TABLE IF NOT EXISTS requirements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,              -- 'need' / 'offer' (UI lo muestra como Necesidad/Oferta)
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT,
            urgency TEXT,
            tags TEXT,
            status TEXT NOT NULL DEFAULT 'open',  -- open/closed
            company TEXT,
            location TEXT,
            chamber_id INTEGER,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            -- legacy/compat:
            rtype TEXT,
            chamber TEXT,
            created_by INTEGER,
            created_by_name TEXT,
            created_by_company TEXT,
            created_by_email TEXT,
            created_on TEXT,
            FOREIGN KEY(chamber_id) REFERENCES chambers(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )"""
    )

    # --- Attachments ---
    c.execute(
        """CREATE TABLE IF NOT EXISTS attachments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id INTEGER NOT NULL,
            uploaded_by_user_id INTEGER,
            filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            mime TEXT,
            size INTEGER,
            created_at TEXT NOT NULL,
            -- legacy/compat:
            original_name TEXT,
            stored_name TEXT,
            mime_type TEXT,
            size_bytes INTEGER,
            uploaded_by INTEGER,
            FOREIGN KEY(requirement_id) REFERENCES requirements(id),
            FOREIGN KEY(uploaded_by_user_id) REFERENCES users(id)
        )"""
    )

    # --- Contact requests ---
    c.execute(
        """CREATE TABLE IF NOT EXISTS contact_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            requirement_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending/accepted/declined
            created_at TEXT NOT NULL,
            responded_at TEXT,
            -- legacy/compat:
            from_name TEXT,
            from_company TEXT,
            from_email TEXT,
            message TEXT,
            FOREIGN KEY(from_user_id) REFERENCES users(id),
            FOREIGN KEY(to_user_id) REFERENCES users(id),
            FOREIGN KEY(requirement_id) REFERENCES requirements(id)
        )"""
    )

    _migrate_schema(c)

    c.commit()
    c.close()
    _SCHEMA_READY = True


def _migrate_schema(c: sqlite3.Connection) -> None:
    """Best-effort migrations to support old DBs without destroying data."""
    # Users: add columns if old table exists without them
    if _table_exists(c, "users"):
        _add_column_if_missing(c, "users", "phone", "phone TEXT")
        _add_column_if_missing(c, "users", "chamber_id", "chamber_id INTEGER")
        _add_column_if_missing(c, "users", "is_active", "is_active INTEGER NOT NULL DEFAULT 1")
        # Some very old DBs used 'password' column name
        cols = _table_columns(c, "users")
        if "password_hash" not in cols and "password" in cols:
            _add_column_if_missing(c, "users", "password_hash", "password_hash TEXT")
            # copy if possible (password may already be a hash in some variants)
            try:
                c.execute("UPDATE users SET password_hash = password WHERE password_hash IS NULL")
            except Exception:
                pass
        # ensure role exists
        _add_column_if_missing(c, "users", "role", "role TEXT NOT NULL DEFAULT 'user'")
        _add_column_if_missing(c, "users", "company", "company TEXT")
        _add_column_if_missing(c, "users", "name", "name TEXT")
        _add_column_if_missing(c, "users", "created_at", "created_at TEXT")

    # Chambers: province/city
    if _table_exists(c, "chambers"):
        _add_column_if_missing(c, "chambers", "province", "province TEXT")
        _add_column_if_missing(c, "chambers", "city", "city TEXT")
        _add_column_if_missing(c, "chambers", "created_at", "created_at TEXT")

    # Requirements: align columns
    if _table_exists(c, "requirements"):
        cols = _table_columns(c, "requirements")
        # Mandatory columns used by services.py
        _add_column_if_missing(c, "requirements", "type", "type TEXT")
        _add_column_if_missing(c, "requirements", "title", "title TEXT")
        _add_column_if_missing(c, "requirements", "description", "description TEXT")
        _add_column_if_missing(c, "requirements", "category", "category TEXT")
        _add_column_if_missing(c, "requirements", "urgency", "urgency TEXT")
        _add_column_if_missing(c, "requirements", "tags", "tags TEXT")
        _add_column_if_missing(c, "requirements", "status", "status TEXT NOT NULL DEFAULT 'open'")
        _add_column_if_missing(c, "requirements", "company", "company TEXT")
        _add_column_if_missing(c, "requirements", "location", "location TEXT")
        _add_column_if_missing(c, "requirements", "chamber_id", "chamber_id INTEGER")
        _add_column_if_missing(c, "requirements", "user_id", "user_id INTEGER")
        _add_column_if_missing(c, "requirements", "created_at", "created_at TEXT")
        _add_column_if_missing(c, "requirements", "updated_at", "updated_at TEXT")

        # Legacy columns that may exist
        _add_column_if_missing(c, "requirements", "rtype", "rtype TEXT")
        _add_column_if_missing(c, "requirements", "chamber", "chamber TEXT")
        _add_column_if_missing(c, "requirements", "created_by", "created_by INTEGER")
        _add_column_if_missing(c, "requirements", "created_by_company", "created_by_company TEXT")
        _add_column_if_missing(c, "requirements", "created_on", "created_on TEXT")

        # Fill new columns from legacy if needed
        # type <- rtype
        try:
            if "rtype" in cols:
                c.execute(
                    "UPDATE requirements SET type = rtype WHERE (type IS NULL OR type='') AND rtype IS NOT NULL"
                )
        except Exception:
            pass
        # rtype <- type (if we have it)
        try:
            c.execute(
                "UPDATE requirements SET rtype = type WHERE (rtype IS NULL OR rtype='') AND type IS NOT NULL"
            )
        except Exception:
            pass

        # Map Spanish/legacy values to canonical used by UI
        try:
            c.execute(
                """UPDATE requirements
                   SET type = CASE
                       WHEN LOWER(type) IN ('oferta','offer') THEN 'offer'
                       WHEN LOWER(type) IN ('necesidad','need') THEN 'need'
                       ELSE type
                   END
                """
            )
        except Exception:
            pass
        try:
            c.execute(
                """UPDATE requirements
                   SET status = CASE
                       WHEN LOWER(status) IN ('abierto','open') THEN 'open'
                       WHEN LOWER(status) IN ('cerrado','closed') THEN 'closed'
                       ELSE status
                   END
                """
            )
        except Exception:
            pass

        # user_id <- created_by
        try:
            c.execute(
                "UPDATE requirements SET user_id = created_by WHERE (user_id IS NULL OR user_id=0) AND created_by IS NOT NULL"
            )
        except Exception:
            pass
        # company <- created_by_company
        try:
            c.execute(
                "UPDATE requirements SET company = created_by_company WHERE (company IS NULL OR company='') AND created_by_company IS NOT NULL"
            )
        except Exception:
            pass
        # created_at <- created_on
        try:
            c.execute(
                "UPDATE requirements SET created_at = created_on WHERE (created_at IS NULL OR created_at='') AND created_on IS NOT NULL"
            )
        except Exception:
            pass

        # chamber_id from chamber name (best-effort)
        try:
            # create missing chamber rows
            rows = c.execute(
                "SELECT DISTINCT chamber FROM requirements WHERE chamber IS NOT NULL AND chamber<>''"
            ).fetchall()
            for r in rows:
                name = (r["chamber"] or "").strip()
                if not name:
                    continue
                ex = c.execute("SELECT id FROM chambers WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
                if not ex:
                    c.execute(
                        "INSERT OR IGNORE INTO chambers(name, created_at) VALUES(?,?)",
                        (name, now_iso()),
                    )
            # set chamber_id where missing
            c.execute(
                """UPDATE requirements
                   SET chamber_id = (SELECT id FROM chambers ch WHERE LOWER(ch.name)=LOWER(requirements.chamber) LIMIT 1)
                   WHERE (chamber_id IS NULL OR chamber_id=0) AND chamber IS NOT NULL AND chamber<>''"""
            )
        except Exception:
            pass

    # Attachments: align columns
    if _table_exists(c, "attachments"):
        _add_column_if_missing(c, "attachments", "uploaded_by_user_id", "uploaded_by_user_id INTEGER")
        _add_column_if_missing(c, "attachments", "filename", "filename TEXT")
        _add_column_if_missing(c, "attachments", "stored_path", "stored_path TEXT")
        _add_column_if_missing(c, "attachments", "mime", "mime TEXT")
        _add_column_if_missing(c, "attachments", "size", "size INTEGER")
        _add_column_if_missing(c, "attachments", "created_at", "created_at TEXT")

        # Legacy to new
        try:
            c.execute(
                "UPDATE attachments SET filename = original_name WHERE (filename IS NULL OR filename='') AND original_name IS NOT NULL"
            )
        except Exception:
            pass
        try:
            # stored_name might be just a filename; turn into full path
            rows = c.execute(
                "SELECT id, stored_name FROM attachments WHERE (stored_path IS NULL OR stored_path='') AND stored_name IS NOT NULL"
            ).fetchall()
            for r in rows:
                sn = (r["stored_name"] or "").strip()
                if not sn:
                    continue
                sp = sn if "/" in sn else str(UPLOAD_DIR / sn)
                c.execute("UPDATE attachments SET stored_path=? WHERE id=?", (sp, int(r["id"])))
        except Exception:
            pass
        try:
            c.execute(
                "UPDATE attachments SET mime = mime_type WHERE (mime IS NULL OR mime='') AND mime_type IS NOT NULL"
            )
        except Exception:
            pass
        try:
            c.execute(
                "UPDATE attachments SET size = size_bytes WHERE size IS NULL AND size_bytes IS NOT NULL"
            )
        except Exception:
            pass
        try:
            c.execute(
                "UPDATE attachments SET uploaded_by_user_id = uploaded_by WHERE (uploaded_by_user_id IS NULL OR uploaded_by_user_id=0) AND uploaded_by IS NOT NULL"
            )
        except Exception:
            pass

    # Contact requests: align columns
    if _table_exists(c, "contact_requests"):
        _add_column_if_missing(c, "contact_requests", "from_user_id", "from_user_id INTEGER")
        _add_column_if_missing(c, "contact_requests", "to_user_id", "to_user_id INTEGER")
        _add_column_if_missing(c, "contact_requests", "requirement_id", "requirement_id INTEGER")
        _add_column_if_missing(c, "contact_requests", "status", "status TEXT NOT NULL DEFAULT 'pending'")
        _add_column_if_missing(c, "contact_requests", "created_at", "created_at TEXT")
        _add_column_if_missing(c, "contact_requests", "responded_at", "responded_at TEXT")


def conn() -> sqlite3.Connection:
    init_db()
    return _raw_conn()


# -------------------- Settings helpers --------------------
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    c = conn()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else default


def set_setting(key: str, value: Optional[str]) -> None:
    c = conn()
    if value is None:
        c.execute("DELETE FROM settings WHERE key=?", (key,))
    else:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    c.commit()
    c.close()


# -------------------- Logging --------------------
def log(*parts: Any, level: str = "INFO") -> None:
    """Flexible logger: accepts many args and joins them into one message."""
    try:
        msg = " ".join("" if p is None else str(p) for p in parts).strip() or "-"
        c = conn()
        c.execute("INSERT INTO logs(ts, level, msg) VALUES(?,?,?)", (now_iso(), level, msg))
        c.commit()
        c.close()
    except Exception:
        # Never crash the app because of logging
        pass


# -------------------- Backup / Restore --------------------
def get_backup_dir() -> str:
    return str(BACKUP_DIR)


def set_backup_dir(path: str) -> None:
    global BACKUP_DIR
    if not path:
        return
    BACKUP_DIR = Path(path)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    set_setting("backup_dir", str(BACKUP_DIR))


def backup_db(reason: str = "manual") -> str:
    """Create a .db copy inside BACKUP_DIR and store last path in settings."""
    init_db()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"cpf_{ts}_{reason}.db"
    shutil.copy2(DB_PATH, dst)
    set_setting("last_backup_path", str(dst))
    return str(dst)


def list_backups(limit: int = 50) -> List[str]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    items = sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in items[: max(1, int(limit))]]


def get_last_backup_path() -> Optional[str]:
    return get_setting("last_backup_path")


def restore_db_from_path(path: str) -> None:
    """Replace current DB with a provided backup path (best-effort)."""
    global _SCHEMA_READY
    if not path:
        raise ValueError("path vacío")
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(str(src))
    shutil.copy2(src, DB_PATH)
    _SCHEMA_READY = False
    init_db()


# -------------------- Super Admin (simple) --------------------
def get_super_admin_email() -> Optional[str]:
    return get_setting("super_admin_email")


def set_super_admin_email(email: str) -> None:
    if not email:
        return
    set_setting("super_admin_email", (email or "").strip().lower())
