"""Récupération du logo d'une application à partir de son adresse.

Le serveur lit la page de l'application (en suivant les redirections, souvent vers une
page de connexion), relève les icônes qu'elle déclare (`<link rel="icon">`,
`apple-touch-icon`, manifeste web), essaie aussi `/favicon.ico` et
`/apple-touch-icon.png`, télécharge le tout, ne garde que de vraies images et choisit la
meilleure (SVG, sinon la plus grande). Le service de favicons de Google ne sert que de
dernier recours, et seulement s'il connaît le site (sinon il renvoie un globe générique
avec un statut 404).

Une redirection vers un autre hôte (fournisseur d'identité, portail de connexion) ne
doit pas donner le logo de ce fournisseur : les icônes de l'hôte demandé passent avant,
puis Google pour cet hôte, et seulement ensuite celles de la page d'arrivée.

Les applications internes, invisibles d'Internet, ont ainsi leur logo : c'est le serveur
MyApps qui les interroge. Les certificats non reconnus (autorité interne) sont acceptés,
puisque seule une image est récupérée, et elle est vérifiée."""
import asyncio
import json
import re
import struct
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36 MyApps-logo")
PAGE_MAX = 1024 * 1024
MANIFEST_MAX = 256 * 1024
ICON_MAX = 1024 * 1024
MIN_SIZE = 16
SVG_SIZE = 4096          # un SVG passe avant n'importe quelle image matricielle
MAX_CANDIDATES = 12
TIMEOUT = httpx.Timeout(6.0)
TOTAL_BUDGET = 20.0

EXT = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp", "ico": ".ico", "svg": ".svg"}

# Rangs : plus petit = préféré, quelle que soit la taille.
RANK_OWN, RANK_GOOGLE, RANK_FOREIGN = 0, 1, 2


@dataclass
class Icon:
    data: bytes
    kind: str          # clé de EXT
    size: int          # plus grand côté en pixels (SVG_SIZE pour un SVG)
    source: str        # adresse d'origine de l'image


# --- Reconnaissance des images ----------------------------------------------
def _jpeg_size(b: bytes) -> int:
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", b[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return max(w, h)
        i += 2 + seg
    return 0


def _webp_size(b: bytes) -> int:
    chunk = b[12:16]
    if chunk == b"VP8X" and len(b) >= 30:
        w = int.from_bytes(b[24:27], "little") + 1
        h = int.from_bytes(b[27:30], "little") + 1
        return max(w, h)
    if chunk == b"VP8L" and len(b) >= 25:
        bits = int.from_bytes(b[21:25], "little")
        return max((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8 " and len(b) >= 30:
        w, h = struct.unpack("<HH", b[26:30])
        return max(w & 0x3FFF, h & 0x3FFF)
    return 0


def _ico_size(b: bytes) -> int:
    count = struct.unpack("<H", b[4:6])[0]
    if not 0 < count <= 64 or len(b) < 6 + 16 * count:
        return 0
    best = 0
    for k in range(count):
        w, h = b[6 + 16 * k], b[7 + 16 * k]
        best = max(best, w or 256, h or 256)
    return best


def identify(data: bytes) -> Optional[tuple[str, int]]:
    """(type, plus grand côté) d'une image reconnue, sinon None : une page d'erreur
    servie avec un statut 200 ou un faux type MIME ne passe pas."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return "png", max(w, h)
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            return "gif", max(w, h)
        if data.startswith(b"\xff\xd8\xff"):
            return "jpeg", _jpeg_size(data)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp", _webp_size(data)
        if data[:4] == b"\x00\x00\x01\x00":
            return "ico", _ico_size(data)
        head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if (head.startswith(b"<svg") or head.startswith(b"<?xml") or head.startswith(b"<!--")) and b"<svg" in head:
            return "svg", SVG_SIZE
    except struct.error:
        return None
    return None


# --- Lecture de la page ------------------------------------------------------
class _HeadParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base: Optional[str] = None
        self.links: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "link" and a.get("href"):
            self.links.append(a)


def _declared_size(sizes: str) -> int:
    best = 0
    for s in (sizes or "").lower().split():
        if s == "any":
            return SVG_SIZE
        w, _, h = s.partition("x")
        if w.isdigit() and h.isdigit():
            best = max(best, int(w), int(h))
    return best


def page_icons(html: str, page_url: str) -> tuple[list[tuple[str, int]], Optional[str]]:
    """Icônes déclarées par la page : [(adresse absolue, taille annoncée)] et l'adresse
    du manifeste. Les icônes monochromes (mask-icon) sont écartées : elles s'affichent
    en noir."""
    p = _HeadParser()
    try:
        p.feed(html)
    except Exception:
        pass
    base = urljoin(page_url, p.base) if p.base else page_url
    icons, manifest = [], None
    for link in p.links:
        rels = set(link.get("rel", "").lower().split())
        href = urljoin(base, link["href"].strip())
        if not href.startswith(("http://", "https://")):
            continue
        if "manifest" in rels and manifest is None:
            manifest = href
        elif rels & {"icon", "apple-touch-icon", "apple-touch-icon-precomposed", "fluid-icon"}:
            icons.append((href, _declared_size(link.get("sizes", ""))))
    return icons, manifest


def manifest_icons(text: str, manifest_url: str) -> list[tuple[str, int]]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for ic in (data.get("icons") or []) if isinstance(data, dict) else []:
        if not isinstance(ic, dict) or not isinstance(ic.get("src"), str):
            continue
        if "monochrome" in str(ic.get("purpose", "")).split():
            continue
        href = urljoin(manifest_url, ic["src"].strip())
        if href.startswith(("http://", "https://")):
            out.append((href, _declared_size(str(ic.get("sizes", "")))))
    return out


# --- Téléchargements ---------------------------------------------------------
async def _get(c: httpx.AsyncClient, url: str, cap: int) -> Optional[tuple[str, bytes]]:
    """(adresse finale, contenu) si 200 et pas plus de `cap` octets, sinon None."""
    try:
        async with c.stream("GET", url) as r:
            if r.status_code != 200:
                return None
            if int(r.headers.get("content-length") or 0) > cap:
                return None
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > cap:
                    return None
            return str(r.url), bytes(buf)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        return None


async def _icon(c: httpx.AsyncClient, url: str) -> Optional[Icon]:
    got = await _get(c, url, ICON_MAX)
    if not got:
        return None
    found = identify(got[1])
    if not found or found[1] < MIN_SIZE:
        return None
    return Icon(got[1], found[0], found[1], url)


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _same_site(a: str, b: str) -> bool:
    """Même hôte, au « www. » près (exemple.org redirige souvent vers www.exemple.org)."""
    strip = lambda h: h[4:] if h.startswith("www.") else h
    return strip(_host(a)) == strip(_host(b))


def google_url(host: str) -> str:
    return f"https://www.google.com/s2/favicons?domain={host}&sz=128"


_BARE = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?([/?#].*)?$")


def normalize(url: str) -> list[str]:
    """Adresses à essayer : sans schéma, https puis http (applications internes)."""
    url = (url or "").strip()
    if not url:
        return []
    if "://" not in url:
        if not _BARE.match(url):
            return []
        return ["https://" + url, "http://" + url]
    if not url.lower().startswith(("http://", "https://")) or not urlparse(url).hostname:
        return []
    return [url]


def _client(transport: Optional[httpx.AsyncBaseTransport], verify: bool) -> httpx.AsyncClient:
    kw = {"transport": transport} if transport else {"verify": verify}
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, max_redirects=6,
                             headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"}, **kw)


async def _fetch_page(url: str, transport) -> tuple[Optional[str], str, httpx.AsyncClient]:
    """Charge la page ; renvoie (html, adresse finale, client à réutiliser).
    Sur échec de connexion (certificat interne non reconnu, par exemple), réessaie sans
    vérification du certificat."""
    for verify in (True, False):
        c = _client(transport, verify)
        try:
            # Le statut importe peu : une page 401 ou 403 déclare souvent ses icônes.
            async with c.stream("GET", url, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}) as r:
                if "html" not in r.headers.get("content-type", "").lower():
                    return None, str(r.url), c
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) >= PAGE_MAX:
                        break
                return bytes(buf).decode(r.encoding or "utf-8", "replace"), str(r.url), c
        except httpx.ConnectError:
            await c.aclose()
            if transport:
                break
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return None, url, c
    return None, url, _client(transport, False)


def _best(icons: list[tuple[int, int, Icon]]) -> Optional[Icon]:
    if not icons:
        return None
    # Rang d'abord, puis la plus grande, puis l'ordre de découverte.
    return min(icons, key=lambda t: (t[0], -t[2].size, t[1]))[2]


async def find_logo(url: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> Optional[Icon]:
    """Meilleur logo pour l'application à `url`, ou None."""
    tries = normalize(url)
    if not tries:
        return None
    try:
        return await asyncio.wait_for(_find(tries, transport), TOTAL_BUDGET)
    except asyncio.TimeoutError:
        return None


async def _find(tries: list[str], transport) -> Optional[Icon]:
    html, final, c = None, tries[0], None
    for t in tries:
        if c:
            await c.aclose()
        html, final, c = await _fetch_page(t, transport)
        if html is not None:
            break
    asked = tries[0] if html is None else t
    try:
        own_host = _host(asked)
        same = _same_site(final, asked)
        declared, manifest = page_icons(html, final) if html else ([], None)
        if manifest:
            got = await _get(c, manifest, MANIFEST_MAX)
            if got:
                declared += manifest_icons(got[1].decode("utf-8", "replace"), got[0])

        cands: list[tuple[int, str]] = []
        seen = set()

        def add(rank: int, u: str):
            if u not in seen and len(cands) < MAX_CANDIDATES:
                seen.add(u)
                cands.append((rank, u))

        page_rank = RANK_OWN if same else RANK_FOREIGN
        # Les plus grandes annoncées d'abord, pour ne pas les perdre au plafond.
        for u, _ in sorted(declared, key=lambda d: -d[1]):
            add(page_rank, u)
        for origin in [_origin(asked)] + ([] if same else [_origin(final)]):
            rank = RANK_OWN if _same_site(origin, asked) else RANK_FOREIGN
            add(rank, origin + "/apple-touch-icon.png")
            add(rank, origin + "/favicon.ico")

        results = await asyncio.gather(*[_icon(c, u) for _, u in cands])
        icons = [(rank, k, ic) for k, ((rank, _), ic) in enumerate(zip(cands, results)) if ic]
        best = _best([i for i in icons if i[0] == RANK_OWN])
        if best:
            return best
        if own_host and "." in own_host:
            g = await _icon(c, google_url(own_host))     # 404 + globe s'il ne connaît pas le site
            if g:
                return g
        return _best(icons)
    finally:
        await c.aclose()
