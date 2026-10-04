"""Modèle de données — portail d'applications MyApps.

Identité   : Site, User, Group, GroupMembership.
Contenu    : App, AppGroup (section réutilisable), Dashboard.
Liaisons   : AppGroupMembership, DashboardAppGroup.
Droits     : AppAccess / DashboardAccess (par groupe),
             UserAppOverride / UserDashboardOverride (exceptions par user, DENY > ALLOW).
Config     : AppSetting (clé/valeur : apparence, LDAP, SSO, état de l'assistant).
"""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

# Rôles applicatifs : ADMIN gère le portail, USER consulte.
ROLES = ("ADMIN", "USER")
EFFECTS = ("ALLOW", "DENY")
EVERYONE_GROUP = "Tout le monde"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Identité ----------------------------------------------------------------
class Site(SQLModel, table=True):
    """Lieu (pour la météo de la barre du haut)."""
    __tablename__ = "sites"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)     # "Lyon"
    slug: str = Field(index=True, unique=True)      # "lyon"
    latitude: float = 0.0
    longitude: float = 0.0


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str = ""
    email: Optional[str] = None
    role: str = Field(default="USER", index=True)
    password_hash: Optional[str] = None             # None = compte AD/SSO pur
    auth_source: str = "local"                       # local | ldap | sso
    is_active: bool = True
    site_id: Optional[int] = Field(default=None, foreign_key="sites.id")
    last_login: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class Group(SQLModel, table=True):
    """Groupe d'utilisateurs : local (créé dans le portail) ou issu de l'annuaire AD.

    Le groupe « Tout le monde » (is_everyone) contient implicitement tous les comptes :
    donner un accès à ce groupe rend l'élément visible par tout utilisateur connecté."""
    __tablename__ = "groups"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)       # CN du groupe AD, ou nom libre
    dn: Optional[str] = None
    description: Optional[str] = None
    source: str = "local"                             # local | ad
    is_everyone: bool = False
    default_dashboard_id: Optional[int] = Field(default=None, foreign_key="dashboards.id")
    site_id: Optional[int] = Field(default=None, foreign_key="sites.id")  # site du groupe (météo)
    created_at: datetime = Field(default_factory=utcnow)


class GroupMembership(SQLModel, table=True):
    __tablename__ = "group_memberships"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    group_id: int = Field(foreign_key="groups.id", index=True)


# --- Contenu -----------------------------------------------------------------
class App(SQLModel, table=True):
    __tablename__ = "apps"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    url: str
    image_url: Optional[str] = None                  # logo (auto ou saisi)
    tooltip: Optional[str] = None                     # texte au survol
    open_new_tab: bool = True
    is_active: bool = True
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class AppGroup(SQLModel, table=True):
    """Section d'applications réutilisable (affichée sur un ou plusieurs dashboards)."""
    __tablename__ = "app_groups"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    icon: Optional[str] = None
    color: Optional[str] = None                       # None = couleur d'accent du portail
    sort_order: int = 0


class AppGroupMembership(SQLModel, table=True):
    __tablename__ = "app_group_memberships"
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: int = Field(foreign_key="apps.id", index=True)
    app_group_id: int = Field(foreign_key="app_groups.id", index=True)
    sort_order: int = 0


class Dashboard(SQLModel, table=True):
    __tablename__ = "dashboards"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(index=True, unique=True)
    background_url: Optional[str] = None              # fond propre au dashboard
    is_default: bool = False                           # repli global
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class DashboardAppGroup(SQLModel, table=True):
    __tablename__ = "dashboard_app_groups"
    id: Optional[int] = Field(default=None, primary_key=True)
    dashboard_id: int = Field(foreign_key="dashboards.id", index=True)
    app_group_id: int = Field(foreign_key="app_groups.id", index=True)
    sort_order: int = 0


# --- Droits ------------------------------------------------------------------
class AppAccess(SQLModel, table=True):
    __tablename__ = "app_access"
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: int = Field(foreign_key="apps.id", index=True)
    group_id: int = Field(foreign_key="groups.id", index=True)


class DashboardAccess(SQLModel, table=True):
    __tablename__ = "dashboard_access"
    id: Optional[int] = Field(default=None, primary_key=True)
    dashboard_id: int = Field(foreign_key="dashboards.id", index=True)
    group_id: int = Field(foreign_key="groups.id", index=True)


class UserAppOverride(SQLModel, table=True):
    __tablename__ = "user_app_overrides"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    app_id: int = Field(foreign_key="apps.id", index=True)
    effect: str = "ALLOW"                              # ALLOW | DENY


class UserDashboardOverride(SQLModel, table=True):
    __tablename__ = "user_dashboard_overrides"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    dashboard_id: int = Field(foreign_key="dashboards.id", index=True)
    effect: str = "ALLOW"


# --- Config ------------------------------------------------------------------
class AppSetting(SQLModel, table=True):
    """Clé/valeur pour la config éditable (apparence, LDAP, SSO, assistant)."""
    __tablename__ = "app_settings"
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: Optional[str] = None
