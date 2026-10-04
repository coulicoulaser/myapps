"""Chemins et paramètres d'exécution.

Le code (lecture seule en production) et les données (base, images envoyées,
code d'installation) sont séparés : l'installeur place le code dans /opt/myapps
et les données dans /var/lib/myapps (variable MYAPPS_DATA_DIR). En développement,
tout reste dans le dossier du projet (`data/`).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

DATA_DIR = Path(os.getenv("MYAPPS_DATA_DIR") or (BASE_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
SETUP_CODE_FILE = DATA_DIR / "setup-code"

DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'myapps.db'}"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# --- Mises à jour --------------------------------------------------------------
# Dépôt GitHub public où sont publiées les versions signées (voir updater.py).
UPDATE_REPO = os.getenv("MYAPPS_UPDATE_REPO") or "coulicoulaser/myapps"
# Surcharges réservées aux tests et aux forks : URL du manifeste, clé publique (texte minisign).
UPDATE_MANIFEST_URL = os.getenv("MYAPPS_UPDATE_MANIFEST_URL") or ""
UPDATE_PUBKEY = os.getenv("MYAPPS_UPDATE_PUBKEY") or ""
UPDATE_PUBKEY_FILE = Path(__file__).resolve().parent / "update_key.pub"
# Posés par install.sh : sans eux, l'installation ne sait pas se mettre à jour seule.
INSTALL_DIR = os.getenv("MYAPPS_INSTALL_DIR") or ""
SERVICE_NAME = os.getenv("MYAPPS_SERVICE") or ""
UPDATER_ENABLED = os.getenv("MYAPPS_UPDATER") == "1"
