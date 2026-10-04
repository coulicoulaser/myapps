"""MyApps — portail d'applications : API FastAPI + frontend statique."""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

import changelog
from auth_routes import admin_router, auth_router
from config import FRONTEND_DIR, UPLOAD_DIR, ensure_dirs
from database import engine, init_db
from models import User
from portal_routes import admin_crud, portal_router
from security import ensure_everyone_group, require_admin
from setup_routes import ensure_setup_code, setup_router
from update_routes import sync_policy_file, update_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    init_db()
    with Session(engine) as s:
        ensure_everyone_group(s)
        ensure_setup_code(s)
        sync_policy_file(s)
    yield


app = FastAPI(title="MyApps", version=changelog.current_version(), lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def headers(request, call_next):
    resp = await call_next(request)
    path = request.url.path
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if path.startswith("/uploads/"):
        # Un SVG envoyé ouvert directement ne doit pas pouvoir exécuter de script
        # sur l'origine du portail (le jeton de session y est stocké).
        resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    elif path == "/" or path.endswith((".js", ".css", ".html")):
        # Front sans étape de build : évite de servir un JS/CSS périmé après mise à jour.
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# --- routers -----------------------------------------------------------------
app.include_router(setup_router)    # /api/setup/*
app.include_router(auth_router)     # /api/auth/*
app.include_router(admin_router)    # /api/settings, /api/groups, /api/users
app.include_router(portal_router)   # /api/me/*, /api/sites
app.include_router(admin_crud)      # /api/apps, /api/app-groups, /api/dashboards, /api/sites…
app.include_router(update_router)   # /api/update/*


@app.get("/api/changelog")
def get_changelog(_: User = Depends(require_admin)):
    """Changelog complet (Markdown) — réservé aux ADMIN (page Administration)."""
    return {"version": changelog.current_version(), "markdown": changelog.read_markdown()}


@app.get("/api/health")
def health():
    return {"ok": True, "version": changelog.current_version()}


# --- fichiers statiques ------------------------------------------------------
ensure_dirs()
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
