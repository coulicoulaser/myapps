"""Synchro nocturne AD → portail : relit groupes et site de chaque compte AD/SSO actif.

Sans elle, un changement de groupe AD n'est vu qu'à la prochaine connexion, ce qui
peut prendre jusqu'à 30 jours avec « Mémoriser la connexion ». Sans effet si l'AD est
désactivé. Lancé par le timer systemd <service>-adsync.timer (python ad_refresh.py).
"""
from sqlmodel import Session, select

from database import engine
from models import User
from security import DIRECTORY_SOURCES, refresh_user_from_ad


def run() -> dict:
    done = skipped = 0
    with Session(engine) as db:
        users = db.exec(select(User).where(User.is_active == True,  # noqa: E712
                                           User.auth_source.in_(DIRECTORY_SOURCES))).all()
        for u in users:
            if refresh_user_from_ad(db, u):
                done += 1
            else:
                skipped += 1
    return {"refreshed": done, "skipped": skipped}


if __name__ == "__main__":
    print(run())
