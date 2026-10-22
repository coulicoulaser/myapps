"""Groupes AD imbriqués : un groupe qui contient d'autres groupes compte leurs membres,
et un utilisateur hérite des groupes parents. Un annuaire sans la règle IN_CHAIN
(OpenLDAP) garde le comportement direct."""
import re

import pytest

import security

BASE = "DC=test,DC=local"


def dn(cn):
    return f"CN={cn},{BASE}"


# PARENT contient ENFANT, qui contient alice ; bob est membre direct de PARENT.
MEMBERS = {dn("PARENT"): {dn("ENFANT"), dn("bob")}, dn("ENFANT"): {dn("alice")}}
USERS = {"alice", "bob"}


def _direct_parents(member_dn):
    return {g for g, ms in MEMBERS.items() if member_dn in ms}


def _all_parents(member_dn):
    out, todo = set(), [member_dn]
    while todo:
        for g in _direct_parents(todo.pop()):
            if g not in out:
                out.add(g)
                todo.append(g)
    return out


class _Values:
    def __init__(self, values):
        self.values = values


class _Entry:
    def __init__(self, **attrs):
        self._attrs = {k: _Values(v if isinstance(v, list) else [v]) for k, v in attrs.items()}

    def __contains__(self, name):
        return name in self._attrs

    def __getitem__(self, name):
        return self._attrs[name]


class FakeConn:
    """Annuaire minimal : comprend les filtres émis par security.py."""

    def __init__(self, in_chain=True):
        self.in_chain = in_chain
        self.entries = []

    def _rule(self, flt, attr):
        m = re.search(rf"\({attr}(:{re.escape(security.IN_CHAIN)}:)?=([^)]*)\)", flt)
        if not m:
            return None
        return bool(m.group(1)), m.group(2).replace("\\2c", ",")

    def search(self, base, flt, scope, attributes=None):
        self.entries = []
        user = re.search(r"\(sAMAccountName=([^)]*)\)", flt)
        if user:
            name = user.group(1)
            if name in USERS:
                self.entries = [_Entry(distinguishedName=dn(name), displayName=name.title(), mail=[])]
            return True
        rule = self._rule(flt, "member")
        if rule:
            chained, target = rule
            if chained and not self.in_chain:
                return True
            groups = _all_parents(target) if chained else _direct_parents(target)
            self.entries = [_Entry(cn=g.split(",")[0][3:]) for g in sorted(groups)]
            return True
        rule = self._rule(flt, "memberOf")
        if rule:
            chained, target = rule
            if chained and not self.in_chain:
                return True
            for u in sorted(USERS):
                parents = _all_parents(dn(u)) if chained else _direct_parents(dn(u))
                if target in parents:
                    self.entries.append(_Entry(sAMAccountName=u, displayName=u.title(), mail=[]))
        return True

    def unbind(self):
        pass


CFG = {"base_dn": BASE, "search_filter": "(sAMAccountName={username})",
       "group_filter": "(objectClass=group)", "site_attr": "physicalDeliveryOfficeName"}


@pytest.fixture()
def annuaire(monkeypatch):
    def use(in_chain=True):
        monkeypatch.setattr(security, "_service_conn", lambda cfg: FakeConn(in_chain))
    return use


def test_utilisateur_herite_des_groupes_parents(annuaire):
    annuaire()
    assert sorted(security.ldap_fetch_user(CFG, "alice")["groups"]) == ["ENFANT", "PARENT"]
    assert security.ldap_fetch_user(CFG, "bob")["groups"] == ["PARENT"]


def test_groupe_compte_les_membres_des_sous_groupes(annuaire):
    annuaire()
    members = security.ldap_group_members(CFG, dn("PARENT"))
    assert sorted(m["username"] for m in members) == ["alice", "bob"]
    assert [m["username"] for m in security.ldap_group_members(CFG, dn("ENFANT"))] == ["alice"]


def test_annuaire_sans_in_chain_garde_le_direct(annuaire):
    annuaire(in_chain=False)
    assert security.ldap_fetch_user(CFG, "alice")["groups"] == ["ENFANT"]
    assert [m["username"] for m in security.ldap_group_members(CFG, dn("PARENT"))] == ["bob"]
