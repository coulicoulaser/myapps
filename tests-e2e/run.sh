#!/usr/bin/env bash
# Lance un serveur MyApps jetable (données temporaires), puis les parcours e2e :
#   1. wizard.test.js : assistant complet (code, admin, identité, fonds, dashboard, apps, portail, admin)
#   2. resume.test.js : utilisateur simple, reprise de l'assistant par un admin
# Prérequis : ../.venv (pip install -r ../requirements.txt), Node 18+, `npm install` ici.
# La recherche de ville et la météo appellent Open-Meteo : accès Internet requis.
set -euo pipefail
cd "$(dirname "$0")"
[[ -d node_modules ]] || npm install --silent
TMP="$(mktemp -d)"; PORT="${PORT:-8199}"
export MYAPPS_DATA_DIR="$TMP" JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
(cd ../backend && exec ../.venv/bin/uvicorn app:app --port "$PORT" --log-level warning) &
SRV=$!
trap 'kill $SRV 2>/dev/null; rm -rf "$TMP"' EXIT
for _ in $(seq 1 40); do curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.25; done
export BASE="http://127.0.0.1:$PORT" CODE_FILE="$TMP/setup-code"
echo "Assistant :";            node wizard.test.js
echo "Reprise / utilisateur :"; node resume.test.js
echo "OK"
