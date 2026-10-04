"""Droits : « Tout le monde », groupes locaux, exceptions, validations d'entrée."""
from sqlmodel import select

from models import Group, GroupMembership, User


def _everyone(db):
    return db.exec(select(Group).where(Group.is_everyone == True)).one()  # noqa: E712


def _mk(client, admin_auth, path, body):
    r = client.post(path, headers=admin_auth, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_tout_le_monde_voit_le_dashboard_public(client, admin_auth, user_auth, db):
    ev = _everyone(db).id
    sec = _mk(client, admin_auth, "/api/app-groups", {"name": "Public"})["id"]
    app_id = _mk(client, admin_auth, "/api/apps", {"name": "Wiki", "url": "https://wiki.tld",
                                                  "app_group_ids": [sec], "group_ids": [ev]})["id"]
    _mk(client, admin_auth, "/api/dashboards", {"name": "Public", "slug": "public", "is_default": True,
                                                "app_group_ids": [sec], "group_ids": [ev]})
    slugs = [d["slug"] for d in client.get("/api/me/dashboards", headers=user_auth).json()]
    assert "public" in slugs
    d = client.get("/api/me/dashboard/public", headers=user_auth).json()
    assert [a["id"] for a in d["groups"][0]["apps"]] == [app_id]
    assert client.get("/api/me/default-dashboard", headers=user_auth).json()["slug"] == "public"


def test_groupe_local_et_exception_deny(client, admin_auth, user_auth, db):
    g = _mk(client, admin_auth, "/api/groups", {"name": "Compta"})["id"]
    sec = _mk(client, admin_auth, "/api/app-groups", {"name": "Finance"})["id"]
    app_id = _mk(client, admin_auth, "/api/apps", {"name": "Paie", "url": "https://paie.tld",
                                                  "app_group_ids": [sec], "group_ids": [g]})["id"]
    _mk(client, admin_auth, "/api/dashboards", {"name": "Compta", "slug": "compta",
                                                "app_group_ids": [sec], "group_ids": [g]})
    bob = db.exec(select(User).where(User.username == "bob")).one()
    assert client.get("/api/me/dashboard/compta", headers=user_auth).status_code == 404

    r = client.patch(f"/api/users/{bob.id}", headers=admin_auth, json={"local_group_ids": [g]})
    assert r.status_code == 200
    d = client.get("/api/me/dashboard/compta", headers=user_auth).json()
    assert [a["id"] for a in d["groups"][0]["apps"]] == [app_id]

    client.patch(f"/api/users/{bob.id}", headers=admin_auth, json={"app_overrides": [{"id": app_id, "effect": "DENY"}]})
    d = client.get("/api/me/dashboard/compta", headers=user_auth).json()
    assert d["groups"] == []
    client.patch(f"/api/users/{bob.id}", headers=admin_auth, json={"app_overrides": [], "local_group_ids": []})


def test_tout_le_monde_indestructible(client, admin_auth, db):
    assert client.delete(f"/api/groups/{_everyone(db).id}", headers=admin_auth).status_code == 400


def test_lien_javascript_refuse(client, admin_auth):
    for url in ("javascript:alert(1)", " JaVaScRiPt:alert(1)", "data:text/html,x", "java\tscript:alert(1)"):
        r = client.post("/api/apps", headers=admin_auth, json={"name": "X", "url": url})
        assert r.status_code == 400, url
    ok = client.post("/api/apps", headers=admin_auth, json={"name": "Bureau", "url": "rdp://srv01"})
    assert ok.status_code == 200


def test_url_image_et_couleur_validees(client, admin_auth):
    bad = [{"logo_url": "javascript:x"}, {"dashboard_background": 'x") ; background:url("evil'},
           {"accent_color": "red"}, {"search_engine": "altavista"}]
    for b in bad:
        assert client.put("/api/settings", headers=admin_auth, json={"branding": b}).status_code == 400, b
    r = client.put("/api/settings", headers=admin_auth, json={"branding": {
        "portal_name": "Intranet", "accent_color": "#10B981", "logo_url": "/uploads/x.png",
        "dashboard_background": "/backgrounds/ocean.svg", "search_engine": "duckduckgo"}})
    assert r.status_code == 200
    b = client.get("/api/auth/branding").json()
    assert b["portal_name"] == "Intranet" and b["accent_color"] == "#10b981"
    assert b["search_engine_url"].startswith("https://duckduckgo.com/")


def test_dernier_admin_protege(client, admin_auth, db):
    admin = db.exec(select(User).where(User.username == "admin")).one()
    assert client.patch(f"/api/users/{admin.id}", headers=admin_auth, json={"role": "USER"}).status_code == 400
    assert client.patch(f"/api/users/{admin.id}", headers=admin_auth, json={"is_active": False}).status_code == 400
    assert client.delete(f"/api/users/{admin.id}", headers=admin_auth).status_code == 400


def test_upload_svg_sandboxe(client, admin_auth):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = client.post("/api/upload", headers=admin_auth, files={"file": ("x.svg", svg, "image/svg+xml")})
    assert r.status_code == 200
    got = client.get(r.json()["url"])
    assert got.status_code == 200
    assert "sandbox" in got.headers["content-security-policy"]
    assert client.post("/api/upload", headers=admin_auth, files={"file": ("x.html", b"<b>", "text/html")}).status_code == 400


def test_sites_crud(client, admin_auth, user_auth):
    s = _mk(client, admin_auth, "/api/sites", {"name": "Saint-Étienne", "latitude": 45.43, "longitude": 4.39})
    assert s["slug"] == "saint-etienne"
    assert client.post("/api/sites", headers=admin_auth, json={"name": "saint-étienne", "latitude": 1, "longitude": 1}).status_code == 400
    assert client.post("/api/sites", headers=admin_auth, json={"name": "Nulle part", "latitude": 99, "longitude": 1}).status_code == 400
    assert any(x["slug"] == "saint-etienne" for x in client.get("/api/sites", headers=user_auth).json())
    assert client.delete(f"/api/sites/{s['id']}", headers=admin_auth).status_code == 204


def test_slug_dashboard_unique(client, admin_auth):
    _mk(client, admin_auth, "/api/dashboards", {"name": "Équipe RH", "slug": ""})
    r = client.post("/api/dashboards", headers=admin_auth, json={"name": "Equipe RH", "slug": ""})
    assert r.status_code == 400


def test_fond_et_polices(client, admin_auth):
    for b in ({"base_color": "navy"}, {"font_title": "Comic Sans MS"}, {"font_body": "../../x"}):
        assert client.put("/api/settings", headers=admin_auth, json={"branding": b}).status_code == 400, b
    r = client.put("/api/settings", headers=admin_auth, json={"branding": {
        "base_color": "#000038", "font_title": "Space Grotesk", "font_body": "Albert Sans"}})
    assert r.status_code == 200
    b = client.get("/api/auth/branding").json()
    assert (b["base_color"], b["font_title"], b["font_body"]) == ("#000038", "Space Grotesk", "Albert Sans")
    client.put("/api/settings", headers=admin_auth, json={"branding": {"font_title": "system", "font_body": "system"}})


def test_animations_desactivables(client, admin_auth):
    assert client.get("/api/auth/branding").json()["animations"] is True
    client.put("/api/settings", headers=admin_auth, json={"branding": {"animations": False}})
    assert client.get("/api/auth/branding").json()["animations"] is False
    client.put("/api/settings", headers=admin_auth, json={"branding": {"animations": True}})
