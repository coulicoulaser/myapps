"""Harnais de test — MyApps (backend `app:app`).

Base SQLite et dossier de données temporaires, imposés AVANT tout import applicatif :
aucun test ne touche une vraie installation. Le TestClient est créé sans context
manager : le lifespan (init_db, code d'installation) ne tourne pas, le schéma est
créé ici. Comptes : un ADMIN et un USER locaux.
"""
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="myapps_test_")
os.environ["MYAPPS_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)

from sqlmodel import Session, SQLModel, select  # noqa: E402

import models  # noqa: E402,F401
from database import engine  # noqa: E402
from models import User  # noqa: E402
from security import create_token, ensure_everyone_group, hash_password  # noqa: E402
import app as app_module  # noqa: E402

app = app_module.app
SQLModel.metadata.create_all(engine)

ADMIN_USERNAME, ADMIN_PASSWORD = "admin", "admin-pw-123"
NON_ADMIN_USERNAME, NON_ADMIN_PASSWORD = "bob", "bob-pw-1234"

with Session(engine) as _s:
    ensure_everyone_group(_s)
    for uname, pw, role in ((ADMIN_USERNAME, ADMIN_PASSWORD, "ADMIN"), (NON_ADMIN_USERNAME, NON_ADMIN_PASSWORD, "USER")):
        if not _s.exec(select(User).where(User.username == uname)).first():
            _s.add(User(username=uname, full_name=uname.title(), role=role,
                        password_hash=hash_password(pw), auth_source="local", is_active=True))
    _s.commit()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="session")
def user_auth():
    return {"Authorization": f"Bearer {create_token(NON_ADMIN_USERNAME)}"}


@pytest.fixture(scope="session")
def admin_auth():
    return {"Authorization": f"Bearer {create_token(ADMIN_USERNAME)}"}


@pytest.fixture()
def db():
    with Session(engine) as s:
        yield s
