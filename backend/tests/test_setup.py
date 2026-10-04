"""Assistant de premier démarrage : le compte administrateur ne se crée qu'avec le
code d'installation, une seule fois, et le code est détruit ensuite."""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import setup_routes
from database import get_session
from models import User


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    """Installation vierge : base vide (aucun admin) et fichier de code temporaire."""
    from conftest import app
    eng = create_engine(f"sqlite:///{tmp_path}/fresh.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)

    def _session():
        with Session(eng) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    monkeypatch.setattr(setup_routes, "SETUP_CODE_FILE", tmp_path / "setup-code")
    monkeypatch.setitem(setup_routes._fails, "count", 0)
    monkeypatch.setitem(setup_routes._fails, "until", 0.0)
    with Session(eng) as s:
        setup_routes.ensure_setup_code(s)
    yield eng, (tmp_path / "setup-code").read_text().strip()
    app.dependency_overrides.pop(get_session, None)


def _admin(client, code, **kw):
    body = {"code": code, "username": "chef", "password": "motdepasse-1", **kw}
    return client.post("/api/setup/admin", json=body)


def test_status_vierge(client, fresh):
    assert client.get("/api/setup/status").json() == {"required": True, "admin_exists": False}


def test_code_fichier_prive(fresh, tmp_path):
    assert (tmp_path / "setup-code").stat().st_mode & 0o007 == 0


def test_mauvais_code_refuse(client, fresh):
    assert client.post("/api/setup/verify", json={"code": "0000-0000-00"}).status_code == 403
    assert _admin(client, "0000-0000-00").status_code == 403


def test_code_tolere_casse_et_tirets(client, fresh):
    _, code = fresh
    assert client.post("/api/setup/verify", json={"code": code.lower().replace("-", " ")}).status_code == 200


def test_blocage_apres_trop_d_essais(client, fresh):
    _, code = fresh
    for _ in range(setup_routes._MAX_FAILS):
        client.post("/api/setup/verify", json={"code": "faux"})
    assert client.post("/api/setup/verify", json={"code": code}).status_code == 429


def test_creation_admin_puis_fermeture(client, fresh, tmp_path):
    eng, code = fresh
    r = _admin(client, code, full_name="La Cheffe")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["is_admin"] is True
    assert not (tmp_path / "setup-code").exists()
    with Session(eng) as s:
        u = s.exec(select(User).where(User.username == "chef")).one()
        assert u.role == "ADMIN" and u.password_hash and u.password_hash != "motdepasse-1"
    # Plus jamais : ni avec le même code, ni avec un autre
    assert _admin(client, code, username="intrus").status_code == 409
    assert client.post("/api/setup/verify", json={"code": code}).status_code == 409
    st = client.get("/api/setup/status").json()
    assert st == {"required": True, "admin_exists": True}

    auth = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.post("/api/setup/complete", headers=auth).status_code == 200
    assert client.get("/api/setup/status").json()["required"] is False
    assert client.post("/api/setup/restart", headers=auth).status_code == 200
    assert client.get("/api/setup/status").json()["required"] is True


def test_mot_de_passe_trop_court(client, fresh):
    _, code = fresh
    assert _admin(client, code, password="court").status_code == 400


def test_identifiant_avec_espace(client, fresh):
    _, code = fresh
    assert _admin(client, code, username="le chef").status_code == 400


def test_pas_de_code_si_admin_existe(tmp_path, monkeypatch, db):
    monkeypatch.setattr(setup_routes, "SETUP_CODE_FILE", tmp_path / "setup-code")
    (tmp_path / "setup-code").write_text("RESTE")
    setup_routes.ensure_setup_code(db)          # la base de test contient déjà un admin
    assert not (tmp_path / "setup-code").exists()
