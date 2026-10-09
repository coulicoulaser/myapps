"""Tri alphabétique des sections, habitudes d'usage (« Les plus utilisées ») et
préférences de l'utilisateur."""
from datetime import timedelta

import pytest
from sqlmodel import select

import usage
from models import AppUsage, Group, User, UserSetting, utcnow


def _everyone(db):
    return db.exec(select(Group).where(Group.is_everyone == True)).one().id  # noqa: E712


def _mk(client, admin_auth, path, body):
    r = client.post(path, headers=admin_auth, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _bob(db):
    return db.exec(select(User).where(User.username == "bob")).one()


@pytest.fixture()
def dash(client, admin_auth, db):
    """Dashboard « Tout le monde » de 4 applications, plus une application d'un autre dashboard."""
    tag = str(len(db.exec(select(AppUsage)).all())) + utcnow().strftime("%H%M%S%f")
    ev = _everyone(db)
    sec = _mk(client, admin_auth, "/api/app-groups", {"name": "Usage " + tag})["id"]
    ids = {}
    for name in ("Zeta", "éditeur", "Alpha", "beta"):
        ids[name] = _mk(client, admin_auth, "/api/apps", {"name": name, "url": f"https://{tag}.{len(ids)}.tld",
                                                         "app_group_ids": [sec], "group_ids": [ev]})["id"]
    slug = "usage-" + tag
    _mk(client, admin_auth, "/api/dashboards", {"name": "Usage", "slug": slug, "app_group_ids": [sec], "group_ids": [ev]})
    other_sec = _mk(client, admin_auth, "/api/app-groups", {"name": "Ailleurs " + tag})["id"]
    ids["ailleurs"] = _mk(client, admin_auth, "/api/apps", {"name": "Ailleurs", "url": f"https://{tag}.x.tld",
                                                           "app_group_ids": [other_sec], "group_ids": [ev]})["id"]
    _mk(client, admin_auth, "/api/dashboards", {"name": "Autre", "slug": slug + "-b", "app_group_ids": [other_sec], "group_ids": [ev]})
    usage.clear(db, _bob(db).id)
    yield slug, ids
    usage.clear(db, _bob(db).id)
    usage.set_prefs(db, _bob(db).id, {"frequent": True, "reduce_motion": False})


def _open(client, auth, app_id, times=1):
    for _ in range(times):
        assert client.post(f"/api/me/usage/{app_id}", headers=auth).status_code == 204


def _dashboard(client, auth, slug):
    return client.get(f"/api/me/dashboard/{slug}", headers=auth).json()


def test_sections_en_ordre_alphabetique(client, user_auth, dash):
    slug, _ = dash
    names = [a["name"] for a in _dashboard(client, user_auth, slug)["groups"][0]["apps"]]
    assert names == ["Alpha", "beta", "éditeur", "Zeta"]          # sans casse ni accents


def test_ordre_manuel_des_tuiles_supprime(client, admin_auth):
    assert client.post("/api/app-groups/1/reorder", headers=admin_auth, json={"ids": [1]}).status_code in (404, 405)


def test_plus_utilisees(client, user_auth, dash, monkeypatch):
    monkeypatch.setattr(usage, "DEBOUNCE_SECONDS", 0)
    slug, ids = dash
    assert _dashboard(client, user_auth, slug)["frequent"] == []           # rien appris
    _open(client, user_auth, ids["Zeta"], 3)
    _open(client, user_auth, ids["beta"], 2)
    _open(client, user_auth, ids["Alpha"], 1)                              # une seule fois : pas une habitude
    _open(client, user_auth, ids["ailleurs"], 5)                           # autre dashboard
    freq = [a["name"] for a in _dashboard(client, user_auth, slug)["frequent"]]
    assert freq == ["Zeta", "beta"]
    # Sections statiques inchangées (alphabétiques, application présente aux deux endroits).
    assert [a["name"] for a in _dashboard(client, user_auth, slug)["groups"][0]["apps"]] == ["Alpha", "beta", "éditeur", "Zeta"]


def test_section_masquee_tant_qu_une_seule_habitude(client, user_auth, dash, monkeypatch):
    monkeypatch.setattr(usage, "DEBOUNCE_SECONDS", 0)
    slug, ids = dash
    _open(client, user_auth, ids["Zeta"], 4)
    assert _dashboard(client, user_auth, slug)["frequent"] == []


def test_habitudes_recentes_passent_devant(db, dash):
    _, ids = dash
    bob, now = _bob(db).id, utcnow()
    for k in range(10):                                   # très utilisée… il y a deux mois
        usage.record(db, bob, ids["Zeta"], now - timedelta(days=60) + timedelta(minutes=k))
    for k in range(3):                                    # un peu, cette semaine
        usage.record(db, bob, ids["beta"], now - timedelta(days=2) + timedelta(minutes=k))
        usage.record(db, bob, ids["Alpha"], now - timedelta(days=1) + timedelta(minutes=k))
    cands = {ids["Zeta"], ids["beta"], ids["Alpha"]}
    # 10 ouvertures il y a 60 jours pèsent moins que 3 de cette semaine…
    assert usage.frequent_ids(db, bob, cands, now) == [ids["Alpha"], ids["beta"], ids["Zeta"]]
    # … et un mois plus tard, sans nouvelle ouverture, l'habitude est éteinte.
    assert usage.frequent_ids(db, bob, cands, now + timedelta(days=30)) == [ids["Alpha"], ids["beta"]]


def test_double_clic_compte_une_fois(db, dash):
    _, ids = dash
    bob, now = _bob(db).id, utcnow()
    usage.record(db, bob, ids["Zeta"], now)
    u = usage.record(db, bob, ids["Zeta"], now + timedelta(seconds=1))
    assert u.count == 1


def test_application_non_visible_refusee(client, admin_auth, user_auth, db, dash):
    g = _mk(client, admin_auth, "/api/groups", {"name": "Réservé usage " + dash[0]})["id"]
    secret = _mk(client, admin_auth, "/api/apps", {"name": "Secret", "url": "https://s.tld", "group_ids": [g]})["id"]
    assert client.post(f"/api/me/usage/{secret}", headers=user_auth).status_code == 404
    assert client.post("/api/me/usage/999999", headers=user_auth).status_code == 404
    assert client.post(f"/api/me/usage/{dash[1]['Zeta']}").status_code == 401


def test_preferences(client, user_auth, dash, monkeypatch):
    monkeypatch.setattr(usage, "DEBOUNCE_SECONDS", 0)
    slug, ids = dash
    assert client.get("/api/auth/me", headers=user_auth).json()["prefs"] == {"frequent": True, "reduce_motion": False}
    _open(client, user_auth, ids["Zeta"], 2)
    _open(client, user_auth, ids["beta"], 2)
    r = client.put("/api/me/prefs", headers=user_auth, json={"frequent": False, "inconnu": True})
    assert r.json() == {"frequent": False, "reduce_motion": False}
    assert _dashboard(client, user_auth, slug)["frequent"] == []
    client.put("/api/me/prefs", headers=user_auth, json={"reduce_motion": True})   # partiel
    assert client.get("/api/me/prefs", headers=user_auth).json() == {"frequent": False, "reduce_motion": True}
    client.put("/api/me/prefs", headers=user_auth, json={"frequent": True})
    assert len(_dashboard(client, user_auth, slug)["frequent"]) == 2


def test_effacer_historique(client, user_auth, dash, monkeypatch):
    monkeypatch.setattr(usage, "DEBOUNCE_SECONDS", 0)
    slug, ids = dash
    _open(client, user_auth, ids["Zeta"], 2)
    _open(client, user_auth, ids["beta"], 2)
    assert client.delete("/api/me/usage", headers=user_auth).json() == {"deleted": 2}
    assert _dashboard(client, user_auth, slug)["frequent"] == []


def test_historique_propre_a_chaque_utilisateur(client, user_auth, admin_auth, dash, monkeypatch):
    monkeypatch.setattr(usage, "DEBOUNCE_SECONDS", 0)
    slug, ids = dash
    _open(client, user_auth, ids["Zeta"], 2)
    _open(client, user_auth, ids["beta"], 2)
    assert _dashboard(client, admin_auth, slug)["frequent"] == []


def test_suppression_app_et_utilisateur_nettoie(client, admin_auth, db, dash, monkeypatch):
    monkeypatch.setattr(usage, "DEBOUNCE_SECONDS", 0)
    _, ids = dash
    bob = _bob(db).id
    usage.record(db, bob, ids["Zeta"])
    assert client.delete(f"/api/apps/{ids['Zeta']}", headers=admin_auth).status_code in (200, 204)
    db.expire_all()
    assert not db.exec(select(AppUsage).where(AppUsage.app_id == ids["Zeta"])).all()

    tmp = _mk(client, admin_auth, "/api/users", {"username": "jetable-" + dash[0], "full_name": "J",
                                                 "password": "motdepasse-123", "role": "USER"})
    uid = tmp["id"]
    usage.record(db, uid, ids["beta"])
    usage.set_prefs(db, uid, {"frequent": False})
    assert client.delete(f"/api/users/{uid}", headers=admin_auth).status_code in (200, 204)
    db.expire_all()
    assert not db.exec(select(AppUsage).where(AppUsage.user_id == uid)).all()
    assert not db.exec(select(UserSetting).where(UserSetting.user_id == uid)).all()
