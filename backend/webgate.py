"""Verrou du code de l'application : rien d'autre que l'écran de connexion sans être connecté.

Le jeton JWT (localStorage) protège l'API ; mais le navigateur ne l'envoie pas quand il charge la
page, ses scripts ou ses images. On pose donc, à la connexion, un cookie de session signé
(HttpOnly, SameSite=Strict) qui ouvre l'accès aux fichiers de l'interface :
- sans cookie valide : « / » renvoie login.html (formulaire seul, aucune donnée, aucun code de
  l'application) ; tous les autres fichiers répondent 404, sauf la petite liste PUBLIC ;
- avec cookie valide (compte existant et actif) : l'interface complète, comme avant.

Monté par `install(app, frontend_dir, public, user_ok)` APRÈS les routeurs de l'API.
"""
import base64
import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from fastapi import Request
from fastapi.responses import FileResponse, Response

COOKIE = os.getenv("GATE_COOKIE_NAME", "app_gate")
# « Secure » : 1 = toujours, 0 = jamais, auto (défaut) = si la visite est en HTTPS (directement,
# via le proxy X-Forwarded-Proto, ou via Cloudflare CF-Visitor). Une installation en HTTP simple
# doit pouvoir poser le cookie, sinon la connexion tournerait en boucle.
SECURE_MODE = os.getenv("GATE_COOKIE_SECURE", "auto")
HOURS = 12
REMEMBER_DAYS = 30


def _key() -> bytes:
    secret = os.getenv("JWT_SECRET", "") or "gate-" + str(os.getpid())
    return hashlib.sha256(("webgate|" + secret).encode()).digest()


def _sig(username: str, exp: str) -> str:
    mac = hmac.new(_key(), f"{username}|{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:18]).decode().rstrip("=")


def make_cookie(username: str, hours: int) -> str:
    exp = str(int(time.time()) + hours * 3600)
    u = base64.urlsafe_b64encode(username.encode()).decode().rstrip("=")
    return f"{u}.{exp}.{_sig(username, exp)}"


def read_cookie(value: Optional[str]) -> Optional[str]:
    try:
        u, exp, sig = (value or "").split(".")
        username = base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode()
        if int(exp) < time.time() or not hmac.compare_digest(sig, _sig(username, exp)):
            return None
        return username
    except (ValueError, UnicodeDecodeError):
        return None


def _secure(request) -> bool:
    if SECURE_MODE in ("0", "1"):
        return SECURE_MODE == "1"
    if request is None:
        return True
    h = request.headers
    return (request.url.scheme == "https" or h.get("x-forwarded-proto", "").lower() == "https"
            or '"https"' in h.get("cf-visitor", ""))


def set_cookie(response, username: str, remember: bool = False, request=None) -> None:
    hours = REMEMBER_DAYS * 24 if remember else HOURS
    response.set_cookie(COOKIE, make_cookie(username, hours), max_age=hours * 3600, httponly=True,
                        secure=_secure(request), samesite="strict", path="/")


def refresh_cookie(request: Request, response, username: str) -> None:
    """Reprise de session avec un jeton gardé (/api/auth/me) : (re)pose le cookie s'il manque."""
    if read_cookie(request.cookies.get(COOKIE)) != username:
        set_cookie(response, username, request=request)


def clear_cookie(response, request=None) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=_secure(request), samesite="strict")


def install(app, frontend_dir, public: Iterable[str], user_ok: Callable[[str], bool],
            login_file: str = "login.html", index_file: str = "index.html",
            open_check: Optional[Callable[[], bool]] = None, public_dirs: Iterable[str] = ()) -> None:
    """`open_check()` vrai = interface ouverte sans connexion (assistant de premier démarrage,
    tant qu'aucun administrateur n'existe). `public_dirs` : dossiers servis à tous (fonds…)."""
    root = Path(frontend_dir).resolve()
    public = set(public) | {login_file}
    public_dirs = tuple(d.rstrip("/") + "/" for d in public_dirs)
    nocache = {"Cache-Control": "no-cache, must-revalidate"}

    def allowed(request: Request) -> bool:
        if open_check and open_check():
            return True
        username = read_cookie(request.cookies.get(COOKIE))
        return bool(username) and user_ok(username)

    @app.get("/", include_in_schema=False)
    def _gate_index(request: Request):
        name = index_file if allowed(request) else login_file
        return FileResponse(root / name, headers=nocache)

    @app.get("/{path:path}", include_in_schema=False)
    def _gate_file(path: str, request: Request):
        if path.startswith("api/"):
            return Response(status_code=404)
        f = (root / path).resolve()
        if root not in f.parents or not f.is_file():
            return Response(status_code=404)
        rel = f.relative_to(root).as_posix()
        if rel == index_file:
            return _gate_index(request)
        if rel in public or rel.startswith(public_dirs) or allowed(request):
            return FileResponse(f, headers=nocache)
        return Response(status_code=404)
