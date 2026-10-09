"""Logo automatique : lecture de la page, choix de la meilleure icône, refus des fausses
images. Les sites sont simulés par httpx.MockTransport (aucun accès réseau)."""
import asyncio
import struct

import httpx
import pytest

import favicon


def png(side: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", side, side) + b"\x08\x06\x00\x00\x00" + b"\x00" * 64


def ico(*sides: int) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(sides))
    entries = b"".join(struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, 40, 0) for s in sides)
    return head + entries + b"\x00" * 64


SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


def site(routes: dict):
    """routes : adresse -> (statut, type, contenu) ou ("redirect", cible)."""
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        r = routes.get(url)
        if r is None:
            return httpx.Response(404, headers={"content-type": "text/html"}, content=b"<h1>404</h1>")
        if r[0] == "redirect":
            return httpx.Response(302, headers={"location": r[1]})
        status, ctype, body = r
        return httpx.Response(status, headers={"content-type": ctype}, content=body)
    return httpx.MockTransport(handler)


def find(url, routes):
    return asyncio.run(favicon.find_logo(url, transport=site(routes)))


def page(head: str) -> tuple:
    return (200, "text/html; charset=utf-8", f"<html><head>{head}</head><body></body></html>".encode())


# --- reconnaissance des images ------------------------------------------------
def test_identify_formats():
    assert favicon.identify(png(180)) == ("png", 180)
    assert favicon.identify(ico(16, 32, 48)) == ("ico", 48)
    assert favicon.identify(ico(0)) == ("ico", 256)          # 0 = 256 px dans un ICO
    assert favicon.identify(b"GIF89a" + struct.pack("<HH", 64, 40) + b"\x00" * 20) == ("gif", 64)
    assert favicon.identify(SVG) == ("svg", favicon.SVG_SIZE)
    jpeg = b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"\x00" * 14 + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 100, 200) + b"\x00" * 20
    assert favicon.identify(jpeg) == ("jpeg", 200)
    webp = b"RIFF" + b"\x00" * 4 + b"WEBPVP8X" + b"\x00" * 8 + (511).to_bytes(3, "little") + (255).to_bytes(3, "little")
    assert favicon.identify(webp) == ("webp", 512)


def test_identify_rejects_non_images():
    assert favicon.identify(b"<!doctype html><html><body>Not found</body></html>") is None
    assert favicon.identify(b"") is None
    assert favicon.identify(b'{"error": "nope"}') is None


# --- choix de l'icône ---------------------------------------------------------
def test_declared_svg_beats_bigger_png_and_favicon():
    ic = find("https://app.test", {
        "https://app.test": page('<link rel="icon" href="/logo.svg" type="image/svg+xml">'
                                 '<link rel="apple-touch-icon" href="/touch.png" sizes="180x180">'),
        "https://app.test/logo.svg": (200, "image/svg+xml", SVG),
        "https://app.test/touch.png": (200, "image/png", png(180)),
        "https://app.test/favicon.ico": (200, "image/x-icon", ico(16)),
    })
    assert ic.kind == "svg" and ic.source == "https://app.test/logo.svg"


def test_largest_raster_wins_and_relative_links_follow_base():
    ic = find("https://app.test/portail/", {
        "https://app.test/portail/": page('<base href="/static/"><link rel="icon" href="i32.png" sizes="32x32">'
                                          '<link rel="icon" href="i192.png" sizes="192x192">'),
        "https://app.test/static/i32.png": (200, "image/png", png(32)),
        "https://app.test/static/i192.png": (200, "image/png", png(192)),
        "https://app.test/favicon.ico": (200, "image/x-icon", ico(16, 32)),
    })
    assert (ic.kind, ic.size, ic.source) == ("png", 192, "https://app.test/static/i192.png")


def test_manifest_icons_are_used():
    ic = find("https://app.test", {
        "https://app.test": page('<link rel="manifest" href="/site.webmanifest"><link rel="icon" href="/f.ico">'),
        "https://app.test/site.webmanifest": (200, "application/manifest+json",
                                              b'{"icons":[{"src":"/m192.png","sizes":"192x192"},{"src":"/m512.png","sizes":"512x512"},'
                                              b'{"src":"/mono.png","sizes":"1024x1024","purpose":"monochrome"}]}'),
        "https://app.test/m192.png": (200, "image/png", png(192)),
        "https://app.test/m512.png": (200, "image/png", png(512)),
        "https://app.test/mono.png": (200, "image/png", png(1024)),
        "https://app.test/f.ico": (200, "image/x-icon", ico(32)),
    })
    assert ic.source == "https://app.test/m512.png"


def test_undeclared_favicon_ico_found():
    ic = find("https://app.test", {
        "https://app.test": page("<title>Connexion</title>"),
        "https://app.test/favicon.ico": (200, "image/x-icon", ico(16, 48)),
    })
    assert (ic.kind, ic.size) == ("ico", 48)


def test_fake_images_and_mask_icons_are_ignored():
    """Page d'erreur servie en 200 sous un type image, icône monochrome, icône 1 px."""
    ic = find("https://app.test", {
        "https://app.test": page('<link rel="icon" href="/broken.png"><link rel="mask-icon" href="/mask.svg">'
                                 '<link rel="icon" href="/pixel.png">'),
        "https://app.test/broken.png": (200, "image/png", b"<html>Oups</html>"),
        "https://app.test/mask.svg": (200, "image/svg+xml", SVG),
        "https://app.test/pixel.png": (200, "image/png", png(1)),
        "https://app.test/favicon.ico": (200, "image/x-icon", ico(32)),
    })
    assert ic.source == "https://app.test/favicon.ico"


def test_sso_redirect_does_not_give_identity_provider_logo():
    """L'application renvoie vers la page de connexion d'un autre hôte : son propre
    favicon passe avant le logo du fournisseur d'identité."""
    ic = find("https://app.test", {
        "https://app.test": ("redirect", "https://login.idp.test/authorize?x=1"),
        "https://login.idp.test/authorize?x=1": page('<link rel="icon" href="/idp.svg">'),
        "https://login.idp.test/idp.svg": (200, "image/svg+xml", SVG),
        "https://app.test/favicon.ico": (200, "image/x-icon", ico(32)),
    })
    assert ic.source == "https://app.test/favicon.ico"


def test_www_redirect_counts_as_same_site():
    ic = find("https://app.test", {
        "https://app.test": ("redirect", "https://www.app.test/fr/"),
        "https://www.app.test/fr/": page('<link rel="icon" href="/big.png" sizes="256x256">'),
        "https://www.app.test/big.png": (200, "image/png", png(256)),
    })
    assert ic.source == "https://www.app.test/big.png"


def test_google_only_as_fallback_and_its_404_globe_refused():
    globe = (404, "image/png", png(16))
    known = favicon.google_url("app.test")
    assert find("https://app.test", {"https://app.test": page(""), known: globe}) is None
    ic = find("https://app.test", {"https://app.test": page(""), known: (200, "image/png", png(64))})
    assert ic.source == known


def test_foreign_icons_last_resort():
    ic = find("https://app.test", {
        "https://app.test": ("redirect", "https://login.idp.test/"),
        "https://login.idp.test/": page('<link rel="icon" href="/idp.png" sizes="64x64">'),
        "https://login.idp.test/idp.png": (200, "image/png", png(64)),
    })
    assert ic.source == "https://login.idp.test/idp.png"


def test_bare_host_tries_https_then_http():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.scheme == "https":
            raise httpx.ConnectError("refused", request=req)
        if req.url.path == "/favicon.ico":
            return httpx.Response(200, content=ico(32))
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")
    ic = asyncio.run(favicon.find_logo("intranet.test", transport=httpx.MockTransport(handler)))
    assert ic.source == "http://intranet.test/favicon.ico"


@pytest.mark.parametrize("bad", ["", "   ", "javascript:alert(1)", "file:///etc/passwd", "ftp://x.test"])
def test_refused_addresses(bad):
    assert favicon.normalize(bad) == []
    assert asyncio.run(favicon.find_logo(bad)) is None


# --- route d'administration ---------------------------------------------------
def test_route_saves_logo_once(client, admin_auth, monkeypatch, tmp_path):
    import portal_routes

    async def fake(url, transport=None):
        return favicon.Icon(png(128), "png", 128, url + "/i.png")
    monkeypatch.setattr(favicon, "find_logo", fake)
    monkeypatch.setattr(portal_routes, "UPLOAD_DIR", tmp_path)
    r1 = client.get("/api/logo", params={"url": "https://app.test"}, headers=admin_auth).json()
    r2 = client.get("/api/logo", params={"url": "https://app.test"}, headers=admin_auth).json()
    assert r1["logo"].startswith("/uploads/logo-") and r1["logo"].endswith(".png")
    assert r1 == r2 and r1["size"] == 128
    assert len(list(tmp_path.iterdir())) == 1


def test_route_requires_address(client, admin_auth):
    assert client.get("/api/logo", headers=admin_auth).status_code == 400
    assert client.get("/api/logo", params={"url": "javascript:x"}, headers=admin_auth).status_code == 400


def test_route_nothing_found(client, admin_auth, monkeypatch):
    async def none(url, transport=None):
        return None
    monkeypatch.setattr(favicon, "find_logo", none)
    r = client.get("/api/logo", params={"url": "https://app.test"}, headers=admin_auth).json()
    assert r["logo"] == "" and r["detail"]
