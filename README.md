# CPF – Prototipo Sistema de Requerimientos (sin precios)

Este prototipo implementa:
- Registro + Login (roles: Admin, Cámara, Usuario)
- Alta y navegación de Requerimientos (OFERTA / NECESIDAD)
- Búsqueda y filtros (producto/keyword, empresa, cámara, ubicación, categoría)
- Matching “inteligente” (similaridad de texto TF‑IDF + sugerencias de pares)
- Flujo de “Solicitud de contacto” (sin publicar precios; se habilita contacto si el dueño acepta)
- Panel de control (métricas básicas + distribución por cámaras)

## Stack del prototipo
- Streamlit (UI web)
- SQLite (persistencia local)
- TF‑IDF (scikit-learn) para matching y sugerencias

## Cómo ejecutar (local)
1) Requisitos: Python 3.10+
2) Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```
3) Correr la app:
   ```bash
   streamlit run app.py
   ```
4) Primer inicio: se creará la base `cpf.db` y el sistema te pedirá crear el usuario Admin inicial.

## Usuarios y roles
- Admin: crea/edita cámaras, asigna roles, ve tablero global.
- Cámara (Chamber Admin): gestiona usuarios de su cámara y ve tablero de su cámara.
- Usuario: publica requerimientos, navega, solicita/acepta contactos.

## Nota importante
Este prototipo está pensado como “prueba de concepto”. Para producción se recomienda:
- Backend (FastAPI) + Postgres
- Frontend (Next.js/React) o app móvil (Flutter)
- Autenticación robusta (JWT/OAuth2), auditoría y permisos finos
- Motor de matching con embeddings/LLM + reglas (y trazabilidad)
- Infra/hosting (Docker + CI/CD)
