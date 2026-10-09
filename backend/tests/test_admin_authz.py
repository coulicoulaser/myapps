"""Tests NÉGATIFS d'autorisation sur toutes les routes admin : 403 pour un USER,
401 sans jeton. Les corps sont valides pour que le contrôle d'accès soit bien atteint."""
import pytest

_UPLOAD_FILES = {"file": ("logo.png", b"\x89PNG\r\n\x1a\n", "image/png")}

ADMIN_ROUTES = [
    ("settings-get",        "GET",    "/api/settings",                 {}),
    ("settings-put",        "PUT",    "/api/settings",                 {"json": {"ldap": {}, "sso": {}, "branding": {}}}),
    ("settings-ldap-test",  "POST",   "/api/settings/ldap/test",       {}),
    ("groups-list",         "GET",    "/api/groups",                   {}),
    ("groups-create",       "POST",   "/api/groups",                   {"json": {"name": "G"}}),
    ("groups-sync",         "POST",   "/api/groups/sync",              {}),
    ("groups-patch",        "PATCH",  "/api/groups/1",                 {"json": {"description": "x"}}),
    ("groups-delete-one",   "DELETE", "/api/groups/1",                 {}),
    ("groups-delete-ad",    "DELETE", "/api/groups",                   {}),
    ("groups-import-users", "POST",   "/api/groups/1/import-users",    {}),
    ("users-list",          "GET",    "/api/users",                    {}),
    ("users-create",        "POST",   "/api/users",                    {"json": {"username": "zz", "full_name": "ZZ", "password": "pw-123456"}}),
    ("users-patch",         "PATCH",  "/api/users/1",                  {"json": {"role": "ADMIN"}}),
    ("users-delete",        "DELETE", "/api/users/1",                  {}),
    ("changelog",           "GET",    "/api/changelog",                {}),
    ("apps-list",           "GET",    "/api/apps",                     {}),
    ("apps-create",         "POST",   "/api/apps",                     {"json": {"name": "A", "url": "https://a.tld"}}),
    ("apps-patch",          "PATCH",  "/api/apps/1",                   {"json": {"name": "A", "url": "https://a.tld"}}),
    ("apps-delete",         "DELETE", "/api/apps/1",                   {}),
    ("app-groups-list",     "GET",    "/api/app-groups",               {}),
    ("app-groups-create",   "POST",   "/api/app-groups",               {"json": {"name": "G"}}),
    ("app-groups-patch",    "PATCH",  "/api/app-groups/1",             {"json": {"name": "G"}}),
    ("app-groups-delete",   "DELETE", "/api/app-groups/1",             {}),
    ("app-groups-reorder",  "POST",   "/api/app-groups/1/reorder",     {"json": {"ids": [1]}}),
    ("dashboards-list",     "GET",    "/api/dashboards",               {}),
    ("dashboards-create",   "POST",   "/api/dashboards",               {"json": {"name": "D", "slug": "d"}}),
    ("dashboards-patch",    "PATCH",  "/api/dashboards/1",             {"json": {"name": "D", "slug": "d"}}),
    ("dashboards-delete",   "DELETE", "/api/dashboards/1",             {}),
    ("dashboards-reorder",  "POST",   "/api/dashboards/1/reorder",     {"json": {"ids": [1]}}),
    ("sites-create",        "POST",   "/api/sites",                    {"json": {"name": "Lyon", "latitude": 45.7, "longitude": 4.8}}),
    ("sites-patch",         "PATCH",  "/api/sites/1",                  {"json": {"name": "Lyon", "latitude": 45.7, "longitude": 4.8}}),
    ("sites-delete",        "DELETE", "/api/sites/1",                  {}),
    ("geocode",             "GET",    "/api/geocode?q=Lyon",           {}),
    ("logo",                "GET",    "/api/logo?url=example.invalid",          {}),
    ("upload",              "POST",   "/api/upload",                   {"files": _UPLOAD_FILES}),
    ("setup-complete",      "POST",   "/api/setup/complete",           {}),
    ("setup-restart",       "POST",   "/api/setup/restart",            {}),
    ("update-status",       "GET",    "/api/update/status",            {}),
    ("update-check",        "POST",   "/api/update/check",             {}),
    ("update-settings",     "PUT",    "/api/update/settings",          {"json": {"mode": "off", "channel": "stable"}}),
    ("update-install",      "POST",   "/api/update/install",           {"json": {}}),
]
_IDS = [r[0] for r in ADMIN_ROUTES]


@pytest.mark.parametrize("_id,method,path,extra", ADMIN_ROUTES, ids=_IDS)
def test_non_admin_forbidden(client, user_auth, _id, method, path, extra):
    resp = client.request(method, path, headers=user_auth, **extra)
    assert resp.status_code == 403, f"{method} {path} : {resp.status_code} {resp.text[:200]}"


@pytest.mark.parametrize("_id,method,path,extra", ADMIN_ROUTES, ids=_IDS)
def test_anonymous_unauthorized(client, _id, method, path, extra):
    resp = client.request(method, path, **extra)
    assert resp.status_code == 401, f"{method} {path} : {resp.status_code} {resp.text[:200]}"


def test_admin_allowed(client, admin_auth):
    assert client.get("/api/users", headers=admin_auth).status_code == 200


def test_forged_token_rejected(client):
    import jwt
    bad = jwt.encode({"sub": "admin"}, "pas-le-bon-secret-" + "y" * 30, algorithm="HS256")
    assert client.get("/api/users", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
