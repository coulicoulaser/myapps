"""Habitudes d'usage et préférences de l'utilisateur.

Chaque ouverture d'une application ajoute 1 à un score qui perd la moitié de sa valeur
tous les HALF_LIFE_DAYS jours : une application ouverte cette semaine passe devant une
application très utilisée il y a deux mois. Le score est stocké à la date de la dernière
ouverture et ramené à « maintenant » à la lecture (pas de tâche de fond).

La section « Les plus utilisées » d'un dashboard reprend, parmi les applications de CE
dashboard, celles qui ont été ouvertes plusieurs fois et récemment. Elle reste masquée
tant que l'historique est trop mince pour dire quelque chose."""
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from models import AppUsage, UserSetting, utcnow

HALF_LIFE_DAYS = 14.0
FREQUENT_MAX = 8
FREQUENT_MIN_APPS = 2      # en dessous, la section n'apprend rien à l'utilisateur
MIN_COUNT = 2              # une seule ouverture ne fait pas une habitude
MIN_SCORE = 0.3            # ≈ une ouverture il y a un mois : habitude éteinte
# Double clic, clic + clic molette : une seule ouverture. Réglable pour les tests e2e.
DEBOUNCE_SECONDS = float(os.environ.get("MYAPPS_USAGE_DEBOUNCE", "5"))

# Préférences : clé -> valeur par défaut. Valeurs stockées sous forme de texte.
PREFS: dict[str, bool] = {
    "frequent": True,           # section « Les plus utilisées » (tri dynamique)
    "reduce_motion": False,     # animations réduites pour cet utilisateur
}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def decayed(u: AppUsage, now: Optional[datetime] = None) -> float:
    age_days = max(0.0, ((now or utcnow()) - _aware(u.last_used)).total_seconds() / 86400)
    return u.score * 0.5 ** (age_days / HALF_LIFE_DAYS)


def record(db: Session, user_id: int, app_id: int, now: Optional[datetime] = None) -> AppUsage:
    now = now or utcnow()
    u = db.exec(select(AppUsage).where(AppUsage.user_id == user_id, AppUsage.app_id == app_id)).first()
    if u is None:
        u = AppUsage(user_id=user_id, app_id=app_id, score=1.0, count=1, last_used=now)
        db.add(u)
        try:
            db.commit()
        except IntegrityError:              # même ouverture enregistrée en parallèle
            db.rollback()
            return db.exec(select(AppUsage).where(AppUsage.user_id == user_id, AppUsage.app_id == app_id)).one()
        return u
    if (now - _aware(u.last_used)).total_seconds() < DEBOUNCE_SECONDS:
        return u
    u.score = decayed(u, now) + 1.0
    u.count += 1
    u.last_used = now
    db.add(u)
    db.commit()
    return u


def frequent_ids(db: Session, user_id: int, candidate_ids: set[int],
                 now: Optional[datetime] = None) -> list[int]:
    """Applications les plus utilisées parmi `candidate_ids`, de la plus à la moins utilisée."""
    if not candidate_ids:
        return []
    rows = db.exec(select(AppUsage).where(AppUsage.user_id == user_id,
                                          AppUsage.app_id.in_(candidate_ids))).all()
    scored = [(decayed(u, now), u.app_id) for u in rows if u.count >= MIN_COUNT]
    scored = sorted([x for x in scored if x[0] >= MIN_SCORE], key=lambda x: -x[0])[:FREQUENT_MAX]
    return [aid for _, aid in scored] if len(scored) >= FREQUENT_MIN_APPS else []


def clear(db: Session, user_id: int) -> int:
    rows = db.exec(select(AppUsage).where(AppUsage.user_id == user_id)).all()
    for r in rows:
        db.delete(r)
    db.commit()
    return len(rows)


# --- Préférences ---------------------------------------------------------------
def get_prefs(db: Session, user_id: int) -> dict:
    out = dict(PREFS)
    for s in db.exec(select(UserSetting).where(UserSetting.user_id == user_id)).all():
        if s.key in PREFS:
            out[s.key] = s.value == "1"
    return out


def set_prefs(db: Session, user_id: int, values: dict) -> dict:
    for key, val in values.items():
        if key not in PREFS or val is None:
            continue
        s = db.exec(select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)).first()
        if s is None:
            s = UserSetting(user_id=user_id, key=key)
        s.value = "1" if val else "0"
        db.add(s)
    db.commit()
    return get_prefs(db, user_id)


def forget_user(db: Session, user_id: int) -> None:
    """Suppression d'un compte : son historique et ses préférences partent avec lui (sans commit)."""
    for r in db.exec(select(AppUsage).where(AppUsage.user_id == user_id)).all():
        db.delete(r)
    for r in db.exec(select(UserSetting).where(UserSetting.user_id == user_id)).all():
        db.delete(r)


def forget_app(db: Session, app_id: int) -> None:
    """Suppression d'une application : historique de tous les utilisateurs (sans commit)."""
    for r in db.exec(select(AppUsage).where(AppUsage.app_id == app_id)).all():
        db.delete(r)
