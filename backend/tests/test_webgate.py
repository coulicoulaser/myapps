"""Portail jamais servi sans connexion (sauf assistant de premier démarrage, sans admin)."""
from fastapi.testclient import TestClient

import app as app_module
from tests.conftest import NON_ADMIN_PASSWORD, NON_ADMIN_USERNAME


def _fresh():
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_anonymous_gets_login_only():
    c = _fresh()
    r = c.get("/")
    assert r.status_code == 200 and 'id="loginForm"' in r.text and "login.js" in r.text
    assert "app.js" not in r.text and "setup.js" not in r.text and 'id="view-app"' not in r.text
    for path in ("/app.js", "/setup.js", "/index.html", "/../backend/app.py"):
        r = c.get(path)
        assert r.status_code == 404 or 'id="loginForm"' in r.text, path
    for path in ("/login.js", "/common.js", "/style.css", "/favicon.svg", "/backgrounds/aurore.svg"):
        assert c.get(path).status_code == 200, path


def test_portal_after_login_and_logout():
    c = _fresh()
    r = c.post("/api/auth/login", data={"username": NON_ADMIN_USERNAME, "password": NON_ADMIN_PASSWORD})
    assert r.status_code == 200
    assert 'id="view-app"' in c.get("/").text and c.get("/app.js").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/app.js").status_code == 404 and 'id="loginForm"' in c.get("/").text


def test_cookie_refreshed_by_me(user_auth):
    c = _fresh()
    assert c.get("/app.js").status_code == 404
    assert c.get("/api/auth/me", headers=user_auth).status_code == 200
    assert c.get("/app.js").status_code == 200


def test_setup_open_without_admin(monkeypatch):
    monkeypatch.setattr(app_module, "admin_exists", lambda s: False)
    c = _fresh()
    assert 'id="view-setup"' in c.get("/").text and c.get("/setup.js").status_code == 200
