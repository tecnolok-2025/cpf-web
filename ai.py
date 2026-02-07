import os
import re
from typing import Any, Dict

import services as svc


def assistant_answer(q: str, role: str = "user") -> Dict[str, Any]:
    """Asistente dentro del sistema CPF.

    Objetivo: ser flexible, conversacional y práctico.
    - Si existe OPENAI_API_KEY: usa OpenAI.
    - Si no existe: fallback local (sin LLM) pero amigable.
    """

    q = (q or "").strip()
    if not q:
        return {
            "answer": "Decime qué querés hacer o entender (por ej: publicar, buscar, bandeja, panel, backups, métricas).",
            "table": None,
        }

    # Saludos y charla
    if re.fullmatch(
        r"(hola|buenas|buen día|buen dia|buenas tardes|buenas noches|hey|hello|qué tal|que tal|como va|cómo va)[.! ]*",
        q,
        re.I,
    ):
        return {
            "answer": (
                "¡Hola! 🙂\n\n"
                "Estoy acá para ayudarte a usar el sistema como si fuera un copiloto.\n"
                "Contame qué estás intentando hacer y te guío paso a paso.\n\n"
                "Ejemplos de cosas que podés preguntarme:\n"
                "• ‘¿Cómo publico una necesidad?’\n"
                "• ‘¿Cómo busco por empresa o tags?’\n"
                "• ‘No entiendo la bandeja, ¿qué significa?’\n"
                "• ‘Soy admin: ¿cómo hago un backup o recupero uno?’\n"
            ),
            "table": None,
        }

    # OpenAI (si hay API key)
    if os.getenv("OPENAI_API_KEY"):
        try:
            from openai import OpenAI  # type: ignore

            client = OpenAI()
            try:
                stats = svc.get_stats()
            except Exception:
                stats = {}

            system = (
                "Sos un asistente dentro del sistema ‘CPF – Sistema de Requerimientos (sin precios)’. "
                "Ayudás a usuarios a entender y usar el sistema.\n\n"
                "Reglas:\n"
                "- Respondé SIEMPRE en español.\n"
                "- Sé flexible y conversacional (estilo ChatGPT).\n"
                "- Si el usuario no entiende, explicá de otra manera con ejemplos.\n"
                "- Si falta info, hacé 1–2 preguntas concretas.\n"
                "- No inventes datos ni funciones que no existen.\n"
                "- Respuestas prácticas, con pasos.\n"
            )
            extra = f"Estado actual (aprox): {stats}\n" if stats else ""

            messages = [
                {"role": "system", "content": system + extra},
                {"role": "user", "content": f"Rol del usuario: {role}\nConsulta: {q}"},
            ]
            model = os.getenv("CPF_OPENAI_MODEL", "gpt-4o-mini")
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.5,
                max_tokens=500,
            )
            ans = (resp.choices[0].message.content or "").strip()
            if ans:
                return {"answer": ans, "table": None}
        except Exception:
            # Si falla, seguimos con modo local
            pass

    # --------- MODO LOCAL (sin LLM) ----------
    ql = q.lower()

    if any(w in ql for w in ["public", "oferta", "necesidad", "cargar", "crear requer"]):
        return {
            "answer": (
                "Para **publicar** una oferta o necesidad:\n"
                "1) Entrá en la pestaña **Publicar**.\n"
                "2) Elegí el **tipo** (Oferta / Necesidad).\n"
                "3) Completá **título** y **descripción** (lo más claro posible).\n"
                "4) Agregá **tags** (palabras clave) para que te encuentren.\n"
                "5) Guardá.\n\n"
                "Si querés, pegame acá un ejemplo de texto y te lo mejoro para que quede bien publicado."
            ),
            "table": None,
        }

    if "bandeja" in ql or "contact" in ql:
        return {
            "answer": (
                "La **Bandeja** es donde aparecen las interacciones: solicitudes de contacto, seguimientos, etc.\n\n"
                "Decime qué estás viendo (o qué te falta) y te digo qué significa y qué hacer después."
            ),
            "table": None,
        }

    if any(w in ql for w in ["métrica", "metricas", "estad", "panel", "admin"]):
        try:
            stats = svc.get_stats()
            return {
                "answer": "Te muestro métricas generales del sistema:",
                "table": stats if isinstance(stats, dict) else None,
            }
        except Exception:
            return {
                "answer": "Puedo mostrar métricas, pero ahora no pude obtenerlas. ¿Estás logueado como admin?",
                "table": None,
            }

    if any(w in ql for w in ["backup", "resguardo", "restaur", "recuper"]):
        return {
            "answer": (
                "Tema **resguardos/backups**: si sos el *super admin*, vas a ver opciones para:\n"
                "• Crear backup ahora\n"
                "• Descargar el último backup\n"
                "• Restaurar uno anterior (por fecha)\n\n"
                "Decime si querés que te guíe para: **crear**, **descargar** o **restaurar**."
            ),
            "table": None,
        }

    return {
        "answer": (
            "Dale. Para ayudarte bien, decime qué querés lograr.\n\n"
            "Por ejemplo: ‘quiero buscar requerimientos por empresa’, o ‘quiero publicar’, o ‘no entiendo un error’.\n"
            "Si copiás el texto del mensaje o el pantallazo del error, te lo traduzco y te digo qué hacer."
        ),
        "table": None,
    }
