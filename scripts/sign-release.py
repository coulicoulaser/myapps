#!/usr/bin/env python3
"""Signature des versions publiées (format minisign).

  keygen DOSSIER     crée une paire de clés : DOSSIER/myapps-release.key (secrète)
                     et DOSSIER/myapps-release.pub (à copier dans backend/update_key.pub)
  sign [FICHIER]     signe dist/latest.json (défaut) → FICHIER.minisig, avec la clé de
                     MYAPPS_SIGNING_KEY ou du fichier MYAPPS_SIGNING_KEY_FILE ; refuse si la
                     clé ne correspond pas à backend/update_key.pub
  verify [FICHIER]   vérifie FICHIER.minisig avec backend/update_key.pub

Équivalent minisign : minisign -Vm dist/latest.json -p backend/update_key.pub
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import signing  # noqa: E402

PUB = ROOT / "backend" / "update_key.pub"


def _secret() -> str:
    if os.getenv("MYAPPS_SIGNING_KEY"):
        return os.environ["MYAPPS_SIGNING_KEY"]
    path = os.getenv("MYAPPS_SIGNING_KEY_FILE")
    if path:
        return Path(path).read_text().strip()
    sys.exit("Clé absente : définir MYAPPS_SIGNING_KEY ou MYAPPS_SIGNING_KEY_FILE")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "keygen":
        out = Path(argv[2] if len(argv) > 2 else ".")
        key, pub = out / "myapps-release.key", out / "myapps-release.pub"
        if key.exists():
            sys.exit(f"{key} existe déjà")
        secret, public = signing.generate_keypair()
        old = os.umask(0o077)
        key.write_text(secret + "\n")
        os.umask(old)
        pub.write_text(public)
        print(f"Clé secrète : {key} (à garder hors du dépôt)\nClé publique : {pub}")
        return 0
    target = Path(argv[2] if len(argv) > 2 else ROOT / "dist" / "latest.json")
    sig = target.with_name(target.name + ".minisig")
    data = target.read_bytes()
    if cmd == "sign":
        version = json.loads(data)["version"]
        sig.write_text(signing.sign(data, _secret(), f"myapps {version}"))
        try:
            signing.verify(data, sig.read_text(), PUB.read_text())
        except signing.SignatureError as e:
            sig.unlink()
            sys.exit(f"Signature refusée par backend/update_key.pub ({e}) : mauvaise clé ?")
        print(f"{sig} : signé et vérifié (myapps {version})")
        return 0
    if cmd == "verify":
        print("Signature valide :", signing.verify(data, sig.read_text(), PUB.read_text()))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
