"""Administration des mises à jour (voir updater.py pour la chaîne de confiance).

L'application ne fait que vérifier et demander : l'installation est faite par le
service root `<service>-update`, déclenché par le fichier de demande."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

import config
import updater
from database import get_session
from models import User
from security import get_setting, require_admin, set_setting

update_router = APIRouter(prefix="/api/update", tags=["update"])


def sync_policy_file(db: Session) -> dict:
    """Le timer root lit la politique dans un fichier, pas dans la base."""
    mode = get_setting(db, "update_mode", updater.DEFAULT_POLICY["mode"])
    channel = get_setting(db, "update_channel", updater.DEFAULT_POLICY["channel"])
    if mode not in updater.MODES:
        mode = updater.DEFAULT_POLICY["mode"]
    if channel not in updater.CHANNELS:
        channel = updater.DEFAULT_POLICY["channel"]
    try:
        return updater.write_policy(mode, channel)
    except OSError:
        return {"mode": mode, "channel": channel}


def _payload(db: Session) -> dict:
    st = updater.read_status()
    current = updater.current_version()
    # Un état enregistré avant une mise à jour peut annoncer une version déjà installée.
    latest = (st.get("latest") or {}).get("version")
    if latest and not st.get("error"):
        try:
            st.update(updater.assess({"version": latest, "min_from": st["latest"].get("min_from")}, current))
        except updater.UpdateError:
            st["available"] = False
    return {
        "current": current,
        "updater_installed": config.UPDATER_ENABLED,
        "repo": config.UPDATE_REPO,
        "policy": sync_policy_file(db),
        "status": st,
        "pending_request": updater.request_file().exists(),
        "log": _log_tail(),
    }


def _log_tail(n: int = 60) -> list[str]:
    try:
        return updater.log_file().read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []


@update_router.get("/status")
def status(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    return _payload(db)


@update_router.post("/check")
def check(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    """Vérification immédiate (fonction synchrone : exécutée hors de la boucle d'événements)."""
    updater.check(sync_policy_file(db)["channel"])
    return _payload(db)


class SettingsIn(BaseModel):
    mode: str
    channel: str = "stable"


@update_router.put("/settings")
def put_settings(p: SettingsIn, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    if p.mode not in updater.MODES or p.channel not in updater.CHANNELS:
        raise HTTPException(400, "Mode ou canal inconnu")
    set_setting(db, "update_mode", p.mode)
    set_setting(db, "update_channel", p.channel)
    db.commit()
    return _payload(db)


class InstallIn(BaseModel):
    allow_major: bool = False


@update_router.post("/install")
def install(p: InstallIn, db: Session = Depends(get_session), me: User = Depends(require_admin)):
    if not config.UPDATER_ENABLED:
        raise HTTPException(400, "Installation automatique indisponible : MyApps n'a pas été installé avec install.sh")
    data = _payload(db)
    st = data["status"]
    if not st.get("available"):
        raise HTTPException(400, st.get("blocked") or "Aucune mise à jour disponible : vérifiez d'abord")
    if st.get("major") and not p.allow_major:
        raise HTTPException(400, "Version majeure : confirmation requise")
    if data["pending_request"] or st.get("installing"):
        raise HTTPException(409, "Une installation est déjà en cours")
    version = st["latest"]["version"]
    req = {"version": version, "allow_major": p.allow_major, "by": me.username, "at": updater.now_iso()}
    updater.request_file().write_text(json.dumps(req), encoding="utf-8")
    updater.log(f"Installation de {version} demandée par {me.username}")
    return {"queued": True, "version": version}
