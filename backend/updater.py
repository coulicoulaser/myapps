"""Mises à jour autonomes depuis les versions publiées sur GitHub.

Chaîne de confiance
  Chaque version publiée porte un manifeste `latest.json` (version, URL et SHA-256 de
  l'archive, version minimale requise, notes) signé au format minisign
  (`latest.json.minisig`). Seul le manifeste signé par la clé livrée avec l'application
  (`update_key.pub`) est accepté ; l'archive doit ensuite avoir exactement l'empreinte
  annoncée. Une version n'est jamais installée si elle n'est pas plus récente.

Répartition des rôles
  - L'application (compte sans privilèges) vérifie les versions disponibles, affiche
    l'état et dépose une demande d'installation (`update-request`) ; elle ne peut pas
    modifier son propre code.
  - `<service>-update.service` (root) est déclenché par le timer quotidien (`auto`) ou
    par l'apparition de la demande (`apply-request`). Il télécharge, vérifie, extrait,
    puis lance le `install.sh --update` de la nouvelle version : celui-ci installe dans
    releases/<version>, bascule le lien `current`, contrôle /api/health et revient à la
    version précédente en cas d'échec.

Fichiers d'échange (dossier de données)
  update-policy.json   mode (notify|auto|off) et canal, écrit par l'application
  update-status.json   dernière vérification, version disponible, historique
  update-request       demande d'installation déposée depuis l'administration
  update.log           sortie des dernières installations

Usage (root) : python updater.py check | auto | apply-request
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import changelog
import config
import signing

MODES = ("notify", "auto", "off")
CHANNELS = ("stable", "beta")
DEFAULT_POLICY = {"mode": "notify", "channel": "stable"}
MAX_MANIFEST = 256 * 1024
MAX_ARCHIVE = 100 * 1024 * 1024
HISTORY_LEN = 20


class UpdateError(Exception):
    pass


def _data() -> Path:
    return config.DATA_DIR


def status_file() -> Path:
    return _data() / "update-status.json"


def policy_file() -> Path:
    return _data() / "update-policy.json"


def request_file() -> Path:
    return _data() / "update-request"


def log_file() -> Path:
    return _data() / "update.log"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- versions (SemVer 2.0 : 1.2.0-beta.1 < 1.2.0) --------------------------------
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?$")


def parse_version(v: str):
    m = _SEMVER.match((v or "").strip())
    if not m:
        raise UpdateError(f"numéro de version invalide : {v!r}")
    major, minor, patch, pre = m.groups()
    # Une version finale passe après ses pré-versions ; les identifiants numériques
    # se comparent comme des nombres et passent avant les identifiants texte.
    pre_key = (1,) if pre is None else (0,) + tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (int(major), int(minor), int(patch), pre_key)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def is_prerelease(v: str) -> bool:
    return "-" in v


def current_version() -> str:
    return changelog.current_version()


# --- fichiers d'échange -----------------------------------------------------------
def _write_json(path: Path, data: dict) -> None:
    """Écriture atomique ; fichier rendu au propriétaire du dossier de données (le
    compte du service), même quand l'écriture est faite par root."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        st = path.parent.stat()
        if os.geteuid() == 0:
            os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, 0o640)
    except OSError:
        pass
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_policy() -> dict:
    p = {**DEFAULT_POLICY, **_read_json(policy_file(), {})}
    if p["mode"] not in MODES:
        p["mode"] = DEFAULT_POLICY["mode"]
    if p["channel"] not in CHANNELS:
        p["channel"] = DEFAULT_POLICY["channel"]
    return {"mode": p["mode"], "channel": p["channel"]}


def write_policy(mode: str, channel: str) -> dict:
    if mode not in MODES or channel not in CHANNELS:
        raise UpdateError("mode ou canal inconnu")
    p = {"mode": mode, "channel": channel}
    _write_json(policy_file(), p)
    return p


def read_status() -> dict:
    return _read_json(status_file(), {})


def _update_status(**changes) -> dict:
    st = read_status()
    st.update(changes)
    _write_json(status_file(), st)
    return st


def log(msg: str) -> None:
    line = f"{now_iso()} {msg}"
    print(line, flush=True)
    try:
        lf = log_file()
        lines = (lf.read_text(encoding="utf-8").splitlines() if lf.exists() else [])[-399:]
        lines.append(line)
        lf.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if os.geteuid() == 0:
            st = lf.parent.stat()
            os.chown(lf, st.st_uid, st.st_gid)
    except OSError:
        pass


# --- téléchargements --------------------------------------------------------------
def _is_local(url: str) -> bool:
    return urllib.parse.urlparse(url).hostname in ("127.0.0.1", "localhost", "::1")


def http_get(url: str, limit: int, accept: str = "*/*") -> bytes:
    """GET borné en taille. HTTPS obligatoire (HTTP toléré seulement en local, pour les tests)."""
    scheme = urllib.parse.urlparse(url).scheme
    if scheme != "https" and not (scheme == "http" and _is_local(url)):
        raise UpdateError(f"URL refusée (HTTPS requis) : {url}")
    req = urllib.request.Request(url, headers={"User-Agent": f"MyApps-updater/{current_version()}",
                                               "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(f"aucune version publiée pour l'instant ({url} introuvable)")
        raise UpdateError(f"téléchargement impossible ({url}) : HTTP {e.code}")
    except Exception as e:
        raise UpdateError(f"téléchargement impossible ({url}) : {e}")
    if len(data) > limit:
        raise UpdateError(f"réponse trop volumineuse : {url}")
    return data


def trusted_public_key() -> str:
    if config.UPDATE_PUBKEY:
        return config.UPDATE_PUBKEY
    try:
        return config.UPDATE_PUBKEY_FILE.read_text(encoding="utf-8")
    except OSError:
        raise UpdateError("clé publique de mise à jour absente (update_key.pub)")


def manifest_urls(channel: str) -> tuple[str, str]:
    """(URL du manifeste, URL de sa signature) pour un canal."""
    if config.UPDATE_MANIFEST_URL:
        u = config.UPDATE_MANIFEST_URL
        return u, u + ".minisig"
    repo = config.UPDATE_REPO
    if channel == "stable":
        # Redirection GitHub vers la dernière version finale publiée : ni API ni quota.
        base = f"https://github.com/{repo}/releases/latest/download/"
        return base + "latest.json", base + "latest.json.minisig"
    # Bêta : la version la plus récente, pré-versions comprises (API GitHub, 60 req/h/IP).
    raw = http_get(f"https://api.github.com/repos/{repo}/releases?per_page=30", 2 * 1024 * 1024,
                   accept="application/vnd.github+json")
    best = None
    for rel in json.loads(raw):
        if rel.get("draft"):
            continue
        tag = str(rel.get("tag_name", "")).lstrip("v")
        try:
            key = parse_version(tag)
        except UpdateError:
            continue
        assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
        if "latest.json" in assets and "latest.json.minisig" in assets and (best is None or key > best[0]):
            best = (key, assets["latest.json"], assets["latest.json.minisig"])
    if not best:
        raise UpdateError("aucune version publiée trouvée")
    return best[1], best[2]


def _allowed_archive(url: str, manifest_url: str) -> bool:
    if url.startswith(f"https://github.com/{config.UPDATE_REPO}/releases/download/"):
        return True
    # Source de test/fork : archive sur la même origine que le manifeste.
    a, m = urllib.parse.urlparse(url), urllib.parse.urlparse(manifest_url)
    return bool(config.UPDATE_MANIFEST_URL) and (a.scheme, a.netloc) == (m.scheme, m.netloc)


def validate_manifest(data: bytes, signature: str, manifest_url: str) -> dict:
    """Vérifie la signature puis le contenu du manifeste ; lève UpdateError sinon."""
    try:
        trusted = signing.verify(data, signature, trusted_public_key())
    except signing.SignatureError as e:
        raise UpdateError(f"manifeste refusé : {e}")
    try:
        m = json.loads(data)
    except ValueError:
        raise UpdateError("manifeste illisible")
    if not isinstance(m, dict) or m.get("name") != "myapps":
        raise UpdateError("manifeste : application inattendue")
    version = str(m.get("version", ""))
    parse_version(version)
    # Le commentaire signé lie la signature à cette version précise.
    if trusted != f"myapps {version}":
        raise UpdateError("manifeste : commentaire signé incohérent")
    arc = m.get("archive") or {}
    url, sha, size = str(arc.get("url", "")), str(arc.get("sha256", "")).lower(), arc.get("size")
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE:
        raise UpdateError("manifeste : empreinte ou taille d'archive invalide")
    if not _allowed_archive(url, manifest_url):
        raise UpdateError(f"manifeste : origine de l'archive refusée ({url})")
    if m.get("min_from"):
        parse_version(str(m["min_from"]))
    return {"name": "myapps", "version": version, "published": str(m.get("published", "")),
            "archive": {"url": url, "sha256": sha, "size": size},
            "min_from": str(m.get("min_from") or ""), "notes": str(m.get("notes") or "")[:20000]}


def fetch_manifest(channel: str) -> dict:
    murl, surl = manifest_urls(channel)
    data = http_get(murl, MAX_MANIFEST)
    sig = http_get(surl, 4096).decode("utf-8", "replace")
    return validate_manifest(data, sig, murl)


def assess(manifest: dict, current: str) -> dict:
    """Ce qu'on peut faire de ce manifeste depuis la version installée."""
    v = manifest["version"]
    newer = is_newer(v, current)
    major = newer and parse_version(v)[0] > parse_version(current)[0]
    blocked = ""
    if newer and manifest.get("min_from") and parse_version(current) < parse_version(manifest["min_from"]):
        blocked = (f"La version {v} demande d'abord la {manifest['min_from']} : "
                   "mise à jour manuelle requise (voir les notes de version).")
    return {"available": newer and not blocked, "major": major, "blocked": blocked}


def check(channel: str | None = None) -> dict:
    """Interroge la source de mises à jour et enregistre le résultat dans update-status.json."""
    channel = channel or read_policy()["channel"]
    current = current_version()
    try:
        m = fetch_manifest(channel)
        a = assess(m, current)
        return _update_status(checked_at=now_iso(), current=current, channel=channel, error="",
                              latest={k: m[k] for k in ("version", "published", "notes", "min_from")},
                              **a)
    except UpdateError as e:
        # Rien de ce qui a été vu avant n'est plus garanti : on oublie la version annoncée.
        return _update_status(checked_at=now_iso(), current=current, channel=channel, error=str(e),
                              available=False, major=False, blocked="", latest=None)


# --- installation (root) ----------------------------------------------------------
def _safe_extract(archive: Path, dest: Path, top: str) -> Path:
    """Extrait l'archive en refusant tout ce qui sortirait de `dest` (chemins absolus,
    « .. », liens, fichiers spéciaux). Tout doit être sous `top/`."""
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        for mbr in members:
            name = mbr.name
            parts = Path(name).parts
            if name.startswith("/") or ".." in parts or not parts or parts[0] != top:
                raise UpdateError(f"archive refusée : chemin suspect « {name} »")
            if not (mbr.isfile() or mbr.isdir()):
                raise UpdateError(f"archive refusée : élément non autorisé « {name} »")
            mbr.uid = mbr.gid = 0
            mbr.uname = mbr.gname = "root"
            mbr.mode = 0o755 if (mbr.isdir() or mbr.mode & 0o111) else 0o644
        # Python ≥ 3.12 : filtre « data » de la bibliothèque standard en plus de nos contrôles.
        tf.extractall(dest, members=members, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return dest / top


def _record(version: str, ok: bool, message: str) -> None:
    st = read_status()
    entry = {"version": version, "ok": ok, "at": now_iso(), "message": message}
    hist = ([entry] + list(st.get("history") or []))[:HISTORY_LEN]
    _update_status(last_result=entry, history=hist, installing="")


def apply(manifest: dict, allow_major: bool = False) -> bool:
    """Télécharge, vérifie et installe la version du manifeste (déjà validé).
    Tout échec est consigné dans l'historique puis relevé en UpdateError."""
    try:
        return _apply(manifest, allow_major)
    except UpdateError as e:
        _record(manifest.get("version", "?"), False, str(e))
        log(f"Échec de la mise à jour vers {manifest.get('version', '?')} : {e}")
        raise


def _apply(manifest: dict, allow_major: bool) -> bool:
    if os.geteuid() != 0:
        raise UpdateError("l'installation d'une mise à jour demande les droits root")
    if not (config.INSTALL_DIR and config.SERVICE_NAME):
        raise UpdateError("installation non gérée par install.sh (MYAPPS_INSTALL_DIR absent)")
    current, v = current_version(), manifest["version"]
    a = assess(manifest, current)
    if not is_newer(v, current):
        raise UpdateError(f"la version {v} n'est pas plus récente que {current}")
    if a["blocked"]:
        raise UpdateError(a["blocked"])
    if a["major"] and not allow_major:
        raise UpdateError(f"changement de version majeure ({current} → {v}) : confirmation manuelle requise")

    releases = Path(config.INSTALL_DIR) / "releases"
    staging = releases / f".staging-{v}"
    lock = open("/run/lock/myapps-update-" + config.SERVICE_NAME + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise UpdateError("une mise à jour est déjà en cours")
    try:
        _update_status(installing=v)
        log(f"Mise à jour {current} → {v} : téléchargement")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, mode=0o700)
        arc = manifest["archive"]
        data = http_get(arc["url"], MAX_ARCHIVE)
        if len(data) != arc["size"] or hashlib.sha256(data).hexdigest() != arc["sha256"]:
            raise UpdateError("archive refusée : taille ou empreinte différente du manifeste signé")
        tgz = staging / "release.tar.gz"
        tgz.write_bytes(data)
        src = _safe_extract(tgz, staging, f"myapps-{v}")
        log(f"Archive vérifiée (sha256 {arc['sha256'][:12]}…), installation")
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        proc = subprocess.run(["bash", str(src / "install.sh"), "--update", "--name", config.SERVICE_NAME],
                              cwd=src, env=env, capture_output=True, text=True, timeout=1800)
        for line in (proc.stdout + proc.stderr).splitlines():
            if line.strip():
                log("  " + re.sub(r"\x1b\[[0-9;]*m", "", line))
        if proc.returncode != 0:
            raise UpdateError(f"install.sh a échoué (code {proc.returncode}) : voir le journal")
        _record(v, True, f"Version {v} installée")
        log(f"Mise à jour terminée : {v}")
        return True
    except subprocess.TimeoutExpired:
        raise UpdateError("install.sh n'a pas terminé en 30 minutes")
    except (OSError, tarfile.TarError) as e:
        raise UpdateError(str(e))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        lock.close()


def cmd_auto() -> int:
    pol = read_policy()
    if pol["mode"] == "off":
        print("Mises à jour désactivées.")
        return 0
    st = check(pol["channel"])
    if st.get("error"):
        print("Vérification impossible :", st["error"])
        return 1
    if not st.get("available"):
        print("Aucune mise à jour à installer.", st.get("blocked") or "")
        return 0
    v = st["latest"]["version"]
    if pol["mode"] != "auto":
        print(f"Version {v} disponible (mode notification : installation depuis l'administration).")
        return 0
    if st.get("major"):
        print(f"Version majeure {v} disponible : installation manuelle depuis l'administration.")
        return 0
    last = st.get("last_result") or {}
    if last.get("version") == v and not last.get("ok"):
        # Déjà tentée et annulée : pas de nouvel essai chaque nuit, l'admin relance à la main.
        print(f"La version {v} a déjà échoué ({last.get('message')}) : pas de nouvel essai automatique.")
        return 0
    try:
        apply(fetch_manifest(pol["channel"]))
        return 0
    except UpdateError as e:
        print("Échec :", e)
        return 1


def cmd_apply_request() -> int:
    rf = request_file()
    if not rf.exists():
        return 0
    # Contenu déposé par l'application : considéré comme non fiable. Seuls la version
    # visée et l'accord pour une version majeure sont lus ; tout le reste est revérifié.
    req = _read_json(rf, {})
    wanted = str(req.get("version", ""))[:40] if isinstance(req, dict) else ""
    allow_major = bool(req.get("allow_major")) if isinstance(req, dict) else False
    # « installing » posé avant de retirer la demande : l'interface ne voit jamais de trou.
    _update_status(installing=wanted or "?")
    rf.unlink(missing_ok=True)
    try:
        m = fetch_manifest(read_policy()["channel"])
        if wanted and m["version"] != wanted:
            raise UpdateError(f"la version publiée ({m['version']}) n'est plus celle demandée ({wanted}) : revérifiez")
    except UpdateError as e:
        _record(wanted or "?", False, str(e))
        log(f"Demande d'installation refusée : {e}")
        return 1
    try:
        apply(m, allow_major=allow_major)
        return 0
    except UpdateError:
        return 1


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "check":
        st = check()
        print(json.dumps({k: st.get(k) for k in ("current", "latest", "available", "major", "blocked", "error")},
                         ensure_ascii=False, indent=2))
        return 1 if st.get("error") else 0
    if cmd == "auto":
        return cmd_auto()
    if cmd == "apply-request":
        return cmd_apply_request()
    print(__doc__.split("Usage")[-1].strip() if "Usage" in __doc__ else "check | auto | apply-request")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
