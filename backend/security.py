"""Authentification & autorisation — MyApps.

Stratégie :
  1. compte local (bcrypt) — le premier administrateur est créé par l'assistant
     de premier démarrage (voir setup_routes.py) ;
  2. AD/LDAP si activé dans Réglages (bind service → recherche → re-bind),
     avec synchronisation des groupes (memberOf) et du site (attribut configurable) ;
  3. SSO OIDC (Authentik, Keycloak, Entra ID…), flux authorization-code, voir /api/auth/sso/*.

Le JWT porte le username. ADMIN gère le portail ; la visibilité des apps/dashboards
dépend des groupes + exceptions par utilisateur (cf. rights.py).
"""
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from database import get_session
from models import EVERYONE_GROUP, AppSetting, Group, GroupMembership, Site, User, utcnow

log = logging.getLogger("myapps.security")


def _secret_or_ephemeral(name: str, current: str) -> str:
    """Secret d'environnement s'il est défini et assez long ; sinon secret aléatoire
    éphémère (les sessions ne survivent pas au redémarrage) et alerte dans le journal.
    L'installeur écrit un JWT_SECRET aléatoire dans /etc/<service>/<service>.env."""
    v = (current or "").strip()
    if len(v) >= 32:
        return v
    log.critical("%s absent ou trop court : secret aléatoire éphémère généré "
                 "(les sessions ne survivront pas au redémarrage).", name)
    return secrets.token_urlsafe(48)


JWT_SECRET = _secret_or_ephemeral("JWT_SECRET", os.getenv("JWT_SECRET", ""))
JWT_ALGO = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))
JWT_REMEMBER_DAYS = int(os.getenv("JWT_REMEMBER_DAYS", "30"))

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# --- mots de passe -----------------------------------------------------------
MIN_PASSWORD_LENGTH = 8


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, h: Optional[str]) -> bool:
    if not h:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], h.encode("utf-8"))
    except Exception:
        return False


def create_token(username: str, remember: bool = False) -> str:
    delta = timedelta(days=JWT_REMEMBER_DAYS) if remember else timedelta(hours=JWT_EXPIRE_HOURS)
    exp = datetime.now(timezone.utc) + delta
    return jwt.encode({"sub": username, "exp": exp}, JWT_SECRET, algorithm=JWT_ALGO)


# --- validation des URL saisies ----------------------------------------------
# Les URL d'images (logo, fonds) finissent dans des attributs src et des url(...) CSS :
# http(s) ou chemin local, sans guillemets ni parenthèses.
_IMAGE_URL_RE = re.compile(r"^(https?://|/)[^\s\"'()<>\\]*$", re.IGNORECASE)
# Liens d'applications : tout schéma (rdp://, ssh://, mailto:…) sauf ceux qui exécutent du code.
_FORBIDDEN_SCHEMES = ("javascript:", "data:", "vbscript:", "file:")


def clean_image_url(value: Optional[str], field: str = "image") -> Optional[str]:
    v = (value or "").strip()
    if not v:
        return None
    if not _IMAGE_URL_RE.match(v):
        raise HTTPException(400, f"URL {field} invalide (http(s)://… ou /chemin attendu)")
    return v


def clean_link_url(value: str) -> str:
    v = (value or "").strip()
    compact = re.sub(r"[\s\x00-\x1f]", "", v).lower()
    if not v or compact.startswith(_FORBIDDEN_SCHEMES):
        raise HTTPException(400, "Lien invalide")
    return v


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def clean_color(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip()
    if not v:
        return None
    if not _HEX_RE.match(v):
        raise HTTPException(400, "Couleur invalide (format #RRGGBB attendu)")
    return v.lower()


# --- config clé/valeur -------------------------------------------------------
def get_setting(db: Session, key: str, default=None):
    row = db.exec(select(AppSetting).where(AppSetting.key == key)).first()
    return row.value if row and row.value is not None else default


def set_setting(db: Session, key: str, value):
    row = db.exec(select(AppSetting).where(AppSetting.key == key)).first()
    val = "" if value is None else str(value)
    if row:
        row.value = val
        db.add(row)
    else:
        db.add(AppSetting(key=key, value=val))


def _as_bool(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


DEFAULT_ACCENT = "#3b82f6"
SEARCH_ENGINES = {
    "google": ("Google", "https://www.google.com/search?q="),
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q="),
    "bing": ("Bing", "https://www.bing.com/search?q="),
    "qwant": ("Qwant", "https://www.qwant.com/?q="),
    "none": ("", ""),
}


def get_branding_config(db: Session) -> dict:
    g = lambda k, d=None: get_setting(db, k, d)  # noqa: E731
    engine = g("search_engine", "google") or "google"
    if engine not in SEARCH_ENGINES:
        engine = "google"
    return {
        "portal_name": g("portal_name", "MyApps") or "MyApps",
        "logo_url": g("logo_url", "") or "",
        "show_name": _as_bool(g("show_name", "1")),
        "logo_plate": _as_bool(g("logo_plate", "0")),
        "accent_color": g("accent_color", DEFAULT_ACCENT) or DEFAULT_ACCENT,
        "login_background": g("login_background", "") or "",
        "dashboard_background": g("dashboard_background", "") or "",
        "search_engine": engine,
        "search_engine_name": SEARCH_ENGINES[engine][0],
        "search_engine_url": SEARCH_ENGINES[engine][1],
    }


def get_ldap_config(db: Session) -> dict:
    g = lambda k, d=None: get_setting(db, k, d)  # noqa: E731
    return {
        "enabled": _as_bool(g("ldap_enabled", "0")),
        "server": g("ldap_server", "") or "",
        "port": int(g("ldap_port", "389") or 389),
        "use_ssl": _as_bool(g("ldap_use_ssl", "0")),
        "base_dn": g("ldap_base_dn", "") or "",
        "user_dn_template": g("ldap_user_dn_template", "{username}") or "{username}",
        "search_filter": g("ldap_search_filter", "(sAMAccountName={username})") or "(sAMAccountName={username})",
        "group_filter": g("ldap_group_filter", "(objectClass=group)") or "(objectClass=group)",
        "site_attr": g("ldap_site_attr", "physicalDeliveryOfficeName") or "physicalDeliveryOfficeName",
        "bind_user": g("ldap_bind_user", "") or "",
        "bind_password": g("ldap_bind_password", "") or "",
    }


def get_sso_config(db: Session) -> dict:
    g = lambda k, d=None: get_setting(db, k, d)  # noqa: E731
    return {
        "enabled": _as_bool(g("sso_enabled", "0")),
        "provider": g("sso_provider", "SSO") or "SSO",
        "authority": g("sso_authority", "") or "",       # issuer OIDC
        "client_id": g("sso_client_id", "") or "",
        "client_secret": g("sso_client_secret", "") or "",
        "redirect_uri": g("sso_redirect_uri", "") or "",
        "scopes": g("sso_scopes", "openid email profile") or "openid email profile",
        "groups_claim": g("sso_groups_claim", "groups") or "groups",
        "auto_create": _as_bool(g("sso_auto_create", "1")),
    }


# --- mapping site ------------------------------------------------------------
def map_site(db: Session, site_raw: Optional[str]) -> Optional[int]:
    """Associe une valeur d'attribut AD à un Site connu (sur le nom ou le slug)."""
    if not site_raw:
        return None
    v = site_raw.strip().lower()
    for s in db.exec(select(Site)).all():
        if s.slug.lower() in v or s.name.lower() in v or v in s.name.lower():
            return s.id
    return None


# --- LDAP --------------------------------------------------------------------
def _domain_from_base(base_dn: str) -> str:
    parts = [p.split("=", 1)[1] for p in base_dn.split(",") if p.strip().upper().startswith("DC=")]
    return ".".join(parts)


def _ldap_server(cfg: dict):
    from ldap3 import ALL, Server
    return Server(cfg["server"], port=cfg["port"], use_ssl=cfg["use_ssl"], get_info=ALL,
                  connect_timeout=8)


def _service_conn(cfg: dict):
    """Connexion avec le compte de service (bind), ou None."""
    from ldap3 import Connection
    if not (cfg.get("bind_user") and cfg.get("bind_password")):
        return None
    domain = _domain_from_base(cfg["base_dn"])
    bind_user = cfg["bind_user"]
    if "@" not in bind_user and "\\" not in bind_user and "," not in bind_user and domain:
        bind_user = f"{bind_user}@{domain}"
    return Connection(_ldap_server(cfg), user=bind_user, password=cfg["bind_password"],
                      auto_bind=True, receive_timeout=15)


def _ldap_escape(v: str) -> str:
    """Échappement RFC 4515 d'une valeur insérée dans un filtre LDAP."""
    return (v.replace("\\", "\\5c").replace("*", "\\2a").replace("(", "\\28")
             .replace(")", "\\29").replace("\x00", "\\00"))


def _user_filter(cfg: dict, username: str) -> str:
    return cfg["search_filter"].format(username=_ldap_escape(username))


def _attr(e, name: str) -> Optional[str]:
    """Première valeur d'un attribut ldap3, None s'il est absent ou vide.
    ldap3 renvoie [] pour un attribut absent : str() donnerait la chaîne « [] »."""
    if name not in e:
        return None
    vals = e[name].values
    v = str(vals[0]).strip() if vals else ""
    return v or None


def ldap_fetch_user(cfg: dict, username: str) -> Optional[dict]:
    """Retourne {dn, display_name, email, groups[], site} pour un user, via compte service."""
    from ldap3 import SUBTREE
    try:
        conn = _service_conn(cfg)
        if not conn:
            return None
        attrs = ["distinguishedName", "displayName", "mail", "memberOf", cfg["site_attr"]]
        conn.search(cfg["base_dn"], _user_filter(cfg, username), SUBTREE, attributes=attrs)
        if not conn.entries:
            conn.unbind()
            return None
        e = conn.entries[0]
        user_dn = _attr(e, "distinguishedName") or ""

        groups = []
        if user_dn and cfg.get("group_filter"):
            grp_flt = f"(&{cfg['group_filter']}(member={_ldap_escape(user_dn)}))"
            conn.search(cfg["base_dn"], grp_flt, SUBTREE, attributes=["cn"])
            groups = [str(ge.cn) for ge in conn.entries if "cn" in ge]
        elif "memberOf" in e:
            member_of = list(e.memberOf.values)
            groups = [m.group(1) for m in (re.search(r"CN=([^,]+)", dn) for dn in member_of) if m]

        site_vals = e[cfg["site_attr"]].values if cfg["site_attr"] in e else []
        conn.unbind()
        return {
            "dn": user_dn,
            "display_name": _attr(e, "displayName") or username,
            "email": _attr(e, "mail"),
            "groups": groups,
            "site": site_vals[0] if site_vals else None,
        }
    except Exception as ex:
        log.warning("LDAP fetch fail '%s': %s", username, ex)
        return None


def ldap_authenticate(cfg: dict, username: str, password: str) -> bool:
    """Vérifie le mot de passe en bindant en tant que l'utilisateur."""
    if not cfg.get("server") or not password:
        return False
    try:
        from ldap3 import SUBTREE, Connection
        server = _ldap_server(cfg)
        domain = _domain_from_base(cfg["base_dn"])
        sc = _service_conn(cfg)
        if sc:
            sc.search(cfg["base_dn"], _user_filter(cfg, username),
                      SUBTREE, attributes=["distinguishedName"])
            if not sc.entries:
                sc.unbind()
                return False
            user_dn = str(sc.entries[0].distinguishedName)
            sc.unbind()
        else:
            user_dn = f"{username}@{domain}" if domain and "@" not in username else \
                cfg["user_dn_template"].format(username=username)
        uc = Connection(server, user=user_dn, password=password, auto_bind=True)
        uc.unbind()
        return True
    except Exception as ex:
        log.info("LDAP auth fail '%s': %s", username, ex)
        return False


def ldap_group_members(cfg: dict, group_dn: str) -> list[dict]:
    """Membres directs d'un groupe AD -> [{username, display_name, email, site}]."""
    from ldap3 import SUBTREE
    out: list[dict] = []
    if not group_dn:
        return out
    try:
        conn = _service_conn(cfg)
        if not conn:
            return out
        flt = f"(&(objectClass=user)(memberOf={_ldap_escape(group_dn)}))"
        attrs = ["sAMAccountName", "displayName", "mail", cfg["site_attr"]]
        conn.search(cfg["base_dn"], flt, SUBTREE, attributes=attrs)
        for e in conn.entries:
            uname = _attr(e, "sAMAccountName")
            if not uname:
                continue
            site_vals = e[cfg["site_attr"]].values if cfg["site_attr"] in e else []
            out.append({
                "username": uname,
                "display_name": _attr(e, "displayName") or uname,
                "email": _attr(e, "mail"),
                "site": site_vals[0] if site_vals else None,
            })
        conn.unbind()
    except Exception as ex:
        log.warning("LDAP members fail '%s': %s", group_dn, ex)
    return out


def ldap_all_groups(cfg: dict) -> list[dict]:
    """Liste tous les groupes AD (pour synchronisation côté admin)."""
    from ldap3 import SUBTREE
    out: list[dict] = []
    try:
        conn = _service_conn(cfg)
        if not conn:
            return out
        conn.search(cfg["base_dn"], cfg["group_filter"], SUBTREE,
                    attributes=["cn", "distinguishedName", "description"])
        for e in conn.entries:
            name = _attr(e, "cn") or ""
            if not name:
                continue
            out.append({
                "name": name,
                "dn": _attr(e, "distinguishedName") or "",
                "description": _attr(e, "description"),
            })
        conn.unbind()
    except Exception as ex:
        log.warning("LDAP group sync fail: %s", ex)
    return out


# --- groupes ------------------------------------------------------------------
def ensure_everyone_group(db: Session) -> Group:
    g = db.exec(select(Group).where(Group.is_everyone == True)).first()  # noqa: E712
    if g:
        return g
    g = db.exec(select(Group).where(Group.name == EVERYONE_GROUP)).first()
    if g:
        g.is_everyone, g.source = True, "local"
    else:
        g = Group(name=EVERYONE_GROUP, description="Tous les utilisateurs connectés",
                  source="local", is_everyone=True)
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def sync_user_groups(db: Session, user: User, group_names: list[str],
                     ad_site: Optional[str] = None) -> None:
    """Remplace les appartenances AD de l'utilisateur par l'état fourni (crée les
    groupes AD manquants). Les groupes locaux, gérés dans le portail, ne sont pas touchés ;
    un nom AD qui correspond déjà à un groupe local est ignoré.

    Site : celui d'un groupe AD de l'utilisateur en priorité, sinon l'attribut AD
    (`ad_site`). Sans l'un ni l'autre, le site en place est conservé."""
    ids: list[int] = []
    group_site_id = None
    for name in sorted({n for n in group_names if n}):
        g = db.exec(select(Group).where(Group.name == name)).first()
        if g and g.source != "ad":
            continue
        if not g:
            g = Group(name=name, source="ad")
            db.add(g)
            db.commit()
            db.refresh(g)
        ids.append(g.id)
        if g.site_id and not group_site_id:
            group_site_id = g.site_id
    ad_group_ids = {g.id for g in db.exec(select(Group).where(Group.source == "ad")).all()}
    for m in db.exec(select(GroupMembership).where(GroupMembership.user_id == user.id)).all():
        if m.group_id in ad_group_ids:
            db.delete(m)
    for gid in ids:
        db.add(GroupMembership(user_id=user.id, group_id=gid))
    site_id = group_site_id or map_site(db, ad_site)
    if site_id and user.site_id != site_id:
        user.site_id = site_id
        db.add(user)
    db.commit()


def apply_ad_identity(db: Session, user: User, info: dict) -> None:
    """Recopie nom et mail depuis l'AD. Une valeur absente côté AD ne vide pas celle du portail."""
    changed = False
    for field, key in (("email", "email"), ("full_name", "display_name")):
        new = info.get(key)
        if new and new != getattr(user, field):
            setattr(user, field, new)
            changed = True
    if changed:
        db.add(user)
        db.commit()


DIRECTORY_SOURCES = ("ldap", "sso")


def refresh_user_from_ad(db: Session, user: User) -> bool:
    """Relit dans l'AD les groupes et le site d'un compte AD/SSO et les applique.
    False (rien n'est touché) si l'AD est désactivé, injoignable ou ne connaît pas
    le compte : une panne LDAP ne doit pas vider les appartenances."""
    if user.auth_source not in DIRECTORY_SOURCES:
        return False
    cfg = get_ldap_config(db)
    if not (cfg["enabled"] and cfg["server"]):
        return False
    info = ldap_fetch_user(cfg, user.username)
    if not info:
        return False
    apply_ad_identity(db, user, info)
    sync_user_groups(db, user, info.get("groups", []), ad_site=info.get("site"))
    return True


# --- authentification combinée ----------------------------------------------
def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    username = (username or "").strip()
    if not username or not password:
        return None
    user = db.exec(select(User).where(User.username == username)).first()
    if user and not user.is_active:
        return None
    # 1) local
    if user and verify_password(password, user.password_hash):
        user.last_login = utcnow()
        db.add(user)
        db.commit()
        return user
    # 2) LDAP / AD — jamais pour un compte local existant (pas de prise de contrôle par homonyme)
    if user and user.auth_source == "local":
        return None
    cfg = get_ldap_config(db)
    if cfg["enabled"] and cfg["server"] and ldap_authenticate(cfg, username, password):
        info = ldap_fetch_user(cfg, username) or {}
        if not user:
            user = User(username=username, full_name=info.get("display_name") or username,
                        email=info.get("email"), role="USER", auth_source="ldap", is_active=True)
            db.add(user)
            db.commit()
            db.refresh(user)
        user.last_login = utcnow()
        db.add(user)
        db.commit()
        if info:
            apply_ad_identity(db, user, info)
            sync_user_groups(db, user, info.get("groups", []), ad_site=info.get("site"))
        return user
    return None


# --- dépendances FastAPI -----------------------------------------------------
def current_user(token: Optional[str] = Depends(oauth2),
                 db: Session = Depends(get_session)) -> User:
    cred_exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                             detail="Authentification requise",
                             headers={"WWW-Authenticate": "Bearer"})
    if not token:
        raise cred_exc
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        username = payload.get("sub")
        if not username:
            raise cred_exc
    except jwt.PyJWTError:
        raise cred_exc
    user = db.exec(select(User).where(User.username == username)).first()
    if not user or not user.is_active:
        raise cred_exc
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs")
    return user
