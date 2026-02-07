import bcrypt

from db import (
    conn,
    now_iso,
    log,
    get_super_admin_email,
    set_super_admin_email,
)


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def get_user_by_email(email: str):
    c = conn()
    row = c.execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    c.close()
    return row


def create_user(email, password, name, company, phone, chamber_id, role="user"):
    email_n = email.strip().lower()
    c = conn()
    c.execute(
        """INSERT INTO users(email, password_hash, name, company, phone, chamber_id, role, created_at)
             VALUES(?,?,?,?,?,?,?,?)""",
        (
            email_n,
            hash_password(password),
            name.strip(),
            company.strip(),
            (phone or "").strip(),
            chamber_id,
            role,
            now_iso(),
        ),
    )
    c.commit()
    user_id = c.execute("SELECT id FROM users WHERE email = ?", (email_n,)).fetchone()[
        "id"
    ]
    c.close()

    # Si es el primer admin creado, lo registramos como "Super Admin"
    if role == "admin" and not get_super_admin_email():
        set_super_admin_email(email_n)

    log(user_id, "user_created", f"role={role}")
    return user_id


def authenticate(email, password):
    u = get_user_by_email(email)
    if not u or not u["is_active"]:
        return None
    if verify_password(password, u["password_hash"]):
        return dict(u)
    return None


def any_admin_exists():
    c = conn()
    row = c.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone()
    c.close()
    return row is not None


def is_super_admin(email: str) -> bool:
    """True si el email corresponde al Super Admin configurado."""
    try:
        return (email or "").strip().lower() == (get_super_admin_email() or "").strip().lower()
    except Exception:
        return False