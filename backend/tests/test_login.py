"""Connexion : durée du jeton selon « Mémoriser », refus des mauvais identifiants."""
from datetime import datetime, timedelta, timezone

import jwt

from conftest import NON_ADMIN_PASSWORD, NON_ADMIN_USERNAME
from security import JWT_ALGO, JWT_EXPIRE_HOURS, JWT_REMEMBER_DAYS, JWT_SECRET


def _login(client, remember=None, password=NON_ADMIN_PASSWORD):
    data = {"username": NON_ADMIN_USERNAME, "password": password}
    if remember is not None:
        data["remember"] = remember
    return client.post("/api/auth/login", data=data)


def _exp(token):
    return datetime.fromtimestamp(jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])["exp"], timezone.utc)


def test_login_court_par_defaut(client):
    r = _login(client, "false")
    assert r.status_code == 200
    exp = _exp(r.json()["access_token"])
    assert abs((exp - (datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS))).total_seconds()) < 60


def test_login_memoriser(client):
    exp = _exp(_login(client, "true").json()["access_token"])
    assert abs((exp - (datetime.now(timezone.utc) + timedelta(days=JWT_REMEMBER_DAYS))).total_seconds()) < 60


def test_login_sans_champ_remember(client):
    exp = _exp(_login(client).json()["access_token"])
    assert exp < datetime.now(timezone.utc) + timedelta(days=1)


def test_mauvais_mot_de_passe(client):
    assert _login(client, password="faux").status_code == 401


def test_compte_desactive(client, db):
    from models import User
    from security import hash_password
    db.add(User(username="parti", full_name="P", role="USER", password_hash=hash_password("pw-parti-1"),
                auth_source="local", is_active=False))
    db.commit()
    r = client.post("/api/auth/login", data={"username": "parti", "password": "pw-parti-1"})
    assert r.status_code == 401
