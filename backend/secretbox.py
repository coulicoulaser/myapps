"""Chiffrement des secrets stockés en base (app_settings) : Fernet, clé `SECRETS_KEY` fournie par
l'environnement (fichier d'environnement du service, écrit par install.sh), jamais dans le code
ni dans la base.

Les clés listées dans SECRET_KEYS sont chiffrées à l'écriture et déchiffrées à la lecture par
get_setting/set_setting (security.py) : le reste du code manipule la valeur en clair. Une valeur
encore en clair (installation antérieure) est rendue telle quelle ; `encrypt_stored_secrets` la
chiffre au démarrage dès que la clé est disponible."""
import logging
import os

SECRET_KEYS = {"ldap_bind_password", "sso_client_secret"}
_PREFIX = "gAAAAA"
log = logging.getLogger("secretbox")


def available() -> bool:
    return bool(os.getenv("SECRETS_KEY", "").strip())


def _fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("SECRETS_KEY", "").strip()
    if not key:
        raise RuntimeError("SECRETS_KEY absent : relancez install.sh pour la créer")
    return Fernet(key.encode())


def is_encrypted(v) -> bool:
    return isinstance(v, str) and v.startswith(_PREFIX)


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(str(plain).encode()).decode()


def decrypt(stored):
    if not stored or not is_encrypted(stored):
        return stored
    return _fernet().decrypt(stored.encode()).decode()
