"""Assistant de premier démarrage.

Tant qu'aucun administrateur n'existe, le portail affiche l'assistant. Pour qu'un
inconnu arrivé le premier sur l'URL ne puisse pas s'approprier le portail, la
création du compte administrateur exige un **code d'installation** : il est généré
au démarrage, écrit dans <données>/setup-code (lisible par root et le compte du
service) et dans le journal, et affiché par install.sh à la fin de l'installation.

Une fois l'administrateur créé, le code est détruit et la suite de l'assistant
(apparence, premier dashboard, première application) passe par les routes admin
habituelles, avec le jeton de cet administrateur. `setup_completed` marque la fin ;
l'assistant peut être relancé depuis Administration › Réglages.
"""
import hmac
import logging
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from config import SETUP_CODE_FILE
from database import get_session
from models import User
from security import (MIN_PASSWORD_LENGTH, create_token, get_setting, hash_password,
                      require_admin, set_setting)

log = logging.getLogger("myapps.setup")
setup_router = APIRouter(prefix="/api/setup", tags=["setup"])

# Anti-force brute sur le code : 10 essais ratés → 15 min de blocage.
_MAX_FAILS, _LOCK_SECONDS = 10, 15 * 60
_fails = {"count": 0, "until": 0.0}


def admin_exists(db: Session) -> bool:
    return db.exec(select(User).where(User.role == "ADMIN")).first() is not None


def setup_completed(db: Session) -> bool:
    return get_setting(db, "setup_completed", "0") == "1"


def _new_code() -> str:
    raw = secrets.token_hex(5).upper()          # 40 bits, ex. 3F9A-C21B-7E
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def ensure_setup_code(db: Session) -> None:
    """Au démarrage : génère (ou garde) le code tant qu'il n'y a pas d'admin, l'efface sinon."""
    if admin_exists(db):
        SETUP_CODE_FILE.unlink(missing_ok=True)
        return
    code = read_code()
    if not code:
        code = _new_code()
        SETUP_CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(SETUP_CODE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            f.write(code + "\n")
    log.warning("Assistant de premier démarrage : code d'installation = %s "
                "(aussi dans %s)", code, SETUP_CODE_FILE)


def read_code() -> str:
    try:
        return SETUP_CODE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _normalize(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _check_code(code: str) -> None:
    now = time.time()
    if _fails["until"] > now:
        raise HTTPException(429, "Trop d'essais. Réessayez dans quelques minutes.")
    expected = read_code()
    if expected and hmac.compare_digest(_normalize(code), _normalize(expected)):
        _fails["count"] = 0
        return
    _fails["count"] += 1
    if _fails["count"] >= _MAX_FAILS:
        _fails["count"], _fails["until"] = 0, now + _LOCK_SECONDS
    raise HTTPException(403, "Code d'installation incorrect")


# ---------------------------------------------------------------------------
@setup_router.get("/status")
def status(db: Session = Depends(get_session)):
    """Public : le front décide d'afficher l'assistant, le login ou le portail."""
    return {"required": not setup_completed(db), "admin_exists": admin_exists(db)}


class CodeIn(BaseModel):
    code: str


@setup_router.post("/verify")
def verify(p: CodeIn, db: Session = Depends(get_session)):
    if admin_exists(db):
        raise HTTPException(409, "Un administrateur existe déjà")
    _check_code(p.code)
    return {"ok": True}


class AdminIn(BaseModel):
    code: str
    username: str
    full_name: str = ""
    email: str = ""
    password: str


@setup_router.post("/admin")
def create_admin(p: AdminIn, db: Session = Depends(get_session)):
    if admin_exists(db):
        raise HTTPException(409, "Un administrateur existe déjà")
    _check_code(p.code)
    username = p.username.strip()
    if not username or any(c.isspace() for c in username):
        raise HTTPException(400, "Identifiant invalide (sans espace)")
    if len(p.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Mot de passe trop court ({MIN_PASSWORD_LENGTH} caractères minimum)")
    if db.exec(select(User).where(User.username == username)).first():
        raise HTTPException(400, "Identifiant déjà pris")
    u = User(username=username, full_name=p.full_name.strip() or username,
             email=p.email.strip() or None, role="ADMIN", auth_source="local",
             password_hash=hash_password(p.password), is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    SETUP_CODE_FILE.unlink(missing_ok=True)
    log.warning("Assistant : administrateur « %s » créé, code d'installation détruit.", username)
    from auth_routes import user_payload
    return {"access_token": create_token(u.username), "token_type": "bearer",
            "user": user_payload(db, u)}


@setup_router.post("/complete")
def complete(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    set_setting(db, "setup_completed", "1")
    db.commit()
    return {"ok": True}


@setup_router.post("/restart")
def restart(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    """Relance l'assistant (apparence, dashboard, application) pour l'admin connecté."""
    set_setting(db, "setup_completed", "0")
    db.commit()
    return {"ok": True}
