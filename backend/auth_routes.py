"""Routes d'authentification, SSO OIDC, réglages, utilisateurs et groupes."""
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import (DashboardAccess, Group, GroupMembership, Site, User,
                    UserAppOverride, UserDashboardOverride)
import usage
from rights import user_group_ids
from security import (FONTS, MIN_PASSWORD_LENGTH, SEARCH_ENGINES, _service_conn, apply_ad_identity,
                      authenticate_user, clean_color, clean_image_url, create_token,
                      current_user, get_branding_config, get_ldap_config, get_sso_config,
                      hash_password, ldap_all_groups, ldap_fetch_user, ldap_group_members,
                      map_site, require_admin, set_setting, sync_user_groups)

log = logging.getLogger("myapps.auth")
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
admin_router = APIRouter(tags=["admin"])


# ============================================================================
# Authentification (local / LDAP) & SSO OIDC
# ============================================================================
def user_payload(db: Session, user: User) -> dict:
    site = db.get(Site, user.site_id) if user.site_id else None
    gnames = []
    for gid in user_group_ids(db, user):
        g = db.get(Group, gid)
        if g:
            gnames.append(g.name)
    return {
        "id": user.id, "username": user.username, "full_name": user.full_name,
        "email": user.email, "role": user.role, "is_admin": user.role == "ADMIN",
        "auth_source": user.auth_source,
        "prefs": usage.get_prefs(db, user.id),
        "site": {"slug": site.slug, "name": site.name} if site else None,
        "groups": gnames,
    }


@auth_router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), remember: bool = Form(False),
          db: Session = Depends(get_session)):
    user = authenticate_user(db, form.username, form.password)
    if not user:
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    token = create_token(user.username, remember=remember)
    return {"access_token": token, "token_type": "bearer", "user": user_payload(db, user)}


@auth_router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_session)):
    return user_payload(db, user)


@auth_router.get("/branding")
def branding(db: Session = Depends(get_session)):
    """Apparence globale (public : nom, logo, couleur, fonds, moteur de recherche)."""
    return get_branding_config(db)


# ---- SSO OIDC ----------------------------------------------------------------
@auth_router.get("/sso/config")
def sso_public_config(db: Session = Depends(get_session)):
    cfg = get_sso_config(db)
    return {"enabled": cfg["enabled"] and bool(cfg["authority"] and cfg["client_id"]),
            "provider": cfg["provider"] or "SSO"}


async def _oidc_discovery(authority: str) -> dict:
    import httpx
    a = (authority or "").strip().rstrip("/")
    url = a if a.endswith("/.well-known/openid-configuration") else a + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


_SSO_STATE_COOKIE = "myapps_sso_state"


@auth_router.get("/sso/login")
async def sso_login(db: Session = Depends(get_session)):
    """Démarre le flux OIDC (authorization code). Le paramètre `state` est lié au
    navigateur par un cookie court, vérifié au retour (anti-CSRF de connexion)."""
    cfg = get_sso_config(db)
    if not cfg["enabled"]:
        return RedirectResponse("/?sso_error=disabled")
    try:
        from urllib.parse import urlencode
        disco = await _oidc_discovery(cfg["authority"])
        state = secrets.token_urlsafe(24)
        params = {"response_type": "code", "client_id": cfg["client_id"],
                  "redirect_uri": cfg["redirect_uri"], "scope": cfg["scopes"], "state": state}
        resp = RedirectResponse(disco["authorization_endpoint"] + "?" + urlencode(params))
        resp.set_cookie(_SSO_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax",
                        secure=cfg["redirect_uri"].startswith("https://"))
        return resp
    except Exception as e:
        log.warning("SSO login error: %r", e)
        return RedirectResponse("/?sso_error=config")


@auth_router.get("/sso/callback")
async def sso_callback(request: Request, code: str = "", error: str = "", state: str = "",
                       db: Session = Depends(get_session)):
    import httpx
    cfg = get_sso_config(db)
    if not cfg["enabled"]:
        return RedirectResponse("/?sso_error=disabled")
    expected = request.cookies.get(_SSO_STATE_COOKIE) or ""
    if error or not code or not state or not secrets.compare_digest(state, expected):
        return RedirectResponse("/?sso_error=config")
    try:
        disco = await _oidc_discovery(cfg["authority"])
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
            tok = await c.post(disco["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": cfg["redirect_uri"],
                "client_id": cfg["client_id"], "client_secret": cfg["client_secret"]})
            tok.raise_for_status()
            access = tok.json().get("access_token")
            ui = await c.get(disco["userinfo_endpoint"], headers={"Authorization": f"Bearer {access}"})
            ui.raise_for_status()
            claims = ui.json()
    except Exception as e:
        log.warning("SSO callback error: %r", e)
        return RedirectResponse("/?sso_error=config")

    username = claims.get("preferred_username") or claims.get("email")
    if not username:
        return RedirectResponse("/?sso_error=config")

    user = db.exec(select(User).where(User.username == username)).first()
    if not user:
        if not cfg["auto_create"]:
            return RedirectResponse("/?sso_error=noaccount")
        user = User(username=username, full_name=claims.get("name", username),
                    email=claims.get("email"), role="USER", auth_source="sso", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.is_active:
        return RedirectResponse("/?sso_error=disabled")
    elif user.auth_source == "local":
        # Un compte local homonyme ne s'ouvre jamais par le SSO.
        return RedirectResponse("/?sso_error=noaccount")

    # Groupes : claim OIDC + enrichissement AD (LDAP) si disponible
    groups = list(claims.get(cfg["groups_claim"], []) or [])
    ldap_cfg = get_ldap_config(db)
    info = {}
    if ldap_cfg["enabled"] and ldap_cfg["server"]:
        info = ldap_fetch_user(ldap_cfg, username) or {}
        groups += info.get("groups", [])
    if info:
        apply_ad_identity(db, user, info)
    if groups or info:
        sync_user_groups(db, user, groups, ad_site=info.get("site"))

    # Jeton transmis dans le fragment (#) : jamais envoyé au serveur ni journalisé.
    resp = RedirectResponse(f"/#sso_token={create_token(user.username)}")
    resp.delete_cookie(_SSO_STATE_COOKIE)
    return resp


# ============================================================================
# Réglages (admin) — apparence, LDAP, SSO
# ============================================================================
LDAP_KEYS = ["ldap_enabled", "ldap_server", "ldap_port", "ldap_use_ssl", "ldap_base_dn",
             "ldap_user_dn_template", "ldap_search_filter", "ldap_group_filter",
             "ldap_site_attr", "ldap_bind_user", "ldap_bind_password"]
SSO_KEYS = ["sso_enabled", "sso_provider", "sso_authority", "sso_client_id",
            "sso_client_secret", "sso_redirect_uri", "sso_scopes", "sso_groups_claim",
            "sso_auto_create"]
BRANDING_IMAGE_KEYS = ["logo_url", "login_background", "dashboard_background"]


def _save_branding(db: Session, b: dict) -> None:
    if "portal_name" in b:
        name = (b["portal_name"] or "").strip()[:60]
        set_setting(db, "portal_name", name or "MyApps")
    for k in BRANDING_IMAGE_KEYS:
        if k in b:
            set_setting(db, k, clean_image_url(b[k], k) or "")
    for k in ("accent_color", "base_color"):
        if k in b:
            set_setting(db, k, clean_color(b[k]) or "")
    for k in ("font_title", "font_body"):
        if k in b:
            if b[k] not in FONTS:
                raise HTTPException(400, "Police non proposée")
            set_setting(db, k, b[k])
    for k in ("show_name", "logo_plate", "animations"):
        if k in b:
            set_setting(db, k, "1" if b[k] in (True, "1", 1, "true") else "0")
    if "search_engine" in b:
        if b["search_engine"] not in SEARCH_ENGINES:
            raise HTTPException(400, "Moteur de recherche inconnu")
        set_setting(db, "search_engine", b["search_engine"])


@admin_router.get("/api/settings")
def get_settings(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    cfg = get_ldap_config(db)
    sso = get_sso_config(db)
    cfg["has_bind_password"] = bool(cfg.pop("bind_password", None))   # jamais renvoyé
    sso["has_client_secret"] = bool(sso.pop("client_secret", None))
    return {"ldap": cfg, "sso": sso, "branding": get_branding_config(db)}


@admin_router.put("/api/settings")
def put_settings(payload: dict, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    for k, v in (payload.get("ldap") or {}).items():
        key = k if k.startswith("ldap_") else f"ldap_{k}"
        if key in LDAP_KEYS:
            if key == "ldap_bind_password" and v == "":   # ne pas écraser par vide
                continue
            set_setting(db, key, v)
    for k, v in (payload.get("sso") or {}).items():
        key = k if k.startswith("sso_") else f"sso_{k}"
        if key in SSO_KEYS:
            if key == "sso_client_secret" and v == "":
                continue
            set_setting(db, key, v)
    _save_branding(db, payload.get("branding") or {})
    db.commit()
    return {"ok": True, "branding": get_branding_config(db)}


@admin_router.post("/api/settings/ldap/test")
def ldap_test(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    cfg = get_ldap_config(db)
    if not cfg["server"]:
        return {"ok": False, "detail": "Serveur LDAP non renseigné"}
    if not (cfg["bind_user"] and cfg["bind_password"]):
        return {"ok": False, "detail": "Compte de service (bind) requis"}
    try:
        conn = _service_conn(cfg)
        if conn is None:
            return {"ok": False, "detail": "Connexion impossible"}
        conn.unbind()
        return {"ok": True, "detail": "Connexion et bind réussis"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


# ============================================================================
# Groupes (locaux + AD)
# ============================================================================
def _group_out(db: Session, g: Group) -> dict:
    members = db.exec(select(GroupMembership).where(GroupMembership.group_id == g.id)).all()
    return {"id": g.id, "name": g.name, "description": g.description,
            "default_dashboard_id": g.default_dashboard_id, "site_id": g.site_id,
            "dn": g.dn, "source": g.source, "is_everyone": g.is_everyone,
            "members": len(members)}


@admin_router.get("/api/groups")
def list_groups(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    out = [_group_out(db, g) for g in db.exec(select(Group)).all()]
    return sorted(out, key=lambda x: (not x["is_everyone"], x["name"].lower()))


class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


@admin_router.post("/api/groups", status_code=201)
def create_group(p: GroupCreate, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    name = p.name.strip()
    if not name:
        raise HTTPException(400, "Nom requis")
    if db.exec(select(Group).where(Group.name == name)).first():
        raise HTTPException(400, "Un groupe porte déjà ce nom")
    g = Group(name=name, description=(p.description or "").strip() or None, source="local")
    db.add(g)
    db.commit()
    db.refresh(g)
    return _group_out(db, g)


@admin_router.post("/api/groups/sync")
def sync_groups(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    cfg = get_ldap_config(db)
    if not (cfg["enabled"] and cfg["server"]):
        raise HTTPException(400, "LDAP non configuré ou désactivé")
    count = skipped = 0
    for g in ldap_all_groups(cfg):
        row = db.exec(select(Group).where(Group.name == g["name"])).first()
        if row and row.source != "ad":
            skipped += 1                       # homonyme d'un groupe local : on n'y touche pas
            continue
        if row:
            row.dn, row.description = g["dn"], g["description"]
            db.add(row)
        else:
            db.add(Group(name=g["name"], dn=g["dn"], description=g["description"], source="ad"))
        count += 1
    db.commit()
    return {"synced": count, "skipped": skipped}


class GroupPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_dashboard_id: Optional[int] = None
    site_id: Optional[int] = None


@admin_router.patch("/api/groups/{gid}")
def patch_group(gid: int, payload: GroupPatch, db: Session = Depends(get_session),
                _: User = Depends(require_admin)):
    g = db.get(Group, gid)
    if not g:
        raise HTTPException(404, "introuvable")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data.pop("name") or "").strip()
        if g.source != "local" or g.is_everyone:
            raise HTTPException(400, "Seul un groupe local peut être renommé")
        if not name:
            raise HTTPException(400, "Nom requis")
        clash = db.exec(select(Group).where(Group.name == name, Group.id != gid)).first()
        if clash:
            raise HTTPException(400, "Un groupe porte déjà ce nom")
        g.name = name
    for k, v in data.items():
        setattr(g, k, v)
    db.add(g)
    if data.get("default_dashboard_id") is not None:
        dash_id = data["default_dashboard_id"]
        exists = db.exec(select(DashboardAccess).where(
            DashboardAccess.group_id == g.id, DashboardAccess.dashboard_id == dash_id)).first()
        if not exists:
            db.add(DashboardAccess(group_id=g.id, dashboard_id=dash_id))
    db.commit()
    return {"ok": True}


def _drop_group(db: Session, g: Group) -> None:
    from models import AppAccess
    for m in db.exec(select(GroupMembership).where(GroupMembership.group_id == g.id)).all():
        db.delete(m)
    for a in db.exec(select(AppAccess).where(AppAccess.group_id == g.id)).all():
        db.delete(a)
    for a in db.exec(select(DashboardAccess).where(DashboardAccess.group_id == g.id)).all():
        db.delete(a)
    db.delete(g)


@admin_router.delete("/api/groups/{gid}", status_code=204)
def delete_group(gid: int, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    g = db.get(Group, gid)
    if not g:
        raise HTTPException(404, "introuvable")
    if g.is_everyone:
        raise HTTPException(400, "Le groupe « Tout le monde » ne peut pas être supprimé")
    _drop_group(db, g)
    db.commit()


@admin_router.delete("/api/groups")
def delete_ad_groups(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    """Supprime tous les groupes issus de l'AD (les groupes locaux sont conservés)."""
    groups = db.exec(select(Group).where(Group.source == "ad")).all()
    for g in groups:
        _drop_group(db, g)
    db.commit()
    return {"deleted": len(groups)}


@admin_router.post("/api/groups/{gid}/import-users")
def import_group_users(gid: int, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    """Crée les comptes des membres d'un groupe AD."""
    g = db.get(Group, gid)
    if not g:
        raise HTTPException(404, "introuvable")
    cfg = get_ldap_config(db)
    if not (cfg["enabled"] and cfg["server"]):
        raise HTTPException(400, "LDAP non configuré ou désactivé")
    if not g.dn:
        raise HTTPException(400, "Groupe sans DN — lancez d'abord la synchronisation AD")

    members = ldap_group_members(cfg, g.dn)
    created = linked = 0
    for m in members:
        user = db.exec(select(User).where(User.username == m["username"])).first()
        if user and user.auth_source == "local":
            continue                           # homonyme local : pas de rattachement AD
        if not user:
            user = User(username=m["username"], full_name=m["display_name"] or m["username"],
                        email=m["email"], role="USER", auth_source="ldap", is_active=True)
            db.add(user)
            db.commit()
            db.refresh(user)
            created += 1
        if g.site_id:
            user.site_id = g.site_id
        elif m.get("site"):
            sid = map_site(db, m["site"])
            if sid:
                user.site_id = sid
        db.add(user)
        db.commit()
        exists = db.exec(select(GroupMembership).where(
            GroupMembership.user_id == user.id, GroupMembership.group_id == g.id)).first()
        if not exists:
            db.add(GroupMembership(user_id=user.id, group_id=g.id))
            linked += 1
    db.commit()
    return {"created": created, "linked": linked, "total": len(members)}


# ============================================================================
# Utilisateurs (admin) — rôle, site, groupes locaux, exceptions
# ============================================================================
def _local_group_ids(db: Session, user: User) -> list[int]:
    local = {g.id for g in db.exec(select(Group).where(Group.source == "local",
                                                       Group.is_everyone == False)).all()}  # noqa: E712
    return [gid for gid in user_group_ids(db, user) if gid in local]


@admin_router.get("/api/users")
def list_users(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    out = []
    for u in db.exec(select(User)).all():
        site = db.get(Site, u.site_id) if u.site_id else None
        app_ov = db.exec(select(UserAppOverride).where(UserAppOverride.user_id == u.id)).all()
        dash_ov = db.exec(select(UserDashboardOverride).where(UserDashboardOverride.user_id == u.id)).all()
        out.append({
            "id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email,
            "role": u.role, "auth_source": u.auth_source, "is_active": u.is_active,
            "site_id": u.site_id, "site": site.name if site else None,
            "groups": len(user_group_ids(db, u)),
            "local_group_ids": _local_group_ids(db, u),
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "app_overrides": [{"id": o.app_id, "effect": o.effect} for o in app_ov],
            "dashboard_overrides": [{"id": o.dashboard_id, "effect": o.effect} for o in dash_ov],
        })
    return sorted(out, key=lambda x: (x["full_name"] or x["username"]).lower())


class OverrideItem(BaseModel):
    id: int
    effect: str


class UserPatch(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    site_id: Optional[int] = None
    local_group_ids: Optional[list[int]] = None
    app_overrides: Optional[list[OverrideItem]] = None
    dashboard_overrides: Optional[list[OverrideItem]] = None


class UserCreate(BaseModel):
    username: str
    full_name: str
    email: Optional[str] = None
    role: str = "USER"
    site_id: Optional[int] = None
    password: str
    local_group_ids: list[int] = []


def _check_role(role: str) -> None:
    if role not in ("ADMIN", "USER"):
        raise HTTPException(400, "Rôle invalide")


def _check_password(pw: str) -> None:
    if len(pw or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Mot de passe trop court ({MIN_PASSWORD_LENGTH} caractères minimum)")


def _other_active_admins(db: Session, uid: int) -> int:
    return len([u for u in db.exec(select(User).where(User.role == "ADMIN", User.is_active == True)).all()  # noqa: E712
                if u.id != uid])


def _set_local_groups(db: Session, uid: int, ids: list[int]) -> None:
    local = {g.id for g in db.exec(select(Group).where(Group.source == "local",
                                                       Group.is_everyone == False)).all()}  # noqa: E712
    for m in db.exec(select(GroupMembership).where(GroupMembership.user_id == uid)).all():
        if m.group_id in local:
            db.delete(m)
    for gid in {i for i in ids if i in local}:
        db.add(GroupMembership(user_id=uid, group_id=gid))


@admin_router.post("/api/users")
def create_user(payload: UserCreate, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    username = payload.username.strip()
    if not username or any(c.isspace() for c in username):
        raise HTTPException(400, "Identifiant invalide (sans espace)")
    if db.exec(select(User).where(User.username == username)).first():
        raise HTTPException(400, "Nom d'utilisateur déjà pris")
    _check_role(payload.role)
    _check_password(payload.password)
    u = User(username=username, full_name=payload.full_name.strip() or username,
             email=(payload.email or "").strip() or None, role=payload.role,
             site_id=payload.site_id, auth_source="local",
             password_hash=hash_password(payload.password), is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    _set_local_groups(db, u.id, payload.local_group_ids)
    db.commit()
    return {"id": u.id}


@admin_router.delete("/api/users/{uid}", status_code=204)
def delete_user(uid: int, db: Session = Depends(get_session), me: User = Depends(require_admin)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "introuvable")
    if u.id == me.id:
        raise HTTPException(400, "Vous ne pouvez pas supprimer votre propre compte")
    if u.role == "ADMIN" and _other_active_admins(db, uid) == 0:
        raise HTTPException(400, "Impossible de supprimer le dernier administrateur")
    for o in db.exec(select(UserAppOverride).where(UserAppOverride.user_id == uid)).all():
        db.delete(o)
    for o in db.exec(select(UserDashboardOverride).where(UserDashboardOverride.user_id == uid)).all():
        db.delete(o)
    for m in db.exec(select(GroupMembership).where(GroupMembership.user_id == uid)).all():
        db.delete(m)
    usage.forget_user(db, uid)
    db.delete(u)
    db.commit()


@admin_router.patch("/api/users/{uid}")
def patch_user(uid: int, payload: UserPatch, db: Session = Depends(get_session),
               _: User = Depends(require_admin)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "introuvable")
    losing_admin = u.role == "ADMIN" and (
        (payload.role is not None and payload.role != "ADMIN") or payload.is_active is False)
    if losing_admin and _other_active_admins(db, uid) == 0:
        raise HTTPException(400, "Il doit rester au moins un administrateur actif")
    if payload.role is not None:
        _check_role(payload.role)
        u.role = payload.role
    if payload.is_active is not None:
        u.is_active = payload.is_active
    if payload.full_name is not None:
        u.full_name = payload.full_name
    if payload.email is not None:
        u.email = payload.email or None
    if payload.password:
        if u.auth_source != "local":
            raise HTTPException(400, "Le mot de passe d'un compte AD/SSO se gère dans l'annuaire")
        _check_password(payload.password)
        u.password_hash = hash_password(payload.password)
    if "site_id" in payload.model_dump(exclude_unset=True):
        u.site_id = payload.site_id
    db.add(u)

    if payload.local_group_ids is not None:
        _set_local_groups(db, uid, payload.local_group_ids)
    if payload.app_overrides is not None:
        for o in db.exec(select(UserAppOverride).where(UserAppOverride.user_id == uid)).all():
            db.delete(o)
        for o in payload.app_overrides:
            if o.effect in ("ALLOW", "DENY"):
                db.add(UserAppOverride(user_id=uid, app_id=o.id, effect=o.effect))
    if payload.dashboard_overrides is not None:
        for o in db.exec(select(UserDashboardOverride).where(UserDashboardOverride.user_id == uid)).all():
            db.delete(o)
        for o in payload.dashboard_overrides:
            if o.effect in ("ALLOW", "DENY"):
                db.add(UserDashboardOverride(user_id=uid, dashboard_id=o.id, effect=o.effect))
    db.commit()
    return {"ok": True}
