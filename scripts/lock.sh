#!/usr/bin/env bash
# Régénère requirements.lock (versions exactes + empreintes, Python 3.10 → 3.13,
# toutes plateformes) à partir de requirements.txt. Nécessite uv (pip install uv).
set -euo pipefail
cd "$(dirname "$0")/.."
uv pip compile requirements.txt --universal --python-version 3.10 --generate-hashes \
   --no-header -o requirements.lock
echo "requirements.lock régénéré : relancer les tests avant de publier."
