import re
import uuid
from typing import Any, Dict, List, Optional

from db import UPLOAD_DIR, conn, now_iso


def _safe_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "archivo"


# -------------------- Cámaras --------------------
def list_chambers() -> List[dict]:
    c = conn()
    rows = c.execute(
        "SELECT id, name, province, city FROM chambers ORDER BY name"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def create_chamber(name: str, location: Optional[str] = None) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    province = None
    city = None
    if location:
        parts = re.split(r"\s*[/-]\s*", location.strip(), maxsplit=1)
        if len(parts) == 2:
            city, province = parts[0].strip() or None, parts[1].strip() or None
        else:
            city = location.strip()

    c = conn()
    cur = c.cursor()
    exists = cur.execute("SELECT 1 FROM chambers WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
    if exists:
        c.close()
        return False
    cur.execute(
        "INSERT INTO chambers(name, province, city, created_at) VALUES(?,?,?,?)",
        (name, province, city, now_iso()),
    )
    c.commit()
    c.close()
    return True


# -------------------- Requerimientos --------------------
def create_requirement(
    type_: str,
    title: str,
    description: str,
    user_id: int,
    company: str,
    chamber_id: Optional[int] = None,
    location: Optional[str] = None,
    category: Optional[str] = None,
    urgency: str = "Media",
    tags: str = "",
) -> int:
    c = conn()
    cur = c.cursor()
    cur.execute(
        """INSERT INTO requirements(type, title, description, category, urgency, tags, status,
                                     company, location, chamber_id, user_id, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            type_,
            title.strip(),
            description.strip(),
            category,
            urgency,
            (tags or "").strip(),
            "open",
            company.strip(),
            location,
            chamber_id,
            int(user_id),
            now_iso(),
        ),
    )
    req_id = int(cur.lastrowid)
    c.commit()
    c.close()
    return req_id


def update_requirement(
    req_id: int,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    urgency: Optional[str] = None,
    tags: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    fields = {}
    if title is not None:
        fields["title"] = title.strip()
    if description is not None:
        fields["description"] = description.strip()
    if category is not None:
        fields["category"] = category
    if urgency is not None:
        fields["urgency"] = urgency
    if tags is not None:
        fields["tags"] = (tags or "").strip()
    if status is not None:
        fields["status"] = status

    if not fields:
        return

    fields["updated_at"] = now_iso()

    keys = list(fields.keys())
    sets = ", ".join([f"{k}=?" for k in keys])
    vals = [fields[k] for k in keys] + [int(req_id)]

    c = conn()
    c.execute(f"UPDATE requirements SET {sets} WHERE id=?", vals)
    c.commit()
    c.close()


def get_requirement(req_id: int) -> Optional[dict]:
    c = conn()
    row = c.execute(
        """SELECT r.*, u.name AS user_name, u.email AS user_email, u.phone AS user_phone,
                  ch.name AS chamber_name
             FROM requirements r
             JOIN users u ON u.id = r.user_id
             LEFT JOIN chambers ch ON ch.id = r.chamber_id
             WHERE r.id=?""",
        (int(req_id),),
    ).fetchone()
    c.close()
    return dict(row) if row else None


def search_requirements(
    q: str = "",
    type_: str = "(Todos)",
    status: str = "open",
    chamber_id: Optional[int] = None,
    limit: int = 200,
) -> List[dict]:
    q = (q or "").strip()
    sql = """SELECT r.id, r.type, r.title, r.description, r.category, r.urgency, r.tags,
                    r.status, r.company, r.location, r.chamber_id, r.user_id, r.created_at,
                    ch.name AS chamber_name
             FROM requirements r
             LEFT JOIN chambers ch ON ch.id = r.chamber_id
             WHERE 1=1"""
    params: List[Any] = []

    if status:
        sql += " AND r.status=?"
        params.append(status)

    if type_ and type_ != "(Todos)":
        sql += " AND r.type=?"
        params.append(type_)

    if chamber_id:
        sql += " AND r.chamber_id=?"
        params.append(int(chamber_id))

    if q:
        like = f"%{q.lower()}%"
        sql += """ AND (
                    LOWER(r.title) LIKE ? OR
                    LOWER(r.description) LIKE ? OR
                    LOWER(r.company) LIKE ? OR
                    LOWER(COALESCE(r.tags,'')) LIKE ?
                )"""
        params.extend([like, like, like, like])

    sql += " ORDER BY r.created_at DESC LIMIT ?"
    params.append(int(limit))

    c = conn()
    rows = c.execute(sql, params).fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_user_requirements(user_id: int, limit: int = 200) -> List[dict]:
    c = conn()
    rows = c.execute(
        """SELECT id, type, title, description, category, urgency, tags, status, created_at, updated_at
           FROM requirements
           WHERE user_id=?
           ORDER BY created_at DESC
           LIMIT ?""",
        (int(user_id), int(limit)),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


# -------------------- Adjuntos --------------------
def save_attachment(
    requirement_id: int,
    uploaded_by_user_id: int,
    filename: str,
    content: bytes,
    mime: Optional[str] = None,
) -> int:
    UPLOAD_DIR.mkdir(exist_ok=True)

    safe = _safe_filename(filename)
    unique = f"r{int(requirement_id)}_{uuid.uuid4().hex}_{safe}"
    stored_path = str((UPLOAD_DIR / unique).as_posix())

    with open(stored_path, "wb") as f:
        f.write(content)

    c = conn()
    cur = c.cursor()
    cur.execute(
        """INSERT INTO attachments(requirement_id, uploaded_by_user_id, filename, stored_path, mime, size, created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            int(requirement_id),
            int(uploaded_by_user_id),
            filename,
            stored_path,
            mime,
            len(content) if content is not None else None,
            now_iso(),
        ),
    )
    att_id = int(cur.lastrowid)
    c.commit()
    c.close()
    return att_id


def list_attachments(requirement_id: int) -> List[dict]:
    c = conn()
    rows = c.execute(
        """SELECT id, filename, stored_path, mime, size, created_at, uploaded_by_user_id
           FROM attachments
           WHERE requirement_id=?
           ORDER BY created_at ASC""",
        (int(requirement_id),),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


# -------------------- Solicitudes de contacto --------------------
def create_contact_request(from_user_id: int, to_user_id: int, requirement_id: int) -> int:
    c = conn()
    cur = c.cursor()

    existing = cur.execute(
        """SELECT id FROM contact_requests
           WHERE from_user_id=? AND to_user_id=? AND requirement_id=? AND status='pending'""",
        (int(from_user_id), int(to_user_id), int(requirement_id)),
    ).fetchone()
    if existing:
        c.close()
        return int(existing["id"])

    cur.execute(
        """INSERT INTO contact_requests(from_user_id, to_user_id, requirement_id, status, created_at)
           VALUES(?,?,?,?,?)""",
        (int(from_user_id), int(to_user_id), int(requirement_id), "pending", now_iso()),
    )
    rid = int(cur.lastrowid)
    c.commit()
    c.close()
    return rid


def list_inbox(user_id: int, status: str = "pending", limit: int = 200) -> List[dict]:
    c = conn()
    rows = c.execute(
        """SELECT cr.id, cr.status, cr.created_at, cr.responded_at,
                  r.id AS requirement_id, r.title, r.type, r.company,
                  u.id AS from_user_id, u.name AS from_name, u.email AS from_email, u.phone AS from_phone
           FROM contact_requests cr
           JOIN requirements r ON r.id = cr.requirement_id
           JOIN users u ON u.id = cr.from_user_id
           WHERE cr.to_user_id=? AND cr.status=?
           ORDER BY cr.created_at DESC
           LIMIT ?""",
        (int(user_id), status, int(limit)),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def respond_contact_request(request_id: int, status: str) -> None:
    if status not in ("accepted", "declined"):
        raise ValueError("status inválido")
    c = conn()
    c.execute(
        "UPDATE contact_requests SET status=?, responded_at=? WHERE id=?",
        (status, now_iso(), int(request_id)),
    )
    c.commit()
    c.close()


# -------------------- Métricas --------------------
def admin_metrics() -> Dict[str, Any]:
    c = conn()
    cur = c.cursor()

    users = cur.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    reqs = cur.execute("SELECT COUNT(*) AS n FROM requirements").fetchone()["n"]
    open_reqs = cur.execute("SELECT COUNT(*) AS n FROM requirements WHERE status='open'").fetchone()["n"]
    contacts_pending = cur.execute("SELECT COUNT(*) AS n FROM contact_requests WHERE status='pending'").fetchone()["n"]
    contacts_accepted = cur.execute("SELECT COUNT(*) AS n FROM contact_requests WHERE status='accepted'").fetchone()["n"]

    by_ch = cur.execute(
        """SELECT COALESCE(ch.name,'(Sin cámara)') AS chamber,
                  COUNT(r.id) AS total
           FROM requirements r
           LEFT JOIN chambers ch ON ch.id = r.chamber_id
           GROUP BY COALESCE(ch.name,'(Sin cámara)')
           ORDER BY total DESC"""
    ).fetchall()

    c.close()
    return {
        "users": int(users),
        "requirements": int(reqs),
        "open_requirements": int(open_reqs),
        "contacts_pending": int(contacts_pending),
        "contacts_accepted": int(contacts_accepted),
        "requirements_by_chamber": [dict(r) for r in by_ch],
    }