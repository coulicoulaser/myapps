"""Secrets en base (compte de service AD, secret SSO) : chiffrés, jamais en clair, et jamais
renvoyés au navigateur."""
from sqlmodel import select

import security
from models import AppSetting


def test_secrets_encrypted_at_rest(db):
    security.set_setting(db, "ldap_bind_password", "Tr3sSecret!")
    security.set_setting(db, "ldap_server", "dc.exemple.local")
    db.commit()
    raw = {r.key: r.value for r in db.exec(select(AppSetting)).all()}
    assert raw["ldap_bind_password"].startswith("gAAAAA") and "Tr3sSecret" not in raw["ldap_bind_password"]
    assert raw["ldap_server"] == "dc.exemple.local"
    assert security.get_setting(db, "ldap_bind_password") == "Tr3sSecret!"


def test_plaintext_migrated(db):
    row = db.exec(select(AppSetting).where(AppSetting.key == "sso_client_secret")).first()
    if row:
        row.value = "ancienClair"
    else:
        row = AppSetting(key="sso_client_secret", value="ancienClair")
    db.add(row); db.commit()
    assert security.encrypt_stored_secrets(db) >= 1
    assert db.exec(select(AppSetting).where(AppSetting.key == "sso_client_secret")).first().value.startswith("gAAAAA")
    assert security.get_setting(db, "sso_client_secret") == "ancienClair"
    assert security.encrypt_stored_secrets(db) == 0


def test_settings_api_never_returns_secret(client, admin_auth, db):
    security.set_setting(db, "ldap_bind_password", "Tr3sSecret!"); db.commit()
    body = client.get("/api/settings", headers=admin_auth).text
    assert "Tr3sSecret" not in body and "gAAAAA" not in body
