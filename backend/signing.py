"""Signatures Ed25519 au format minisign.

Les versions publiées sont signées (manifeste `latest.json` → `latest.json.minisig`).
Le format est celui de minisign : n'importe qui peut vérifier une version avec
`minisign -Vm latest.json -P <clé publique>`. La clé publique de confiance est livrée
avec l'application (`backend/update_key.pub`) ; la clé privée ne quitte jamais
l'éditeur (secret GitHub Actions ou poste de signature).

Format des fichiers (https://jedisct1.github.io/minisign/) :
  clé publique : base64("Ed" + id(8) + pk(32))
  signature    : base64("ED" + id(8) + sig(64)) où sig = Ed25519(BLAKE2b-512(message)),
                 puis le commentaire de confiance et base64(Ed25519(sig + commentaire)).
La clé secrète, elle, n'est PAS au format minisign (chiffré par scrypt) : c'est une
simple chaîne « MYAPPS-SIGNING-KEY-V1:base64(id(8) + graine(32)) », faite pour être
collée dans un secret de CI.
"""
import base64
import hashlib
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SECRET_PREFIX = "MYAPPS-SIGNING-KEY-V1:"


class SignatureError(Exception):
    """Signature absente, mal formée, d'une autre clé ou invalide."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str, what: str) -> bytes:
    try:
        return base64.b64decode(s.strip(), validate=True)
    except Exception:
        raise SignatureError(f"{what} : base64 invalide")


# --- clés ----------------------------------------------------------------------
def generate_keypair() -> tuple[str, str]:
    """Renvoie (clé secrète, fichier de clé publique minisign)."""
    keyid = os.urandom(8)
    seed = os.urandom(32)
    pk = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    secret = SECRET_PREFIX + _b64(keyid + seed)
    public = f"untrusted comment: minisign public key {keyid[::-1].hex().upper()}\n{_b64(b'Ed' + keyid + pk)}\n"
    return secret, public


def _load_secret(secret: str) -> tuple[bytes, Ed25519PrivateKey]:
    secret = (secret or "").strip()
    if not secret.startswith(SECRET_PREFIX):
        raise SignatureError("clé secrète : préfixe attendu " + SECRET_PREFIX)
    raw = _unb64(secret[len(SECRET_PREFIX):], "clé secrète")
    if len(raw) != 40:
        raise SignatureError("clé secrète : longueur invalide")
    return raw[:8], Ed25519PrivateKey.from_private_bytes(raw[8:])


def parse_public_key(text: str) -> tuple[bytes, Ed25519PublicKey]:
    """Accepte le fichier minisign complet ou la seule ligne base64."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip() and not l.startswith("untrusted comment:")]
    if not lines:
        raise SignatureError("clé publique vide")
    raw = _unb64(lines[0], "clé publique")
    if len(raw) != 42 or raw[:2] != b"Ed":
        raise SignatureError("clé publique : format minisign Ed25519 attendu")
    return raw[2:10], Ed25519PublicKey.from_public_bytes(raw[10:])


# --- signature -----------------------------------------------------------------
def sign(message: bytes, secret: str, trusted_comment: str) -> str:
    """Signature minisign (pré-hachée BLAKE2b, algorithme « ED »)."""
    if "\n" in trusted_comment or "\r" in trusted_comment:
        raise SignatureError("commentaire de confiance sur une seule ligne")
    keyid, sk = _load_secret(secret)
    sig = sk.sign(hashlib.blake2b(message, digest_size=64).digest())
    global_sig = sk.sign(sig + trusted_comment.encode("utf-8"))
    return (f"untrusted comment: signature MyApps\n{_b64(b'ED' + keyid + sig)}\n"
            f"trusted comment: {trusted_comment}\n{_b64(global_sig)}\n")


def verify(message: bytes, signature: str, public_key: str) -> str:
    """Vérifie une signature minisign. Renvoie le commentaire de confiance, ou lève
    SignatureError. Les signatures « Ed » (non pré-hachées) sont aussi acceptées."""
    keyid, pk = parse_public_key(public_key)
    lines = (signature or "").splitlines()
    if len(lines) < 4 or not lines[0].startswith("untrusted comment:") or not lines[2].startswith("trusted comment: "):
        raise SignatureError("signature : format minisign attendu (4 lignes)")
    raw = _unb64(lines[1], "signature")
    if len(raw) != 74:
        raise SignatureError("signature : longueur invalide")
    alg, sig_keyid, sig = raw[:2], raw[2:10], raw[10:]
    if sig_keyid != keyid:
        raise SignatureError("signature faite avec une autre clé que la clé de confiance")
    if alg == b"ED":
        signed = hashlib.blake2b(message, digest_size=64).digest()
    elif alg == b"Ed":
        signed = message
    else:
        raise SignatureError("signature : algorithme inconnu")
    trusted = lines[2][len("trusted comment: "):]
    global_sig = _unb64(lines[3], "signature globale")
    try:
        pk.verify(sig, signed)
        pk.verify(global_sig, sig + trusted.encode("utf-8"))
    except InvalidSignature:
        raise SignatureError("signature invalide : fichier modifié ou mauvaise clé")
    return trusted
