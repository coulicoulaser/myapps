"""Routes du portail : données utilisateur (dashboards, apps, météo), sites et CRUD admin."""
import os
import re
import time
import unicodedata
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from config import UPLOAD_DIR
from database import get_session
from models import (App, AppAccess, AppGroup, AppGroupMembership, Dashboard,
                    DashboardAccess, DashboardAppGroup, Group, GroupMembership, Site, User)
from rights import (dashboard_payload, default_dashboard_slug, visible_apps_flat,
                    visible_dashboards)
from security import (clean_color, clean_image_url, clean_link_url, current_user,
                      require_admin)

portal_router = APIRouter(tags=["portail"])
admin_crud = APIRouter(tags=["admin-crud"])


# ============================================================================
# Données utilisateur
# ============================================================================
@portal_router.get("/api/me/dashboards")
def my_dashboards(db: Session = Depends(get_session), user: User = Depends(current_user)):
    return [{"slug": d.slug, "name": d.name} for d in visible_dashboards(db, user)]


@portal_router.get("/api/me/default-dashboard")
def my_default(db: Session = Depends(get_session), user: User = Depends(current_user)):
    return {"slug": default_dashboard_slug(db, user)}


@portal_router.get("/api/me/dashboard/{slug}")
def my_dashboard(slug: str, db: Session = Depends(get_session), user: User = Depends(current_user)):
    payload = dashboard_payload(db, user, slug)
    if not payload:
        raise HTTPException(404, "Dashboard introuvable ou non autorisé")
    return payload


@portal_router.get("/api/me/apps")
def my_apps(db: Session = Depends(get_session), user: User = Depends(current_user)):
    return visible_apps_flat(db, user)


# ---- Météo (Open-Meteo, sans clé) ------------------------------------------
_WMO = {
    0: ("Ciel dégagé", "☀️"), 1: ("Plutôt dégagé", "🌤️"), 2: ("Partiellement nuageux", "⛅"),
    3: ("Couvert", "☁️"), 45: ("Brouillard", "🌫️"), 48: ("Brouillard givrant", "🌫️"),
    51: ("Bruine légère", "🌦️"), 53: ("Bruine", "🌦️"), 55: ("Bruine dense", "🌧️"),
    61: ("Pluie faible", "🌦️"), 63: ("Pluie", "🌧️"), 65: ("Forte pluie", "🌧️"),
    71: ("Neige faible", "🌨️"), 73: ("Neige", "❄️"), 75: ("Forte neige", "❄️"),
    80: ("Averses", "🌦️"), 81: ("Averses", "🌧️"), 82: ("Fortes averses", "⛈️"),
    95: ("Orage", "⛈️"), 96: ("Orage grêle", "⛈️"), 99: ("Orage grêle", "⛈️"),
}
_weather_cache: dict[str, tuple[float, dict]] = {}


@portal_router.get("/api/me/weather")
async def weather(site: Optional[str] = None, db: Session = Depends(get_session),
                  user: User = Depends(current_user)):
    import httpx

    s = db.exec(select(Site).where(Site.slug == site)).first() if site else None
    if not s and user.site_id:
        s = db.get(Site, user.site_id)
    if not s:
        # repli : site défini sur l'un des groupes de l'utilisateur
        gids = [m.group_id for m in
                db.exec(select(GroupMembership).where(GroupMembership.user_id == user.id)).all()]
        for gid in gids:
            g = db.get(Group, gid)
            if g and g.site_id:
                s = db.get(Site, g.site_id)
                break
    if not s:
        s = db.exec(select(Site)).first()
    if not s:
        raise HTTPException(404, "Aucun site configuré")

    cached = _weather_cache.get(s.slug)
    if cached and time.time() - cached[0] < 1800:
        return cached[1]

    url = (f"https://api.open-meteo.com/v1/forecast?latitude={s.latitude}"
           f"&longitude={s.longitude}&current=temperature_2m,apparent_temperature,"
           f"is_day,weather_code&timezone=auto")
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(url)
            r.raise_for_status()
            cur = r.json()["current"]
    except Exception:
        raise HTTPException(502, "Météo indisponible")

    label, icon = _WMO.get(cur["weather_code"], ("—", "🌡️"))
    out = {"slug": s.slug, "site": s.name, "temperature": round(cur["temperature_2m"]),
           "apparent": round(cur["apparent_temperature"]), "label": label, "icon": icon,
           "is_day": cur["is_day"] == 1}
    _weather_cache[s.slug] = (time.time(), out)
    return out


@portal_router.get("/api/sites")
def list_sites(db: Session = Depends(get_session), user: User = Depends(current_user)):
    rows = sorted(db.exec(select(Site)).all(), key=lambda s: s.name.lower())
    return [{"id": s.id, "name": s.name, "slug": s.slug,
             "latitude": s.latitude, "longitude": s.longitude} for s in rows]


# ============================================================================
# Sites (admin) — lieux affichés dans le widget météo
# ============================================================================
def slugify(value: str) -> str:
    v = unicodedata.normalize("NFD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", v.lower()).strip("-")


class SiteIn(BaseModel):
    name: str
    latitude: float
    longitude: float


def _check_site(db: Session, p: SiteIn, sid: Optional[int] = None) -> tuple[str, str]:
    name = p.name.strip()
    slug = slugify(name)
    if not name or not slug:
        raise HTTPException(400, "Nom requis")
    if not (-90 <= p.latitude <= 90 and -180 <= p.longitude <= 180):
        raise HTTPException(400, "Coordonnées invalides")
    for other in db.exec(select(Site)).all():
        if other.id != sid and (other.slug == slug or other.name.lower() == name.lower()):
            raise HTTPException(400, "Un site porte déjà ce nom")
    return name, slug


@admin_crud.post("/api/sites", status_code=201)
def create_site(p: SiteIn, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    name, slug = _check_site(db, p)
    s = Site(name=name, slug=slug, latitude=p.latitude, longitude=p.longitude)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "name": s.name, "slug": s.slug}


@admin_crud.patch("/api/sites/{sid}")
def update_site(sid: int, p: SiteIn, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    s = db.get(Site, sid)
    if not s:
        raise HTTPException(404, "introuvable")
    s.name, s.slug = _check_site(db, p, sid)
    s.latitude, s.longitude = p.latitude, p.longitude
    db.add(s)
    db.commit()
    _weather_cache.clear()
    return {"ok": True}


@admin_crud.delete("/api/sites/{sid}", status_code=204)
def delete_site(sid: int, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    s = db.get(Site, sid)
    if not s:
        raise HTTPException(404, "introuvable")
    for u in db.exec(select(User).where(User.site_id == sid)).all():
        u.site_id = None
        db.add(u)
    for g in db.exec(select(Group).where(Group.site_id == sid)).all():
        g.site_id = None
        db.add(g)
    db.delete(s)
    db.commit()
    _weather_cache.clear()


@admin_crud.get("/api/geocode")
async def geocode(q: str = "", _: User = Depends(require_admin)):
    """Recherche de ville (Open-Meteo, sans clé) pour renseigner un site."""
    import httpx
    q = q.strip()
    if len(q) < 2:
        return []
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                            params={"name": q, "count": 6, "language": "fr", "format": "json"})
            r.raise_for_status()
            rows = r.json().get("results") or []
    except Exception:
        raise HTTPException(502, "Recherche de ville indisponible (accès Internet requis)")
    return [{"name": x.get("name"), "admin": x.get("admin1"), "country": x.get("country"),
             "latitude": x.get("latitude"), "longitude": x.get("longitude")} for x in rows]


# ============================================================================
# Récupération auto du logo (admin)
# ============================================================================
@admin_crud.get("/api/logo")
async def fetch_logo(url: str = "", _: User = Depends(require_admin)):
    """Cherche le logo de l'application à son adresse (voir favicon.py) et l'enregistre
    dans les envois : le portail ne dépend pas d'un service tiers pour l'afficher."""
    import hashlib

    import favicon

    if not favicon.normalize(url):
        raise HTTPException(400, "Indiquez d'abord l'adresse (http ou https) de l'application")
    icon = await favicon.find_logo(url)
    if not icon:
        return {"logo": "", "detail": "Aucun logo trouvé à cette adresse : envoyez une image"}
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Nommé d'après le contenu : relancer la recherche ne duplique pas le fichier.
    name = "logo-" + hashlib.sha256(icon.data).hexdigest()[:32] + favicon.EXT[icon.kind]
    path = UPLOAD_DIR / name
    if not path.exists():
        path.write_bytes(icon.data)
    return {"logo": "/uploads/" + name, "source": icon.source,
            "size": None if icon.kind == "svg" else icon.size}


# ============================================================================
# Upload d'images locales (admin) — logos & fonds de dashboard
# ============================================================================
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}
_MAX_UPLOAD = 8 * 1024 * 1024


@admin_crud.post("/api/upload")
async def upload_image(file: UploadFile = File(...), _: User = Depends(require_admin)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, "Format non supporté (png, jpg, gif, webp, svg, ico)")
    data = await file.read(_MAX_UPLOAD + 1)
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(400, "Fichier trop volumineux (max 8 Mo)")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + ext
    (UPLOAD_DIR / name).write_bytes(data)
    return {"url": "/uploads/" + name}


# ============================================================================
# Réordonnancement (admin) — drag & drop
# ============================================================================
class ReorderIn(BaseModel):
    ids: list[int]


@admin_crud.post("/api/app-groups/{gid}/reorder")
def reorder_apps(gid: int, payload: ReorderIn, db: Session = Depends(get_session),
                 _: User = Depends(require_admin)):
    """Ordre des apps (tuiles) dans un groupe."""
    for i, aid in enumerate(payload.ids):
        m = db.exec(select(AppGroupMembership).where(
            AppGroupMembership.app_group_id == gid, AppGroupMembership.app_id == aid)).first()
        if m:
            m.sort_order = i
            db.add(m)
    db.commit()
    return {"ok": True}


@admin_crud.post("/api/dashboards/{did}/reorder")
def reorder_sections(did: int, payload: ReorderIn, db: Session = Depends(get_session),
                     _: User = Depends(require_admin)):
    """Ordre des groupes d'apps (sections) dans un dashboard."""
    for i, agid in enumerate(payload.ids):
        l = db.exec(select(DashboardAppGroup).where(
            DashboardAppGroup.dashboard_id == did, DashboardAppGroup.app_group_id == agid)).first()
        if l:
            l.sort_order = i
            db.add(l)
    db.commit()
    return {"ok": True}


# ============================================================================
# CRUD Apps (admin)
# ============================================================================
def _app_full(db: Session, a: App) -> dict:
    return {
        "id": a.id, "name": a.name, "url": a.url, "image_url": a.image_url,
        "tooltip": a.tooltip, "open_new_tab": a.open_new_tab, "is_active": a.is_active,
        "sort_order": a.sort_order,
        "app_group_ids": [m.app_group_id for m in
                          db.exec(select(AppGroupMembership).where(AppGroupMembership.app_id == a.id)).all()],
        "group_ids": [x.group_id for x in
                      db.exec(select(AppAccess).where(AppAccess.app_id == a.id)).all()],
    }


class AppIn(BaseModel):
    name: str
    url: str
    image_url: Optional[str] = None
    tooltip: Optional[str] = None
    open_new_tab: bool = True
    is_active: bool = True
    sort_order: int = 0
    app_group_ids: list[int] = []
    group_ids: list[int] = []


def _check_app(p: AppIn) -> tuple[str, str, Optional[str]]:
    name = p.name.strip()
    if not name:
        raise HTTPException(400, "Nom requis")
    return name, clean_link_url(p.url), clean_image_url(p.image_url, "du logo")


@admin_crud.get("/api/apps")
def list_apps(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    rows = sorted(db.exec(select(App)).all(), key=lambda a: (a.sort_order, a.name.lower()))
    return [_app_full(db, a) for a in rows]


@admin_crud.post("/api/apps")
def create_app(payload: AppIn, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    name, url, img = _check_app(payload)
    a = App(name=name, url=url, image_url=img, tooltip=payload.tooltip,
            open_new_tab=payload.open_new_tab, is_active=payload.is_active,
            sort_order=payload.sort_order)
    db.add(a)
    db.commit()
    db.refresh(a)
    _set_app_links(db, a.id, payload.app_group_ids, payload.group_ids)
    return _app_full(db, a)


@admin_crud.patch("/api/apps/{aid}")
def update_app(aid: int, payload: AppIn, db: Session = Depends(get_session),
               _: User = Depends(require_admin)):
    a = db.get(App, aid)
    if not a:
        raise HTTPException(404, "introuvable")
    a.name, a.url, a.image_url = _check_app(payload)
    a.tooltip, a.open_new_tab = payload.tooltip, payload.open_new_tab
    a.is_active, a.sort_order = payload.is_active, payload.sort_order
    db.add(a)
    db.commit()
    _set_app_links(db, a.id, payload.app_group_ids, payload.group_ids)
    return _app_full(db, a)


@admin_crud.delete("/api/apps/{aid}", status_code=204)
def delete_app(aid: int, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    a = db.get(App, aid)
    if not a:
        raise HTTPException(404, "introuvable")
    for m in db.exec(select(AppGroupMembership).where(AppGroupMembership.app_id == aid)).all():
        db.delete(m)
    for x in db.exec(select(AppAccess).where(AppAccess.app_id == aid)).all():
        db.delete(x)
    db.delete(a)
    db.commit()


def _set_app_links(db: Session, aid: int, app_group_ids: list[int], group_ids: list[int]):
    for m in db.exec(select(AppGroupMembership).where(AppGroupMembership.app_id == aid)).all():
        db.delete(m)
    for i, gid in enumerate(app_group_ids):
        db.add(AppGroupMembership(app_id=aid, app_group_id=gid, sort_order=i))
    for x in db.exec(select(AppAccess).where(AppAccess.app_id == aid)).all():
        db.delete(x)
    for gid in group_ids:
        db.add(AppAccess(app_id=aid, group_id=gid))
    db.commit()


# ============================================================================
# CRUD Groupes d'apps (admin)
# ============================================================================
class AppGroupIn(BaseModel):
    name: str
    icon: Optional[str] = None
    color: Optional[str] = None
    sort_order: int = 0
    app_ids: list[int] = []


def _check_app_group(db: Session, p: AppGroupIn, gid: Optional[int] = None) -> str:
    name = p.name.strip()
    if not name:
        raise HTTPException(400, "Nom requis")
    clash = db.exec(select(AppGroup).where(AppGroup.name == name)).first()
    if clash and clash.id != gid:
        raise HTTPException(400, "Une section porte déjà ce nom")
    return name


@admin_crud.get("/api/app-groups")
def list_app_groups(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    out = []
    for g in sorted(db.exec(select(AppGroup)).all(), key=lambda x: (x.sort_order, x.name.lower())):
        mems = sorted(db.exec(select(AppGroupMembership).where(AppGroupMembership.app_group_id == g.id)).all(),
                      key=lambda x: x.sort_order)
        out.append({"id": g.id, "name": g.name, "icon": g.icon, "color": g.color,
                    "sort_order": g.sort_order, "app_ids": [m.app_id for m in mems]})
    return out


@admin_crud.post("/api/app-groups", status_code=201)
def create_app_group(payload: AppGroupIn, db: Session = Depends(get_session),
                     _: User = Depends(require_admin)):
    name = _check_app_group(db, payload)
    g = AppGroup(name=name, icon=payload.icon, color=clean_color(payload.color),
                 sort_order=payload.sort_order)
    db.add(g)
    db.commit()
    db.refresh(g)
    _set_group_apps(db, g.id, payload.app_ids)
    return {"id": g.id}


@admin_crud.patch("/api/app-groups/{gid}")
def update_app_group(gid: int, payload: AppGroupIn, db: Session = Depends(get_session),
                     _: User = Depends(require_admin)):
    g = db.get(AppGroup, gid)
    if not g:
        raise HTTPException(404, "introuvable")
    g.name = _check_app_group(db, payload, gid)
    g.icon, g.color, g.sort_order = payload.icon, clean_color(payload.color), payload.sort_order
    db.add(g)
    db.commit()
    _set_group_apps(db, gid, payload.app_ids)
    return {"ok": True}


@admin_crud.delete("/api/app-groups/{gid}", status_code=204)
def delete_app_group(gid: int, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    g = db.get(AppGroup, gid)
    if not g:
        raise HTTPException(404, "introuvable")
    for m in db.exec(select(AppGroupMembership).where(AppGroupMembership.app_group_id == gid)).all():
        db.delete(m)
    for l in db.exec(select(DashboardAppGroup).where(DashboardAppGroup.app_group_id == gid)).all():
        db.delete(l)
    db.delete(g)
    db.commit()


def _set_group_apps(db: Session, gid: int, app_ids: list[int]):
    for m in db.exec(select(AppGroupMembership).where(AppGroupMembership.app_group_id == gid)).all():
        db.delete(m)
    for i, aid in enumerate(app_ids):
        db.add(AppGroupMembership(app_id=aid, app_group_id=gid, sort_order=i))
    db.commit()


# ============================================================================
# CRUD Dashboards (admin)
# ============================================================================
class DashboardIn(BaseModel):
    name: str
    slug: str
    background_url: Optional[str] = None
    is_default: bool = False
    sort_order: int = 0
    app_group_ids: list[int] = []
    group_ids: list[int] = []


def _check_dashboard(db: Session, p: DashboardIn, did: Optional[int] = None):
    name = p.name.strip()
    slug = slugify(p.slug or name)
    if not name or not slug:
        raise HTTPException(400, "Nom requis")
    clash = db.exec(select(Dashboard).where(Dashboard.slug == slug)).first()
    if clash and clash.id != did:
        raise HTTPException(400, "Un dashboard utilise déjà cette adresse (slug)")
    return name, slug, clean_image_url(p.background_url, "du fond")


@admin_crud.get("/api/dashboards")
def list_dashboards(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    out = []
    for d in sorted(db.exec(select(Dashboard)).all(), key=lambda x: (x.sort_order, x.name.lower())):
        ag = sorted(db.exec(select(DashboardAppGroup).where(DashboardAppGroup.dashboard_id == d.id)).all(),
                    key=lambda x: x.sort_order)
        acc = db.exec(select(DashboardAccess).where(DashboardAccess.dashboard_id == d.id)).all()
        out.append({"id": d.id, "name": d.name, "slug": d.slug, "background_url": d.background_url,
                    "is_default": d.is_default, "sort_order": d.sort_order,
                    "app_group_ids": [x.app_group_id for x in ag],
                    "group_ids": [x.group_id for x in acc]})
    return out


@admin_crud.post("/api/dashboards", status_code=201)
def create_dashboard(payload: DashboardIn, db: Session = Depends(get_session),
                     _: User = Depends(require_admin)):
    if payload.is_default:
        for d in db.exec(select(Dashboard).where(Dashboard.is_default == True)).all():
            d.is_default = False
            db.add(d)
    name, slug, bg = _check_dashboard(db, payload)
    d = Dashboard(name=name, slug=slug, background_url=bg,
                  is_default=payload.is_default, sort_order=payload.sort_order)
    db.add(d)
    db.commit()
    db.refresh(d)
    _set_dashboard_links(db, d.id, payload.app_group_ids, payload.group_ids)
    return {"id": d.id}


@admin_crud.patch("/api/dashboards/{did}")
def update_dashboard(did: int, payload: DashboardIn, db: Session = Depends(get_session),
                     _: User = Depends(require_admin)):
    d = db.get(Dashboard, did)
    if not d:
        raise HTTPException(404, "introuvable")
    if payload.is_default:
        for other in db.exec(select(Dashboard).where(Dashboard.is_default == True)).all():
            if other.id != did:
                other.is_default = False
                db.add(other)
    d.name, d.slug, d.background_url = _check_dashboard(db, payload, did)
    d.is_default, d.sort_order = payload.is_default, payload.sort_order
    db.add(d)
    db.commit()
    _set_dashboard_links(db, did, payload.app_group_ids, payload.group_ids)
    return {"ok": True}


@admin_crud.delete("/api/dashboards/{did}", status_code=204)
def delete_dashboard(did: int, db: Session = Depends(get_session), _: User = Depends(require_admin)):
    d = db.get(Dashboard, did)
    if not d:
        raise HTTPException(404, "introuvable")
    for l in db.exec(select(DashboardAppGroup).where(DashboardAppGroup.dashboard_id == did)).all():
        db.delete(l)
    for a in db.exec(select(DashboardAccess).where(DashboardAccess.dashboard_id == did)).all():
        db.delete(a)
    for g in db.exec(select(Group).where(Group.default_dashboard_id == did)).all():
        g.default_dashboard_id = None
        db.add(g)
    db.delete(d)
    db.commit()


def _set_dashboard_links(db: Session, did: int, app_group_ids: list[int], group_ids: list[int]):
    for l in db.exec(select(DashboardAppGroup).where(DashboardAppGroup.dashboard_id == did)).all():
        db.delete(l)
    for i, agid in enumerate(app_group_ids):
        db.add(DashboardAppGroup(dashboard_id=did, app_group_id=agid, sort_order=i))
    for a in db.exec(select(DashboardAccess).where(DashboardAccess.dashboard_id == did)).all():
        db.delete(a)
    for gid in group_ids:
        db.add(DashboardAccess(dashboard_id=did, group_id=gid))
    db.commit()
