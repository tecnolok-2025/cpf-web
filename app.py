import streamlit as st
import re

def _uget(u, key, default=None):
    """Lee un campo de usuario soportando dict o sqlite3.Row."""
    if u is None:
        return default
    try:
        if hasattr(u, "get"):
            return u.get(key, default)
    except Exception:
        pass
    try:
        return u[key]
    except Exception:
        try:
            return dict(u).get(key, default)
        except Exception:
            return default
def _norm_text(s: str) -> str:
    import unicodedata
    s = s or ""
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    return s.casefold()

# Lista MUY acotada de insultos graves (evitamos falsos positivos).
# Si necesitás ampliarla, lo hacemos con criterio y pruebas.
_OFFENSIVE_WORDS = [
    "pelotudo", "pelotuda",
    "boludo", "boluda",
    "idiota",
    "imbecil", "imbécil",
    "puto", "puta",
    "mierda",
]

def detect_offensive_words(text: str):
    """Devuelve lista de coincidencias: [{'word':..., 'start':..., 'end':...}]"""
    t = text or ""
    nt = _norm_text(t)
    matches = []
    for w in _OFFENSIVE_WORDS:
        nw = _norm_text(w)
        # buscar como palabra completa (bordes no alfanuméricos)
        for m in re.finditer(rf"(?<![\w]){re.escape(nw)}(?![\w])", nt):
            matches.append({"word": w, "start": m.start(), "end": m.end()})
    # ordenar y deduplicar por rango
    matches = sorted(matches, key=lambda x: (x["start"], x["end"]))
    dedup = []
    last_end = -1
    for mm in matches:
        if mm["start"] >= last_end:
            dedup.append(mm)
            last_end = mm["end"]
    return dedup

def highlight_offensive(text: str, matches):
    """Devuelve HTML con <mark> para mostrar dónde está el problema."""
    if not matches:
        return text
    t = text or ""
    # Como matches están sobre texto normalizado, hacemos highlight aproximado:
    # reconstruimos por búsqueda sobre original con normalización por ventanas.
    nt = _norm_text(t)
    spans = [(m["start"], m["end"]) for m in matches]
    out = []
    last = 0
    for s,e in spans:
        out.append(t[last:s])
        out.append(f"<mark>{t[s:e]}</mark>")
        last = e
    out.append(t[last:])
    return "".join(out)
import pandas as pd
import datetime
from pathlib import Path

import services as svc
from db import backup_db, list_backups, get_backup_dir, set_backup_dir, get_last_backup_path, restore_db_from_path, get_super_admin_email
from auth import any_admin_exists, create_user, authenticate, is_super_admin, is_super_admin

try:
    from ai import assistant_answer, review_requirement
except Exception:
    def assistant_answer(q: str, role: str = "user"):
        return {"answer": "Asistente IA no disponible (ai.py con error).", "table": None}

    def review_requirement(title: str, description: str):
        """Fallback: revisión simple local sin IA (evita falsos positivos)."""
        text = f"{title}\n{description}".lower()
        # Lista corta de insultos comunes (ajustable)
        bad_words = [
            "idiota", "imbecil", "imbécil", "estupido", "estúpido", "pelotudo", "pelotuda",
            "mierda", "puta", "puto", "carajo", "concha", "tonto", "boludo", "boluda"
        ]
        matches = [w for w in bad_words if re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE)]
        if matches:
            return {
                "allowed": False,
                "reason": "El texto contiene palabras ofensivas.",
                "matches": sorted(set(matches)),
            }
        return {"allowed": True, "reason": "OK", "matches": []}


def _get_user():
    return st.session_state.get("user")


def _maybe_auto_backup(reason: str = "logout"):
    """Backup automático SOLO para Super Admin, al cerrar sesión.

    Nota: en Render Free el disco no es persistente; si querés conservarlo, descargalo
    o usá un disco persistente/plan pago.
    """
    u = st.session_state.get("user")
    if not u:
        return
    if _uget(u, 'role') != "admin":
        return
    if not is_super_admin(_uget(u, 'email', "")):
        return
    if reason != "logout":
        return

    done_key = f"_auto_backup_done_{reason}"
    if st.session_state.get(done_key):
        return

    try:
        b = backup_db(reason=reason)
        if b.get("ok"):
            st.session_state["_last_backup"] = b
            st.session_state[done_key] = True
    except Exception as e:
        st.session_state["_last_backup_err"] = str(e)


def _backup_download_ui():
    """UI de resguardo (solo Super Admin)."""
    u = st.session_state.get("user")
    if not u or _uget(u, 'role') != "admin" or not is_super_admin(_uget(u, 'email', "")):
        return

    st.caption(f"Super Admin: {get_super_admin_email() or '-'}")

    cur_dir = get_backup_dir()
    new_dir = st.text_input(
        "Directorio de backups",
        value=cur_dir,
        help="En PC/local podés elegir cualquier carpeta. En Render el filesystem puede ser efímero salvo disco persistente.",
    )
    if new_dir and new_dir != cur_dir and st.button("Guardar directorio"):
        set_backup_dir(new_dir)
        st.success("Directorio actualizado.")

    if st.button("Crear backup ahora"):
        b = backup_db(reason="manual")
        st.session_state["_last_backup"] = b
        st.success("Backup generado.")

    b = st.session_state.get("_last_backup")
    if b and b.get("ok"):
        st.download_button(
            "Descargar último backup (.db)",
            data=b["bytes"],
            file_name=b.get("filename","cpf_backup.db"),
            mime="application/octet-stream",
            use_container_width=True,
        )
    else:
        st.info("Todavía no hay un backup generado en esta sesión.")

    st.divider()
    st.subheader("Restaurar (solo Super Admin)")
    backups = list_backups()
    pick = st.selectbox("Backups locales", options=["(ninguno)"] + backups, format_func=lambda p: p if p=="(ninguno)" else Path(p).name)
    up = st.file_uploader("O subir un backup .db", type=["db"])
    if st.button("♻️ Restaurar ahora", use_container_width=True):
        try:
            if up is not None:
                tmp_path = Path("Resguardo") / f"uploaded_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                tmp_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path.write_bytes(up.getvalue())
                restore_db_from_path(str(tmp_path))
                st.success("Restaurado. Recargando…")
                st.rerun()
            elif pick and pick != "(ninguno)":
                restore_db_from_path(pick)
                st.success("Restaurado. Recargando…")
                st.rerun()
            else:
                st.warning("Seleccioná o subí un backup.")
        except Exception as e:
            st.error(f"No se pudo restaurar: {e}")


def _logout():
    st.session_state.pop("user", None)
    st.rerun()


def _assistant_sidebar(role: str):
    st.divider()
    with st.expander("Asistente IA", expanded=False):
        st.caption("Consultas rápidas sobre el sistema y el contenido (modo local/IA).")
        st.session_state.setdefault("assistant_history", [])
        st.session_state.setdefault("assistant_q", "")

        def _send():
            q = (st.session_state.get("assistant_q") or "").strip()
            if not q:
                return
            out = assistant_answer(q, role=role)
            ans = out.get("answer", "")
            st.session_state["assistant_history"].append({"role": "user", "content": q})
            st.session_state["assistant_history"].append({"role": "assistant", "content": ans})
            st.session_state["assistant_q"] = ""

        st.text_input("Tu pregunta", key="assistant_q", placeholder="Ej: ¿cómo publico? ¿métricas? ¿qué hace la bandeja?")
        st.button("Enviar", on_click=_send, key="assistant_send")

        hist = st.session_state["assistant_history"]
        if hist:
            st.write("---")
            for msg in hist[-6:]:
                if msg["role"] == "user":
                    st.markdown(f"**Vos:** {msg['content']}")
                else:
                    st.markdown(f"**Asistente:** {msg['content']}")


def _login_ui():
    st.subheader("Iniciar sesión")
    with st.form("login_form"):
        email = st.text_input("Correo electrónico")
        password = st.text_input("Contraseña", type="password")
        ok = st.form_submit_button("Ingresar")
        if ok:
            u = authenticate(email, password)
            if u:
                st.session_state["user"] = u
                st.success("Sesión iniciada.")
                st.rerun()
            else:
                st.error("Credenciales inválidas o usuario inactivo.")


def _register_ui():
    st.subheader("Registrarse")
    chambers = svc.list_chambers()
    chamber_names = [c["name"] for c in chambers]

    with st.form("register_form"):
        email = st.text_input("Correo electrónico")
        password = st.text_input("Contraseña", type="password")
        name = st.text_input("Nombre y Apellido")
        company = st.text_input("Empresa")
        phone = st.text_input("Teléfono (opcional)")
        ch = st.selectbox("Cámara (opcional)", ["(Sin cámara)"] + chamber_names)
        ok = st.form_submit_button("Crear usuario")

        if ok:
            chamber_id = None
            if ch != "(Sin cámara)":
                for c in chambers:
                    if c["name"] == ch:
                        chamber_id = c["id"]
                        break
            try:
                create_user(email, password, name, company, phone=phone or None, role="user", chamber_id=chamber_id)
                st.success("Usuario creado. Ahora podés iniciar sesión.")
            except Exception as e:
                st.error(str(e))


def _admin_bootstrap_ui():
    st.info("No existe usuario Admin. Creá el Admin inicial para habilitar el sistema.")

    chambers = svc.list_chambers()
    chamber_names = [c["name"] for c in chambers]

    with st.form("bootstrap_admin"):
        email = st.text_input("Correo electrónico (Administrador)")
        password = st.text_input("Contraseña", type="password")
        name = st.text_input("Nombre y Apellido")
        company = st.text_input("Empresa")
        phone = st.text_input("Teléfono (opcional)")
        ch = st.selectbox("Cámara (opcional)", ["(Sin cámara)"] + chamber_names)
        ok = st.form_submit_button("Crear administrador")

        if ok:
            chamber_id = None
            if ch != "(Sin cámara)":
                for c in chambers:
                    if c["name"] == ch:
                        chamber_id = c["id"]
                        break
            try:
                user_id = create_user(email, password, name, company, phone=phone or None, role="admin", chamber_id=chamber_id)
                st.session_state["user"] = {
                    "id": user_id,
                    "email": email.strip().lower(),
                    "name": name.strip(),
                    "company": company.strip(),
                    "phone": phone.strip() if phone else None,
                    "role": "admin",
                    "chamber_id": chamber_id,
                    "is_active": 1,
                }
                st.success("Admin creado. Ya estás dentro.")
                st.rerun()
            except Exception as e:
                st.error(str(e))


def main():
    st.set_page_config(page_title="CPF – Sistema de Requerimientos", layout="wide")

    with st.sidebar:
        st.title("Sesión")

        if not any_admin_exists():
            _admin_bootstrap_ui()
            _assistant_sidebar(role="anon")
            return

        u = _get_user()
        if not u:
            c1, c2 = st.columns(2)
            with c1:
                _login_ui()
            with c2:
                _register_ui()
            _assistant_sidebar(role="anon")
            return

        st.success(f"Usuario: {u['name']}")
        st.write(f"Empresa: {u['company']}")
        st.write(f"Rol: {u['role']}")

        # Backup automático al ingresar (solo admin)
        # _maybe_auto_backup("login")  # (deshabilitado: backup sólo al cerrar sesión)

        if u["role"] == "admin" and is_super_admin(_uget(u, 'email', "")):
            st.markdown("---")
            st.subheader("Resguardo (solo Néstor Manucci / Super Admin)")
            _backup_download_ui()
            st.markdown("---")

            if st.session_state.get("_logout_confirm"):
                st.warning("Se generó un backup al intentar cerrar sesión. Si querés, descargalo y luego confirmá.")
                if st.button("✅ Confirmar cerrar sesión", use_container_width=True):
                    _logout()
            else:
                st.button("Cerrar sesión", on_click=_start_logout_with_backup, use_container_width=True)
        else:
            st.button("Cerrar sesión", on_click=_logout, use_container_width=True)

        _assistant_sidebar(role=u["role"])

    st.title("CPF – Sistema de Requerimientos (sin precios)")
    st.caption("Prototipo: publicar OFERTAS/NECESIDADES, navegar, buscar y solicitar contacto. Negociación y precio: fuera del sistema.")

    role = u["role"] if u else "anon"
    t = st.tabs(["Navegar", "Publicar", "Bandeja", "Panel", "Asistente IA"])

    with t[0]:
        st.header("Requisitos del navegador")

        chambers = svc.list_chambers()
        chamber_options = ["(Todas)"] + [c["name"] for c in chambers]
        chamber_sel = st.selectbox("Cámara", chamber_options)
        chamber_id = None
        if chamber_sel != "(Todas)":
            for c in chambers:
                if c["name"] == chamber_sel:
                    chamber_id = c["id"]
                    break

        q = st.text_input("Buscar (producto/palabra clave/empresa/persona/tags)")
        tipo = st.selectbox("Tipo", ["(Todos)", "need", "offer"],
                            format_func=lambda x: {"(Todos)": "(Todos)", "need": "Necesidad", "offer": "Oferta"}.get(x, x))
        status = st.selectbox("Estado", ["open", "closed"],
                              format_func=lambda x: {"open": "abierto", "closed": "cerrado"}.get(x, x))

        reqs = svc.search_requirements(q=q, type_=tipo, status=status, chamber_id=chamber_id)

        st.subheader(f"Resultados ({len(reqs)})")
        for r in reqs:
            with st.expander(f"#{r['id']} · {('NECESIDAD' if r['type']=='need' else 'OFERTA')} · {r['title']}"):
                st.write(f"**Empresa:** {r['company']}")
                st.write(f"**Cámara:** {r.get('chamber_name') or '(Sin cámara)'}")
                if r.get("category"):
                    st.write(f"**Categoría:** {r['category']}")
                st.write(f"**Urgencia:** {r.get('urgency','Media')}")
                if r.get("tags"):
                    st.write(f"**Tags:** {r['tags']}")
                st.write(r["description"])

                atts = svc.list_attachments(r["id"])
                if atts:
                    st.write("**Adjuntos:**")
                    for a in atts:
                        st.write(f"- {a['filename']} ({a.get('size','?')} bytes)")

                if u and int(u["id"]) != int(r["user_id"]):
                    if st.button("Solicitar contacto", key=f"contact_{r['id']}"):
                        svc.create_contact_request(from_user_id=u["id"], to_user_id=r["user_id"], requirement_id=r["id"])
                        st.success("Solicitud enviada.")

    with t[1]:
        st.header("Publicar un requerimiento")

        chambers = svc.list_chambers()
        chamber_options = ["(Sin cámara)"] + [c["name"] for c in chambers]

        with st.form("publish_form"):
            type_ = st.selectbox("Tipo", ["need", "offer"],
                                 format_func=lambda x: {"need": "Necesidad", "offer": "Oferta"}[x])
            title = st.text_input("Título")
            desc = st.text_area("Descripción", height=160)

            category = st.selectbox("Categoría (opcional)", ["(Sin categoría)"] + CATEGORIES)
            urgency = st.selectbox("Urgencia", URGENCY, index=1)
            tags = st.text_input("Tags (opcional, separados por coma)")

            chamber_sel = st.selectbox("Cámara (opcional)", chamber_options)
            chamber_id = None
            if chamber_sel != "(Sin cámara)":
                for c in chambers:
                    if c["name"] == chamber_sel:
                        chamber_id = c["id"]
                        break

            location = st.text_input("Ubicación (opcional)")

            files = st.file_uploader(
                "Adjuntar archivos (opcional) — JPG/PNG/PDF/Word/Excel",
                type=["jpg", "jpeg", "png", "pdf", "doc", "docx", "xls", "xlsx"],
                accept_multiple_files=True,
            )

            ok = st.form_submit_button("Publicar")

        if ok:
            if not title.strip() or not desc.strip():
                st.error("Completá Título y Descripción.")
            else:
                rev = review_requirement(title, desc)
                if not rev.get("ok", True):
                    st.error(rev.get("reason", "El texto no pasó la moderación."))
                    if rev.get("hits"):
                        st.write("Palabras detectadas:", ", ".join(rev["hits"]))
                else:
                    final_title = rev.get("suggested_title", title).strip()
                    final_desc = rev.get("suggested_description", desc).strip()
                    final_category = None if category == "(Sin categoría)" else category

                    req_id = svc.create_requirement(
                        type_=type_,
                        title=final_title,
                        description=final_desc,
                        user_id=u["id"],
                        company=u["company"],
                        chamber_id=chamber_id,
                        location=location.strip() or None,
                        category=final_category,
                        urgency=urgency,
                        tags=tags,
                    )

                    if files:
                        for f in files:
                            try:
                                svc.save_attachment(
                                    requirement_id=req_id,
                                    uploaded_by_user_id=u["id"],
                                    filename=f.name,
                                    content=f.getvalue(),
                                    mime=getattr(f, "type", None),
                                )
                            except Exception as e:
                                st.warning(f"No se pudo guardar {f.name}: {e}")

                    st.success(f"Requerimiento publicado con ID #{req_id}.")

    with t[2]:
        st.header("Bandeja")

        st.subheader("Solicitudes de contacto recibidas")
        inbox = svc.list_inbox(u["id"], status="pending")
        if not inbox:
            st.write("No tenés solicitudes pendientes.")
        else:
            for it in inbox:
                with st.expander(f"Solicitud #{it['id']} — {it['from_name']} por #{it['requirement_id']} · {it['title']}"):
                    st.write(f"**Contacto:** {it['from_name']} · {it['from_email']} · {it.get('from_phone') or ''}")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Aceptar", key=f"acc_{it['id']}"):
                            svc.respond_contact_request(it["id"], "accepted")
                            st.success("Aceptada.")
                            st.rerun()
                    with c2:
                        if st.button("Rechazar", key=f"dec_{it['id']}"):
                            svc.respond_contact_request(it["id"], "declined")
                            st.info("Rechazada.")
                            st.rerun()

        st.divider()
        st.subheader("Mis publicaciones (editar/cerrar)")
        mine = svc.list_user_requirements(u["id"])
        if not mine:
            st.write("Todavía no publicaste requerimientos.")
        else:
            for r in mine:
                with st.expander(f"#{r['id']} · {('NECESIDAD' if r['type']=='need' else 'OFERTA')} · {r['title']} ({r['status']})"):
                    with st.form(f"edit_{r['id']}"):
                        title2 = st.text_input("Título", value=r["title"])
                        desc2 = st.text_area("Descripción", value=r["description"], height=120)
                        cat2 = st.selectbox("Categoría", ["(Sin categoría)"] + CATEGORIES,
                                            index=(["(Sin categoría)"] + CATEGORIES).index(r.get("category") or "(Sin categoría)"))
                        urg2 = st.selectbox("Urgencia", URGENCY,
                                            index=URGENCY.index(r.get("urgency", "Media")) if r.get("urgency", "Media") in URGENCY else 1)
                        tags2 = st.text_input("Tags", value=r.get("tags") or "")
                        status2 = st.selectbox("Estado", ["open", "closed"],
                                               index=0 if r["status"] == "open" else 1,
                                               format_func=lambda x: {"open": "abierto", "closed": "cerrado"}[x])
                        save = st.form_submit_button("Guardar cambios")
                        if save:
                            rev = review_requirement(title2, desc2)
                            if not rev.get("ok", True):
                                st.error(rev.get("reason", "El texto no pasó la moderación."))
                            else:
                                svc.update_requirement(
                                    r["id"],
                                    title=rev.get("suggested_title", title2),
                                    description=rev.get("suggested_description", desc2),
                                    category=None if cat2 == "(Sin categoría)" else cat2,
                                    urgency=urg2,
                                    tags=tags2,
                                    status=status2,
                                )
                                st.success("Actualizado.")
                                st.rerun()

    with t[3]:
        st.header("Panel")
        m = svc.admin_metrics()

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Usuarios", m["users"])
        c2.metric("Requerimientos", m["requirements"])
        c3.metric("Abiertos", m["open_requirements"])
        c4.metric("Contactos pendientes", m["contacts_pending"])
        c5.metric("Contactos aceptados", m["contacts_accepted"])

        st.subheader("Requerimientos por cámara")
        if m["requirements_by_chamber"]:
            st.dataframe(pd.DataFrame(m["requirements_by_chamber"]), use_container_width=True)

        if role == "admin":
            st.divider()
            st.subheader("Administración de Cámaras")
            chambers = svc.list_chambers()
            st.dataframe(pd.DataFrame(chambers), use_container_width=True)
            with st.form("add_chamber"):
                nm = st.text_input("Nombre cámara")
                loc = st.text_input("Ciudad/Provincia (opcional)")
                ok2 = st.form_submit_button("Crear cámara")
                if ok2:
                    if svc.create_chamber(nm.strip(), loc.strip() or None):
                        st.success("Cámara creada.")
                        st.rerun()
                    else:
                        st.error("No se pudo crear (¿ya existe?).")

    with t[4]:
        st.header("Asistente IA")
        st.caption("Chat de ayuda sobre el funcionamiento y consultas (modo local/IA).")

        if "chat" not in st.session_state:
            st.session_state["chat"] = []

        for msg in st.session_state["chat"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        q = st.chat_input("Escribí tu consulta…")
        if q:
            st.session_state["chat"].append({"role": "user", "content": q})
            with st.chat_message("user"):
                st.markdown(q)

            out = assistant_answer(q, role=role)
            ans = out.get("answer", "")
            with st.chat_message("assistant"):
                st.markdown(ans)
                if out.get("table") is not None:
                    st.dataframe(pd.DataFrame(out["table"]), use_container_width=True)

            st.session_state["chat"].append({"role": "assistant", "content": ans})



def _start_logout_with_backup():
    """Genera backup (si corresponde) y pide confirmación de cierre de sesión."""
    _maybe_auto_backup("logout")
    st.session_state["_logout_confirm"] = True

if __name__ == "__main__":
    main()