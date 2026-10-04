"""Lecture du CHANGELOG.md de l'app + version courante (SemVer).

La version affichée = le premier numéro `## [x.y.z]` (ou `x.y.z-beta.N`) rencontré en haut du
CHANGELOG (source de vérité unique : on bumpe le CHANGELOG à chaque modif)."""
import re
from pathlib import Path

_CHANGELOG = Path(__file__).parent.parent / "CHANGELOG.md"
_VERSION_RE = re.compile(r"^##\s*\[?v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)", re.MULTILINE)
_FALLBACK = "0.0.0"


def read_markdown() -> str:
    try:
        return _CHANGELOG.read_text(encoding="utf-8")
    except Exception:
        return ""


def current_version() -> str:
    m = _VERSION_RE.search(read_markdown())
    return m.group(1) if m else _FALLBACK


def section(version: str) -> str:
    """Texte Markdown de la section d'une version (notes de version)."""
    md = read_markdown()
    m = re.search(rf"^##\s*\[?v?{re.escape(version)}\]?.*$", md, re.MULTILINE)
    if not m:
        return ""
    rest = md[m.end():]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    return rest[: nxt.start()].strip() if nxt else rest.strip()
