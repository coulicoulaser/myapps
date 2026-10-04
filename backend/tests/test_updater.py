"""Mises à jour : chaîne de confiance (signature, empreinte, origine), règles de
version, extraction sûre, demande d'installation depuis l'administration."""
import hashlib
import http.server
import io
import json
import os
import tarfile
import threading
from functools import partial

import pytest

import changelog
import config
import signing
import updater

CURRENT = changelog.current_version()


def _bump(v, major=0, minor=0, patch=1):
    a, b, c = (int(x) for x in v.split("-")[0].split("."))
    if major:
        return f"{a + 1}.0.0"
    if minor:
        return f"{a}.{b + 1}.0"
    return f"{a}.{b}.{c + patch}"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """Serveur HTTP local qui joue le rôle de GitHub Releases, avec une clé de test."""
    pub = tmp_path / "pub"
    pub.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    secret, public = signing.generate_keypair()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(pub))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "UPDATE_PUBKEY", public)
    monkeypatch.setattr(config, "UPDATE_MANIFEST_URL", base + "/latest.json")

    class S:
        pass
    s = S()
    s.pub, s.data, s.base, s.secret, s.public = pub, data, base, secret, public

    def publish(version, *, install_sh="#!/bin/bash\necho installé\n", members=None, min_from="",
                comment=None, archive_url=None, tamper=False, key=None):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, content in (members or [(f"myapps-{version}/install.sh", install_sh)]):
                info = tarfile.TarInfo(name)
                payload = content.encode()
                info.size, info.mode = len(payload), 0o755
                tf.addfile(info, io.BytesIO(payload))
        blob = buf.getvalue()
        (pub / f"myapps-{version}.tar.gz").write_bytes(blob)
        manifest = {"name": "myapps", "version": version, "published": "2026-10-04T00:00:00+00:00",
                    "archive": {"url": archive_url or f"{base}/myapps-{version}.tar.gz",
                                "sha256": hashlib.sha256(blob).hexdigest(), "size": len(blob)},
                    "min_from": min_from, "notes": f"### Ajouté\n- Nouveautés {version}"}
        raw = json.dumps(manifest).encode()
        sig = signing.sign(raw, key or secret, comment or f"myapps {version}")
        if tamper:
            raw = raw.replace(b"Nouveaut", b"Nouvelle")
        (pub / "latest.json").write_bytes(raw)
        (pub / "latest.json.minisig").write_text(sig)
        return manifest
    s.publish = publish
    yield s
    httpd.shutdown()


# --- versions ---------------------------------------------------------------------
def test_ordre_semver():
    assert updater.is_newer("1.1.0", "1.0.9")
    assert updater.is_newer("1.10.0", "1.9.9")
    assert updater.is_newer("1.2.0", "1.2.0-rc.1")
    assert updater.is_newer("1.2.0-beta.10", "1.2.0-beta.2")
    assert updater.is_newer("1.2.0-beta.1", "1.2.0-beta")
    assert not updater.is_newer("1.0.0", "1.0.0")
    with pytest.raises(updater.UpdateError):
        updater.parse_version("1.0")


# --- vérification -----------------------------------------------------------------
def test_version_disponible(server):
    v = _bump(CURRENT)
    server.publish(v)
    st = updater.check("stable")
    assert st["error"] == "" and st["available"] is True and st["major"] is False
    assert st["latest"]["version"] == v and "Nouveautés" in st["latest"]["notes"]
    assert json.loads((server.data / "update-status.json").read_text())["latest"]["version"] == v


def test_meme_version_pas_disponible(server):
    server.publish(CURRENT)
    assert updater.check("stable")["available"] is False


def test_version_majeure_signalee(server):
    server.publish(_bump(CURRENT, major=1))
    st = updater.check("stable")
    assert st["available"] is True and st["major"] is True


def test_manifeste_modifie_refuse(server):
    server.publish(_bump(CURRENT), tamper=True)
    st = updater.check("stable")
    assert st["available"] is False and "signature invalide" in st["error"]


def test_autre_cle_refusee(server):
    other, _ = signing.generate_keypair()
    server.publish(_bump(CURRENT), key=other)
    assert "autre clé" in updater.check("stable")["error"]


def test_commentaire_signe_lie_a_la_version(server):
    # Signature valide mais faite pour une autre version : rejeu refusé.
    server.publish(_bump(CURRENT), comment="myapps 0.0.1")
    assert "commentaire signé" in updater.check("stable")["error"]


def test_archive_d_une_autre_origine_refusee(server):
    server.publish(_bump(CURRENT), archive_url="https://evil.example/myapps.tar.gz")
    assert "origine de l'archive" in updater.check("stable")["error"]


def test_version_minimale_bloque(server):
    v = _bump(CURRENT, minor=1)
    server.publish(v, min_from=_bump(CURRENT, patch=50))
    st = updater.check("stable")
    assert st["available"] is False and "mise à jour manuelle" in st["blocked"]


def test_http_distant_refuse():
    with pytest.raises(updater.UpdateError, match="HTTPS"):
        updater.http_get("http://example.com/latest.json", 10)


def test_cle_officielle_valide():
    _, pk = signing.parse_public_key(config.UPDATE_PUBKEY_FILE.read_text())
    assert pk is not None


# --- extraction ---------------------------------------------------------------------
def _tgz(tmp_path, members):
    p = tmp_path / "a.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for name, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tf.addfile(info)
            else:
                info.size = 1
                tf.addfile(info, io.BytesIO(b"x"))
    return p


@pytest.mark.parametrize("members", [
    [("myapps-9.9.9/../../evil", "file")],
    [("/etc/evil", "file")],
    [("autre-dossier/install.sh", "file")],
    [("myapps-9.9.9/lien", "sym")],
])
def test_extraction_refuse_les_pieges(tmp_path, members):
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(updater.UpdateError):
        updater._safe_extract(_tgz(tmp_path, members), dest, "myapps-9.9.9")
    assert not (tmp_path / "evil").exists()


def test_extraction_normale(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    top = updater._safe_extract(_tgz(tmp_path, [("myapps-9.9.9/install.sh", "file")]), dest, "myapps-9.9.9")
    assert (top / "install.sh").read_bytes() == b"x"


# --- installation (root) ----------------------------------------------------------
root_only = pytest.mark.skipif(os.geteuid() != 0, reason="installation réelle : root requis")


@pytest.fixture()
def installable(server, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path / "opt"))
    monkeypatch.setattr(config, "SERVICE_NAME", "myapps-test-" + str(os.getpid()))
    return server


@root_only
def test_installation_lance_install_sh_de_la_version(installable, tmp_path):
    marker = tmp_path / "lance"
    v = _bump(CURRENT)
    m = installable.publish(v, install_sh=f'#!/bin/bash\n[ "$1" = --update ] && echo "$3" > {marker}\n')
    assert updater.apply(updater.fetch_manifest("stable")) is True
    assert marker.read_text().strip().startswith("myapps-test-")
    st = updater.read_status()
    assert st["last_result"]["ok"] is True and st["last_result"]["version"] == m["version"]
    assert not list((tmp_path / "opt" / "releases").glob(".staging-*"))


@root_only
def test_echec_install_sh_consigne(installable):
    installable.publish(_bump(CURRENT), install_sh="#!/bin/bash\necho boum >&2\nexit 3\n")
    with pytest.raises(updater.UpdateError, match="code 3"):
        updater.apply(updater.fetch_manifest("stable"))
    st = updater.read_status()
    assert st["last_result"]["ok"] is False and st["installing"] == ""
    assert "boum" in (installable.data / "update.log").read_text()


@root_only
def test_archive_differente_du_manifeste(installable):
    v = _bump(CURRENT)
    installable.publish(v)
    m = updater.fetch_manifest("stable")
    (installable.pub / f"myapps-{v}.tar.gz").write_bytes(b"remplacee")
    with pytest.raises(updater.UpdateError, match="empreinte"):
        updater.apply(m)


@root_only
def test_majeure_exige_accord(installable):
    installable.publish(_bump(CURRENT, major=1))
    with pytest.raises(updater.UpdateError, match="majeure"):
        updater.apply(updater.fetch_manifest("stable"))


@root_only
def test_demande_pour_une_autre_version_refusee(installable):
    installable.publish(_bump(CURRENT, patch=2))
    (installable.data / "update-request").write_text(json.dumps({"version": _bump(CURRENT)}))
    assert updater.cmd_apply_request() == 1
    st = updater.read_status()
    assert not (installable.data / "update-request").exists()
    assert st["last_result"]["ok"] is False and "plus celle demandée" in st["last_result"]["message"]
    assert st["installing"] == ""


def test_mode_off_ne_contacte_rien(server, monkeypatch):
    updater.write_policy("off", "stable")
    monkeypatch.setattr(updater, "http_get", lambda *a, **k: pytest.fail("aucun accès réseau attendu"))
    assert updater.cmd_auto() == 0


def test_mode_notification_n_installe_pas(server, monkeypatch):
    updater.write_policy("notify", "stable")
    server.publish(_bump(CURRENT))
    monkeypatch.setattr(updater, "apply", lambda *a, **k: pytest.fail("pas d'installation en mode notification"))
    assert updater.cmd_auto() == 0
    assert updater.read_status()["available"] is True


# --- API d'administration ---------------------------------------------------------
def test_api_etat_et_reglages(client, admin_auth, server):
    r = client.get("/api/update/status", headers=admin_auth)
    assert r.status_code == 200 and r.json()["policy"] == {"mode": "notify", "channel": "stable"}
    r = client.put("/api/update/settings", headers=admin_auth, json={"mode": "auto", "channel": "beta"})
    assert r.json()["policy"] == {"mode": "auto", "channel": "beta"}
    assert json.loads((server.data / "update-policy.json").read_text())["mode"] == "auto"
    assert client.put("/api/update/settings", headers=admin_auth, json={"mode": "toujours"}).status_code == 400
    client.put("/api/update/settings", headers=admin_auth, json={"mode": "notify", "channel": "stable"})


def test_api_installation_depose_la_demande(client, admin_auth, server, monkeypatch):
    v = _bump(CURRENT)
    server.publish(v)
    monkeypatch.setattr(config, "UPDATER_ENABLED", False)
    client.post("/api/update/check", headers=admin_auth)
    assert client.post("/api/update/install", headers=admin_auth, json={}).status_code == 400   # sans install.sh

    monkeypatch.setattr(config, "UPDATER_ENABLED", True)
    r = client.post("/api/update/install", headers=admin_auth, json={})
    assert r.status_code == 200 and r.json()["version"] == v
    req = json.loads((server.data / "update-request").read_text())
    assert req["version"] == v and req["by"] == "admin"
    assert client.post("/api/update/install", headers=admin_auth, json={}).status_code == 409


def test_api_majeure_demande_confirmation(client, admin_auth, server, monkeypatch):
    server.publish(_bump(CURRENT, major=1))
    monkeypatch.setattr(config, "UPDATER_ENABLED", True)
    client.post("/api/update/check", headers=admin_auth)
    assert client.post("/api/update/install", headers=admin_auth, json={}).status_code == 400
    assert client.post("/api/update/install", headers=admin_auth, json={"allow_major": True}).status_code == 200


def test_auto_ne_retente_pas_une_version_en_echec(server, monkeypatch):
    v = _bump(CURRENT)
    updater.write_policy("auto", "stable")
    server.publish(v)
    updater._update_status(last_result={"version": v, "ok": False, "at": "x", "message": "annulée"})
    monkeypatch.setattr(updater, "apply", lambda *a, **k: pytest.fail("pas de nouvel essai automatique"))
    assert updater.cmd_auto() == 0


@root_only
def test_auto_installe_en_mode_automatique(installable, tmp_path):
    marker = tmp_path / "lance"
    updater.write_policy("auto", "stable")
    installable.publish(_bump(CURRENT), install_sh=f"#!/bin/bash\ntouch {marker}\n")
    assert updater.cmd_auto() == 0 and marker.exists()


def test_refus_efface_la_version_annoncee(client, admin_auth, server):
    v = _bump(CURRENT)
    server.publish(v)
    assert client.post("/api/update/check", headers=admin_auth).json()["status"]["available"] is True
    server.publish(v, tamper=True)
    st = client.post("/api/update/check", headers=admin_auth).json()["status"]
    assert st["available"] is False and st["latest"] is None and "signature" in st["error"]
    assert client.get("/api/update/status", headers=admin_auth).json()["status"]["available"] is False


class _Redirector(http.server.BaseHTTPRequestHandler):
    target = ""

    def do_HEAD(self):
        self.send_response(302)
        self.send_header("Location", self.target)
        self.end_headers()

    def log_message(self, *a, **k):
        pass


def test_premier_saut_de_redirection():
    _Redirector.target = "https://github.com/x/y/releases/download/v9.9.9/latest.json"
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert updater.first_redirect(f"http://127.0.0.1:{httpd.server_port}/latest.json") == _Redirector.target
    finally:
        httpd.shutdown()


def test_stable_lit_manifeste_et_signature_dans_la_meme_version(monkeypatch):
    """Régression 1.3.0 : le cache GitHub redirigeait latest.json vers la 1.2.0 et sa
    signature vers la 1.3.0 → signature refusée juste après la publication."""
    monkeypatch.setattr(config, "UPDATE_MANIFEST_URL", "")
    monkeypatch.setattr(config, "UPDATE_REPO", "acme/myapps")
    monkeypatch.setattr(updater, "first_redirect",
                        lambda url: "https://github.com/acme/myapps/releases/download/v1.3.0/latest.json")
    m, s = updater.manifest_urls("stable")
    assert m == "https://github.com/acme/myapps/releases/download/v1.3.0/latest.json"
    assert s == m + ".minisig"
    monkeypatch.setattr(updater, "first_redirect", lambda url: "https://evil.example/latest.json")
    with pytest.raises(updater.UpdateError, match="redirection inattendue"):
        updater.manifest_urls("stable")
