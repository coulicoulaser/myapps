"""Moteur de droits.

Visible si un groupe de l'utilisateur a accès (« Tout le monde » inclus d'office).
Les exceptions par utilisateur priment : DENY > ALLOW > règle de groupe. ADMIN voit tout.
"""
from typing import Optional

from sqlmodel import Session, select

from models import (App, AppAccess, AppGroup, AppGroupMembership, Dashboard,
                    DashboardAccess, DashboardAppGroup, Group, GroupMembership,
                    User, UserAppOverride, UserDashboardOverride)


def user_group_ids(db: Session, user: User) -> list[int]:
    """Groupes explicites de l'utilisateur."""
    return [m.group_id for m in
            db.exec(select(GroupMembership).where(GroupMembership.user_id == user.id)).all()]


def effective_group_ids(db: Session, user: User) -> list[int]:
    """Groupes explicites + « Tout le monde »."""
    ids = user_group_ids(db, user)
    for g in db.exec(select(Group).where(Group.is_everyone == True)).all():  # noqa: E712
        if g.id not in ids:
            ids.append(g.id)
    return ids


def _resolve(base: set[int], allow: set[int], deny: set[int]) -> set[int]:
    return (base | allow) - deny


def allowed_app_ids(db: Session, user: User) -> set[int]:
    if user.role == "ADMIN":
        return {a.id for a in db.exec(select(App).where(App.is_active == True)).all()}  # noqa: E712
    gids = effective_group_ids(db, user)
    base = {a.app_id for a in db.exec(select(AppAccess).where(AppAccess.group_id.in_(gids))).all()} if gids else set()
    ov = db.exec(select(UserAppOverride).where(UserAppOverride.user_id == user.id)).all()
    allow = {o.app_id for o in ov if o.effect == "ALLOW"}
    deny = {o.app_id for o in ov if o.effect == "DENY"}
    return _resolve(base, allow, deny)


def allowed_dashboard_ids(db: Session, user: User) -> set[int]:
    if user.role == "ADMIN":
        return {d.id for d in db.exec(select(Dashboard)).all()}
    gids = effective_group_ids(db, user)
    base = {d.dashboard_id for d in
            db.exec(select(DashboardAccess).where(DashboardAccess.group_id.in_(gids))).all()} if gids else set()
    ov = db.exec(select(UserDashboardOverride).where(UserDashboardOverride.user_id == user.id)).all()
    allow = {o.dashboard_id for o in ov if o.effect == "ALLOW"}
    deny = {o.dashboard_id for o in ov if o.effect == "DENY"}
    return _resolve(base, allow, deny)


def visible_dashboards(db: Session, user: User) -> list[Dashboard]:
    ids = allowed_dashboard_ids(db, user)
    rows = [d for d in db.exec(select(Dashboard)).all() if d.id in ids]
    return sorted(rows, key=lambda d: (d.sort_order, d.name))


def default_dashboard_slug(db: Session, user: User) -> Optional[str]:
    allowed = allowed_dashboard_ids(db, user)
    # 1) défaut d'un des groupes explicites de l'utilisateur, puis de « Tout le monde »
    for gid in effective_group_ids(db, user):
        g = db.get(Group, gid)
        if g and g.default_dashboard_id and g.default_dashboard_id in allowed:
            d = db.get(Dashboard, g.default_dashboard_id)
            if d:
                return d.slug
    # 2) défaut global
    gd = db.exec(select(Dashboard).where(Dashboard.is_default == True)).first()  # noqa: E712
    if gd and gd.id in allowed:
        return gd.slug
    # 3) premier visible
    vis = visible_dashboards(db, user)
    return vis[0].slug if vis else None


def _app_payload(a: App) -> dict:
    return {"id": a.id, "name": a.name, "url": a.url, "image_url": a.image_url,
            "tooltip": a.tooltip, "open_new_tab": a.open_new_tab}


def dashboard_payload(db: Session, user: User, slug: str) -> Optional[dict]:
    d = db.exec(select(Dashboard).where(Dashboard.slug == slug)).first()
    if not d:
        return None
    if d.id not in allowed_dashboard_ids(db, user):
        return None

    allowed_apps = allowed_app_ids(db, user)
    groups_out = []
    links = sorted(db.exec(select(DashboardAppGroup).where(DashboardAppGroup.dashboard_id == d.id)).all(),
                   key=lambda x: x.sort_order)
    for link in links:
        ag = db.get(AppGroup, link.app_group_id)
        if not ag:
            continue
        mems = sorted(db.exec(select(AppGroupMembership).where(AppGroupMembership.app_group_id == ag.id)).all(),
                      key=lambda x: x.sort_order)
        apps = []
        for m in mems:
            a = db.get(App, m.app_id)
            if a and a.is_active and a.id in allowed_apps:
                apps.append(_app_payload(a))
        if apps or user.role == "ADMIN":
            groups_out.append({"id": ag.id, "name": ag.name, "icon": ag.icon,
                               "color": ag.color, "apps": apps})

    return {"id": d.id, "name": d.name, "slug": d.slug,
            "background_url": d.background_url, "groups": groups_out}


def visible_apps_flat(db: Session, user: User) -> list[dict]:
    """Applications proposées par la recherche : exactement celles que l'utilisateur voit
    sur ses dashboards. Le droit sur l'application ne suffit pas : rangée seulement dans
    une section d'un dashboard qu'il ne voit pas, ou dans aucune section, elle n'apparaît
    pas (sinon la recherche dévoilerait son nom et son adresse)."""
    dash_ids = allowed_dashboard_ids(db, user)
    section_ids = {l.app_group_id for l in db.exec(select(DashboardAppGroup).where(
        DashboardAppGroup.dashboard_id.in_(dash_ids))).all()} if dash_ids else set()
    section_ids &= {g.id for g in db.exec(select(AppGroup)).all()}
    placed = {m.app_id for m in db.exec(select(AppGroupMembership).where(
        AppGroupMembership.app_group_id.in_(section_ids))).all()} if section_ids else set()
    ids = allowed_app_ids(db, user) & placed
    rows = [a for a in db.exec(select(App).where(App.is_active == True)).all() if a.id in ids]  # noqa: E712
    return [_app_payload(a) for a in sorted(rows, key=lambda a: a.name.lower())]
