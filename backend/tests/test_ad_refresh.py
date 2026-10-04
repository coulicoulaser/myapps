"""Synchro AD : groupes et site suivent l'annuaire, sans toucher aux groupes locaux
ni aux comptes locaux ; une panne LDAP ne vide rien."""
import pytest
from sqlmodel import Session, select

import ad_refresh
import security
from database import engine
from models import Group, GroupMembership, Site, User


@pytest.fixture()
def world(monkeypatch):
    with Session(engine) as s:
        for u in s.exec(select(User).where(User.username.in_(["mover", "localsite", "homonyme"]))).all():
            for m in s.exec(select(GroupMembership).where(GroupMembership.user_id == u.id)).all():
                s.delete(m)
            s.delete(u)
        s.commit()
        sites = {}
        for slug, name in (("nord", "Rennes"), ("sud", "Marseille")):
            site = s.exec(select(Site).where(Site.slug == slug)).first() or Site(slug=slug, name=name)
            s.add(site)
            s.commit()
            s.refresh(site)
            sites[slug] = site.id
        groups = {}
        for name, slug, source in (("GRP_NORD", "nord", "ad"), ("GRP_SUD", "sud", "ad"), ("Projet X", None, "local")):
            g = s.exec(select(Group).where(Group.name == name)).first() or Group(name=name, source=source)
            g.site_id = sites[slug] if slug else None
            s.add(g)
            s.commit()
            s.refresh(g)
            groups[name] = g.id
        u = User(username="mover", full_name="Mover", role="USER", auth_source="ldap",
                 is_active=True, site_id=sites["nord"])
        s.add(u)
        s.commit()
        s.refresh(u)
        s.add(GroupMembership(user_id=u.id, group_id=groups["GRP_NORD"]))
        s.add(GroupMembership(user_id=u.id, group_id=groups["Projet X"]))
        s.commit()

    ad = {"mover": {"groups": ["GRP_SUD"], "site": None, "email": "mover@x.fr", "display_name": "Mover"}}
    monkeypatch.setattr(security, "get_ldap_config", lambda db: {"enabled": True, "server": "ldap.test"})
    monkeypatch.setattr(security, "ldap_fetch_user", lambda cfg, username: ad.get(username))
    return {"sites": sites, "groups": groups, "ad": ad}


def _state(username="mover"):
    with Session(engine) as s:
        u = s.exec(select(User).where(User.username == username)).first()
        names = sorted(s.get(Group, m.group_id).name for m in
                       s.exec(select(GroupMembership).where(GroupMembership.user_id == u.id)).all())
        return u.site_id, names


def test_groupe_et_site_suivent_l_ad_groupes_locaux_gardes(world):
    assert ad_refresh.run()["refreshed"] >= 1
    assert _state() == (world["sites"]["sud"], ["GRP_SUD", "Projet X"])


def test_panne_ldap_ne_vide_rien(world, monkeypatch):
    monkeypatch.setattr(security, "ldap_fetch_user", lambda cfg, username: None)
    ad_refresh.run()
    assert _state() == (world["sites"]["nord"], ["GRP_NORD", "Projet X"])


def test_retire_de_tous_les_groupes_ad(world):
    world["ad"]["mover"] = {"groups": [], "site": None}
    ad_refresh.run()
    assert _state() == (world["sites"]["nord"], ["Projet X"])


def test_attribut_site_ad(world):
    world["ad"]["mover"] = {"groups": ["GRP_SANS_SITE"], "site": "Marseille"}
    ad_refresh.run()
    assert _state() == (world["sites"]["sud"], ["GRP_SANS_SITE", "Projet X"])


def test_nom_ad_homonyme_d_un_groupe_local_ignore(world):
    world["ad"]["mover"] = {"groups": ["Projet X", "GRP_SUD"], "site": None}
    ad_refresh.run()
    with Session(engine) as s:
        assert s.exec(select(Group).where(Group.name == "Projet X")).one().source == "local"
    assert _state()[1] == ["GRP_SUD", "Projet X"]


def test_comptes_locaux_intouches(world):
    with Session(engine) as s:
        s.add(User(username="localsite", full_name="L", role="USER", auth_source="local",
                   is_active=True, site_id=world["sites"]["nord"]))
        s.commit()
    world["ad"]["localsite"] = {"groups": ["GRP_SUD"], "site": None}
    ad_refresh.run()
    assert _state("localsite") == (world["sites"]["nord"], [])


def test_compte_local_jamais_ouvert_par_ldap(world, monkeypatch):
    """Un compte local homonyme d'un compte AD ne s'ouvre pas avec le mot de passe AD."""
    from fastapi.testclient import TestClient
    from app import app
    with Session(engine) as s:
        s.add(User(username="homonyme", full_name="H", role="USER", auth_source="local",
                   password_hash=security.hash_password("local-pw-1"), is_active=True))
        s.commit()
    monkeypatch.setattr(security, "ldap_authenticate", lambda cfg, u, p: True)
    r = TestClient(app).post("/api/auth/login", data={"username": "homonyme", "password": "ad-pw"})
    assert r.status_code == 401


def test_injection_filtre_ldap_echappee():
    cfg = {"search_filter": "(sAMAccountName={username})"}
    assert security._user_filter(cfg, "*)(objectClass=*") == r"(sAMAccountName=\2a\29\28objectClass=\2a)"


class _Vals:
    def __init__(self, values):
        self.values = values


class _Entry(dict):
    def __getattr__(self, k):
        return self[k]


def test_attr_gere_liste_vide_ldap3():
    e = _Entry(mail=_Vals([]), displayName=_Vals(["Jean"]))
    assert security._attr(e, "mail") is None
    assert security._attr(e, "displayName") == "Jean"
    assert security._attr(e, "absent") is None
