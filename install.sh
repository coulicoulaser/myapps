#!/usr/bin/env bash
# =============================================================================
#  MyApps — installation et mise à jour sur Debian / Ubuntu
#
#  Installe les dépendances système, l'application dans un environnement Python
#  isolé, un service systemd (+ synchro AD nocturne, + mises à jour), et en option
#  nginx + HTTPS.
#
#  Organisation : chaque version vit dans <dir>/releases/<version> avec son propre
#  environnement Python ; <dir>/current pointe vers la version active. Une mise à
#  jour bascule ce lien, contrôle que le portail répond, et revient à la version
#  précédente (base restaurée) sinon.
#
#  Usage : sudo ./install.sh [options]      (sans option : questions interactives)
#          sudo ./install.sh --update       (reprend les options de l'installation)
# =============================================================================
set -Eeuo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="# Généré par install.sh (MyApps)"
KEEP_RELEASES=3

SVC="myapps"
INSTALL_DIR=""
DATA_DIR=""
PORT="8080"
DOMAIN=""
HTTPS=0
EMAIL=""
ASSUME_YES=0
PORT_SET=0
UPDATE=0

c_info="\033[1;34m"; c_ok="\033[1;32m"; c_warn="\033[1;33m"; c_err="\033[1;31m"; c_off="\033[0m"
[[ -t 1 ]] || { c_info=""; c_ok=""; c_warn=""; c_err=""; c_off=""; }
step() { echo -e "\n${c_info}==>${c_off} $*"; }
ok()   { echo -e "  ${c_ok}✓${c_off} $*"; }
warn() { echo -e "  ${c_warn}!${c_off} $*"; }
die()  { echo -e "\n${c_err}Erreur :${c_off} $*" >&2; exit 1; }
trap 'echo -e "\n${c_err}Échec à la ligne $LINENO (commande : $BASH_COMMAND).${c_off}" >&2' ERR

usage() {
  cat <<EOF
MyApps — installation et mise à jour sur Debian / Ubuntu

Usage : sudo $0 [options]

  --domain NOM        Nom d'hôte public (ex. portail.exemple.fr) : installe nginx
                      en frontal sur le port 80. Sans domaine, l'application écoute
                      directement sur --port, sur toutes les interfaces.
  --https             Certificat Let's Encrypt (certbot) pour --domain, avec
                      redirection HTTP → HTTPS. Le DNS doit pointer vers la machine
                      et le port 80 doit être joignable depuis Internet.
  --email ADRESSE     E-mail pour Let's Encrypt (alertes d'expiration).
  --port N            Port de l'application (défaut 8080).
  --name NOM          Nom du service systemd et du compte système (défaut myapps).
                      Permet plusieurs installations sur la même machine.
  --dir DOSSIER       Dossier du code (défaut /opt/<nom>).
  --data DOSSIER      Dossier des données (défaut /var/lib/<nom>).
  --update            Mise à jour d'une installation existante avec ses options
                      d'origine (utilisé par le service de mise à jour).
  -y, --yes           Aucune question (valeurs par défaut + options fournies).
  -h, --help          Cette aide.

Exemples :
  sudo ./install.sh
  sudo ./install.sh -y --port 8080
  sudo ./install.sh -y --domain portail.exemple.fr --https --email it@exemple.fr
  sudo ./install.sh --update
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --https)  HTTPS=1; shift ;;
    --email)  EMAIL="${2:-}"; shift 2 ;;
    --port)   PORT="${2:-}"; PORT_SET=1; shift 2 ;;
    --name)   SVC="${2:-}"; shift 2 ;;
    --dir)    INSTALL_DIR="${2:-}"; shift 2 ;;
    --data)   DATA_DIR="${2:-}"; shift 2 ;;
    --update) UPDATE=1; ASSUME_YES=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "option inconnue : $1" ;;
  esac
done

# --- vérifications préalables -------------------------------------------------
[[ $EUID -eq 0 ]] || die "lancez le script en root : sudo $0"
[[ -f "$SRC_DIR/backend/app.py" && -f "$SRC_DIR/requirements.txt" ]] || die "lancez le script depuis le dossier MyApps (backend/ introuvable)."
[[ -r /etc/os-release ]] || die "/etc/os-release introuvable : distribution non reconnue."
# Lu dans un sous-shell : /etc/os-release définit NAME, VERSION… qui écraseraient nos variables.
OS_IDS="$(. /etc/os-release; echo " ${ID:-} ${ID_LIKE:-} ")"
OS_PRETTY="$(. /etc/os-release; echo "${PRETTY_NAME:-Linux}")"
[[ "$OS_IDS" =~ [[:space:]](debian|ubuntu)[[:space:]] ]] || die "distribution non prise en charge ($OS_PRETTY). Debian 12+ ou Ubuntu 22.04+ requis."
[[ -d /run/systemd/system ]] || die "systemd n'est pas actif sur cette machine (conteneur ?)."
[[ "$SVC" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || die "--name : minuscules, chiffres et tirets (ex. myapps)."

VERSION="$(sed -n 's/^## \[\([0-9][0-9.]*[0-9A-Za-z.-]*\)\].*/\1/p' "$SRC_DIR/CHANGELOG.md" | head -1)"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || die "version introuvable dans CHANGELOG.md"

CONF_DIR="/etc/$SVC"
ENV_FILE="$CONF_DIR/$SVC.env"
INSTALL_CONF="$CONF_DIR/install.conf"
UNIT="/etc/systemd/system/$SVC.service"

# Mise à jour : on reprend les options enregistrées à l'installation.
if [[ $UPDATE -eq 1 ]]; then
  [[ -f "$UNIT" ]] && grep -qF "$MARKER" "$UNIT" || die "aucune installation « $SVC » faite par ce script : --update impossible."
  if [[ -f "$INSTALL_CONF" ]]; then
    while IFS='=' read -r k v; do
      case "$k" in
        PORT) PORT="$v" ;; DOMAIN) DOMAIN="$v" ;; HTTPS) HTTPS="$v" ;; EMAIL) EMAIL="$v" ;;
        INSTALL_DIR) INSTALL_DIR="$v" ;; DATA_DIR) DATA_DIR="$v" ;;
      esac
    done < <(grep -E '^[A-Z_]+=' "$INSTALL_CONF")
  else
    # Installation 1.0.x (avant install.conf) : options relues dans les fichiers générés.
    PORT="$(sed -n 's/.*--port \([0-9]*\).*/\1/p' "$UNIT" | head -1)"
    INSTALL_DIR="$(sed -n 's/^WorkingDirectory=\(.*\)\/backend$/\1/p' "$UNIT" | head -1)"
    INSTALL_DIR="${INSTALL_DIR%/current}"
    DATA_DIR="$(sed -n 's/^MYAPPS_DATA_DIR=//p' "$ENV_FILE" | head -1)"
    SITE_OLD="/etc/nginx/sites-available/$SVC.conf"
    if [[ -f "$SITE_OLD" ]]; then
      DOMAIN="$(sed -n 's/^ *server_name \([^ ;]*\);.*/\1/p' "$SITE_OLD" | head -1)"
      grep -q "managed by Certbot" "$SITE_OLD" && HTTPS=1
    fi
  fi
fi

ask() {  # ask "Question" "défaut" -> REPLY
  local q="$1" def="${2:-}"
  if [[ $ASSUME_YES -eq 1 || ! -t 0 ]]; then REPLY="$def"; return; fi
  read -r -p "  $q${def:+ [$def]} : " REPLY || true
  REPLY="${REPLY:-$def}"
}
ask_yn() {  # ask_yn "Question" o|n -> code retour
  local q="$1" def="${2:-n}"
  ask "$q (o/n)" "$def"
  [[ "$REPLY" =~ ^[oOyY] ]]
}

echo -e "${c_info}MyApps $VERSION${c_off} — $( [[ $UPDATE -eq 1 ]] && echo "mise à jour" || echo "installation") sur $OS_PRETTY"

if [[ $ASSUME_YES -eq 0 && -t 0 ]]; then
  step "Configuration (Entrée = valeur proposée)"
  if [[ -z "$DOMAIN" ]]; then
    echo "  Avec un nom de domaine, nginx est installé en frontal (port 80, HTTPS possible)."
    echo "  Sans domaine, le portail est servi directement sur un port (ex. http://IP:8080)."
    ask "Nom de domaine (vide = aucun)" ""
    DOMAIN="$REPLY"
  fi
  if [[ -n "$DOMAIN" && $HTTPS -eq 0 ]]; then
    ask_yn "Activer HTTPS avec Let's Encrypt pour $DOMAIN" "o" && HTTPS=1
  fi
  if [[ $HTTPS -eq 1 && -z "$EMAIL" ]]; then
    ask "E-mail pour Let's Encrypt (vide = aucun)" ""
    EMAIL="$REPLY"
  fi
  if [[ $PORT_SET -eq 0 ]]; then
    ask "Port interne de l'application" "$PORT"
    PORT="$REPLY"
  fi
fi

[[ "$PORT" =~ ^[0-9]+$ && "$PORT" -ge 1024 && "$PORT" -le 65535 ]] || die "--port : nombre entre 1024 et 65535."
[[ -z "$DOMAIN" || "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "--domain invalide : $DOMAIN"
[[ "$HTTPS" =~ ^[01]$ ]] || die "valeur HTTPS invalide : $HTTPS"
[[ $HTTPS -eq 0 || -n "$DOMAIN" ]] || die "--https nécessite --domain."
[[ "$EMAIL" =~ ^[^[:space:]\"\']*$ ]] || die "--email invalide."

INSTALL_DIR="${INSTALL_DIR:-/opt/$SVC}"
DATA_DIR="${DATA_DIR:-/var/lib/$SVC}"
for d in "$INSTALL_DIR" "$DATA_DIR"; do
  [[ "$d" == /* && "$d" =~ ^/[^/]+/.+ && "$d" != *..* && "$d" =~ ^[A-Za-z0-9/._-]+$ ]] || die "dossier refusé : $d (chemin absolu à au moins 2 niveaux attendu, ex. /opt/myapps)."
done
RELEASES="$INSTALL_DIR/releases"
CURRENT="$INSTALL_DIR/current"
USE_NGINX=0; [[ -n "$DOMAIN" ]] && USE_NGINX=1
BIND="0.0.0.0"; [[ $USE_NGINX -eq 1 ]] && BIND="127.0.0.1"

# Ne jamais écraser un service homonyme qui n'a pas été créé par ce script.
if [[ -f "$UNIT" ]] && ! grep -qF "$MARKER" "$UNIT"; then
  die "un service « $SVC » existe déjà et n'a pas été installé par ce script. Choisissez un autre nom : --name autre-nom"
fi
UPGRADE=0; [[ -f "$UNIT" ]] && UPGRADE=1

# Port déjà pris par autre chose que notre propre service ?
if command -v ss >/dev/null && ss -ltnH "( sport = :$PORT )" 2>/dev/null | grep -q .; then
  if [[ $UPGRADE -eq 0 ]] || ! systemctl is-active --quiet "$SVC"; then
    die "le port $PORT est déjà utilisé. Choisissez-en un autre : --port N"
  fi
fi

echo
echo "  Service   : $SVC $( [[ $UPGRADE -eq 1 ]] && echo '(mise à jour)' || echo '(nouvelle installation)')"
echo "  Version   : $VERSION"
echo "  Code      : $INSTALL_DIR"
echo "  Données   : $DATA_DIR"
echo "  Accès     : $( [[ $USE_NGINX -eq 1 ]] && echo "http$( [[ $HTTPS -eq 1 ]] && echo s)://$DOMAIN (nginx → 127.0.0.1:$PORT)" || echo "http://<ip>:$PORT")"
if [[ $ASSUME_YES -eq 0 && -t 0 ]]; then ask_yn "Continuer" "o" || die "annulé."; fi

# --- paquets système ----------------------------------------------------------
step "Paquets système"
export DEBIAN_FRONTEND=noninteractive
PKGS=(python3 python3-venv python3-pip ca-certificates curl sqlite3)
[[ $USE_NGINX -eq 1 ]] && PKGS+=(nginx)
[[ $HTTPS -eq 1 ]] && PKGS+=(certbot python3-certbot-nginx)
MISSING=()
for p in "${PKGS[@]}"; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || MISSING+=("$p")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends "${MISSING[@]}" >/dev/null
  ok "installés : ${MISSING[*]}"
else
  ok "déjà présents : ${PKGS[*]}"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10 ou plus récent requis (trouvé : $(python3 -V 2>&1))."
ok "$(python3 -V)"

# --- compte système -----------------------------------------------------------
step "Compte système « $SVC »"
if id "$SVC" >/dev/null 2>&1; then
  ok "déjà présent"
else
  useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$SVC"
  ok "créé"
fi

# --- sauvegarde avant mise à jour ----------------------------------------------
DB="$DATA_DIR/myapps.db"
BK=""
if [[ $UPGRADE -eq 1 && -f "$DB" ]]; then
  step "Sauvegarde de la base"
  mkdir -p "$DATA_DIR/backups"
  BK="$DATA_DIR/backups/myapps-$(date +%Y%m%d-%H%M%S).db"
  sqlite3 "$DB" ".backup '$BK'"          # copie cohérente (base en WAL)
  chown "$SVC:$SVC" "$DATA_DIR/backups" "$BK"; chmod 640 "$BK"
  ls -1t "$DATA_DIR"/backups/myapps-*.db 2>/dev/null | tail -n +6 | xargs -r rm -f   # garde les 5 dernières
  ok "$BK"
fi

install_venv() {  # install_venv <dossier de version>
  local rel="$1"
  python3 -m venv "$rel/.venv"
  "$rel/.venv/bin/pip" install -q --disable-pip-version-check --upgrade pip
  if [[ -f "$rel/requirements.lock" ]]; then
    # Versions exactes et empreintes vérifiées : une dépendance publiée entre-temps
    # ne peut pas casser l'installation.
    "$rel/.venv/bin/pip" install -q --disable-pip-version-check --require-hashes --no-deps -r "$rel/requirements.lock"
  else
    "$rel/.venv/bin/pip" install -q --disable-pip-version-check -r "$rel/requirements.txt"
  fi
}

# --- ancienne organisation (1.0.x : code à plat dans <dir>) ----------------------
mkdir -p "$INSTALL_DIR"
if [[ -d "$INSTALL_DIR/backend" && ! -L "$CURRENT" ]]; then
  step "Passage à l'organisation par versions"
  OLDV="$(sed -n 's/^## \[\([0-9][0-9.]*[0-9A-Za-z.-]*\)\].*/\1/p' "$INSTALL_DIR/CHANGELOG.md" 2>/dev/null | head -1)"
  OLDV="${OLDV:-ancienne}"
  mkdir -p "$RELEASES/$OLDV"
  find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 ! -name releases -exec mv -t "$RELEASES/$OLDV" {} +
  rm -rf "$RELEASES/$OLDV/.venv"          # un venv déplacé ne fonctionne plus : on le recrée
  install_venv "$RELEASES/$OLDV"
  ln -sfn "releases/$OLDV" "$CURRENT"
  ok "version $OLDV déplacée dans releases/$OLDV (retour arrière possible)"
fi

PREV=""
[[ -L "$CURRENT" ]] && PREV="$(readlink -f "$CURRENT")"

# --- code ---------------------------------------------------------------------
step "Copie de l'application"
mkdir -p "$RELEASES"
REL="$RELEASES/$VERSION"
if [[ -n "$PREV" && "$(realpath -m "$REL")" == "$PREV" ]]; then
  REL="$RELEASES/$VERSION+$(date +%Y%m%d%H%M%S)"     # réinstallation de la version active
fi
rm -rf "$REL"
mkdir -p "$REL"
tar -C "$SRC_DIR" \
    --exclude=.venv --exclude=data --exclude=dist --exclude=__pycache__ --exclude=.github \
    --exclude=.pytest_cache --exclude='*.pyc' --exclude=.git --exclude=node_modules \
    -cf - . | tar -C "$REL" -xf -
chown -R root:root "$REL"
chmod -R u=rwX,go=rX "$REL"
ok "$REL"

step "Environnement Python et dépendances"
install_venv "$REL"
ok "$("$REL/.venv/bin/python" -c 'import fastapi, sqlmodel; print("fastapi", fastapi.__version__, "· sqlmodel", sqlmodel.__version__)')"

# --- données et configuration ---------------------------------------------------
step "Données et configuration"
mkdir -p "$DATA_DIR/uploads"
chown -R "$SVC:$SVC" "$DATA_DIR"
chmod 750 "$DATA_DIR"

mkdir -p "$CONF_DIR"
JWT_SECRET=""; EXTRA=""
if [[ -f "$ENV_FILE" ]]; then
  JWT_SECRET="$(sed -n 's/^JWT_SECRET=//p' "$ENV_FILE" | head -1)"
  EXTRA="$(grep -E '^MYAPPS_UPDATE_(REPO|MANIFEST_URL|PUBKEY)=' "$ENV_FILE" || true)"   # surcharges conservées
fi
[[ ${#JWT_SECRET} -ge 32 ]] || JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
cat > "$ENV_FILE" <<EOF
$MARKER — relancer install.sh réécrit ce fichier (JWT_SECRET est conservé).
JWT_SECRET=$JWT_SECRET
MYAPPS_DATA_DIR=$DATA_DIR
DATABASE_URL=sqlite:///$DB
# Durée de session : 12 h, ou 30 jours avec « Mémoriser la connexion »
JWT_EXPIRE_HOURS=12
JWT_REMEMBER_DAYS=30
# Mises à jour (service $SVC-update)
MYAPPS_INSTALL_DIR=$INSTALL_DIR
MYAPPS_SERVICE=$SVC
MYAPPS_UPDATER=1
EOF
[[ -n "$EXTRA" ]] && echo "$EXTRA" >> "$ENV_FILE"
cat > "$INSTALL_CONF" <<EOF
$MARKER — options de l'installation, relues par « install.sh --update ».
PORT=$PORT
DOMAIN=$DOMAIN
HTTPS=$HTTPS
EMAIL=$EMAIL
INSTALL_DIR=$INSTALL_DIR
DATA_DIR=$DATA_DIR
EOF
chown root:"$SVC" "$CONF_DIR" "$ENV_FILE"
chmod 750 "$CONF_DIR"; chmod 640 "$ENV_FILE"; chmod 600 "$INSTALL_CONF"
ok "$ENV_FILE"

# --- services systemd -----------------------------------------------------------
step "Services systemd"
PROXY_OPTS=""
[[ $USE_NGINX -eq 1 ]] && PROXY_OPTS=" --proxy-headers --forwarded-allow-ips 127.0.0.1"
cat > "$UNIT" <<EOF
$MARKER
[Unit]
Description=MyApps — portail d'applications ($SVC)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC
Group=$SVC
WorkingDirectory=$CURRENT/backend
EnvironmentFile=$ENV_FILE
ExecStart=$CURRENT/.venv/bin/uvicorn app:app --host $BIND --port $PORT --no-server-header$PROXY_OPTS
Restart=on-failure
RestartSec=3
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/$SVC-adsync.service" <<EOF
$MARKER
[Unit]
Description=MyApps ($SVC) — synchro nocturne des groupes Active Directory

[Service]
Type=oneshot
User=$SVC
Group=$SVC
WorkingDirectory=$CURRENT/backend
EnvironmentFile=$ENV_FILE
ExecStart=$CURRENT/.venv/bin/python ad_refresh.py
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR
EOF

cat > "/etc/systemd/system/$SVC-adsync.timer" <<EOF
$MARKER
[Unit]
Description=MyApps ($SVC) — synchro AD chaque nuit (sans effet si l'AD est désactivé)

[Timer]
OnCalendar=*-*-* 03:30
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Mises à jour : vérification quotidienne (installation seulement en mode « automatique »)
# et installation à la demande depuis l'administration (fichier update-request).
for kind in auto apply-request; do
  unit_name="$SVC-update"; [[ $kind == apply-request ]] && unit_name="$SVC-update-request"
  cat > "/etc/systemd/system/$unit_name.service" <<EOF
$MARKER
[Unit]
Description=MyApps ($SVC) — mises à jour ($kind)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
WorkingDirectory=$CURRENT/backend
ExecStart=$CURRENT/.venv/bin/python updater.py $kind
TimeoutStartSec=45min
EOF
done

cat > "/etc/systemd/system/$SVC-update.timer" <<EOF
$MARKER
[Unit]
Description=MyApps ($SVC) — recherche de mises à jour chaque nuit

[Timer]
OnCalendar=*-*-* 03:00
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "/etc/systemd/system/$SVC-update.path" <<EOF
$MARKER
[Unit]
Description=MyApps ($SVC) — demande d'installation depuis l'administration

[Path]
PathExists=$DATA_DIR/update-request
Unit=$SVC-update-request.service

[Install]
WantedBy=paths.target
EOF

health_version() {  # version servie sur le port, vide si l'application ne répond pas
  curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null \
    | python3 -c 'import sys, json; print(json.load(sys.stdin).get("version", ""))' 2>/dev/null || true
}
wait_version() {  # wait_version <version attendue> : 0 si servie dans les 60 s
  for _ in $(seq 1 120); do
    [[ "$(health_version)" == "$1" ]] && return 0
    sleep 0.5
  done
  return 1
}

# Bascule atomique du lien « current » vers la nouvelle version.
ln -sfn "releases/$(basename "$REL")" "$CURRENT.new"
mv -Tf "$CURRENT.new" "$CURRENT"

systemctl daemon-reload
systemctl enable --quiet "$SVC.service" "$SVC-adsync.timer" "$SVC-update.timer" "$SVC-update.path"
systemctl restart "$SVC.service"
systemctl start "$SVC-adsync.timer" "$SVC-update.timer"
systemctl restart "$SVC-update.path"
ok "$SVC.service, $SVC-adsync.timer, $SVC-update.timer, $SVC-update.path"

echo -n "  Démarrage de la version $VERSION"
if wait_version "$VERSION"; then
  echo; ok "application en ligne"
else
  echo
  journalctl -u "$SVC" -n 30 --no-pager || true
  if [[ -n "$PREV" && -d "$PREV" ]]; then
    warn "la version $VERSION ne répond pas : retour à $(basename "$PREV")"
    ln -sfn "releases/$(basename "$PREV")" "$CURRENT.new"
    mv -Tf "$CURRENT.new" "$CURRENT"
    systemctl stop "$SVC.service"
    if [[ -n "$BK" && -f "$BK" ]]; then
      rm -f "$DB-wal" "$DB-shm"
      sqlite3 "$DB" ".restore '$BK'"
      chown "$SVC:$SVC" "$DB"; chmod 640 "$DB"
      ok "base restaurée depuis $BK"
    fi
    rm -rf "$REL"
    systemctl start "$SVC.service"
    PREV_V="$(sed -n 's/^## \[\([0-9][0-9.]*[0-9A-Za-z.-]*\)\].*/\1/p' "$PREV/CHANGELOG.md" | head -1)"
    wait_version "$PREV_V" && ok "version $PREV_V rétablie" || warn "la version précédente ne répond pas non plus"
    die "mise à jour vers $VERSION annulée (journal ci-dessus)."
  fi
  die "l'application ne répond pas sur le port $PORT (journal ci-dessus)."
fi

# Ménage : on garde la version active et les plus récentes (retour arrière manuel possible).
mapfile -t OLD < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%T@ %p\n' \
                   | sort -rn | cut -d' ' -f2- | grep -vxF "$REL" | tail -n +"$KEEP_RELEASES")
for d in "${OLD[@]}"; do rm -rf "$d"; done
[[ ${#OLD[@]} -gt 0 ]] && ok "${#OLD[@]} ancienne(s) version(s) supprimée(s)"

# --- nginx + HTTPS --------------------------------------------------------------
if [[ $USE_NGINX -eq 1 ]]; then
  step "nginx ($DOMAIN)"
  SITE="/etc/nginx/sites-available/$SVC.conf"
  if [[ -f "$SITE" ]] && ! grep -qF "$MARKER" "$SITE"; then
    die "$SITE existe déjà et n'a pas été créé par ce script."
  fi
  # Une config déjà passée par certbot (HTTPS) est conservée telle quelle.
  if [[ -f "$SITE" ]] && grep -q "managed by Certbot" "$SITE"; then
    ok "configuration HTTPS existante conservée"
  else
    cat > "$SITE" <<EOF
$MARKER
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    client_max_body_size 10m;      # envoi d'images (8 Mo max côté application)

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF
  fi
  ln -sf "$SITE" "/etc/nginx/sites-enabled/$SVC.conf"
  nginx -t -q || die "configuration nginx invalide (voir ci-dessus)."
  systemctl enable --quiet nginx
  systemctl reload nginx || systemctl restart nginx
  ok "$SITE"

  if [[ $HTTPS -eq 1 ]] && ! grep -q "managed by Certbot" "$SITE"; then
    step "Certificat HTTPS (Let's Encrypt)"
    CB_MAIL=(--register-unsafely-without-email)
    [[ -n "$EMAIL" ]] && CB_MAIL=(-m "$EMAIL")
    if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos "${CB_MAIL[@]}" --redirect; then
      ok "HTTPS actif, renouvellement automatique par certbot.timer"
    else
      HTTPS=0
      warn "échec de certbot : le portail reste accessible en HTTP."
      warn "Vérifiez le DNS de $DOMAIN et l'ouverture du port 80, puis : sudo certbot --nginx -d $DOMAIN"
    fi
  fi
fi

# --- pare-feu -------------------------------------------------------------------
if [[ $UPDATE -eq 0 ]] && command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  step "Pare-feu (ufw)"
  if [[ $USE_NGINX -eq 1 ]]; then ufw allow "Nginx Full" >/dev/null; ok "Nginx Full autorisé"
  else ufw allow "$PORT/tcp" >/dev/null; ok "port $PORT/tcp autorisé"; fi
fi

# --- résumé ---------------------------------------------------------------------
if [[ $USE_NGINX -eq 1 ]]; then
  URL="http$( [[ $HTTPS -eq 1 ]] && echo s)://$DOMAIN"
else
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  URL="http://${IP:-<ip-de-la-machine>}:$PORT"
fi

echo
echo -e "${c_ok}$( [[ $UPGRADE -eq 1 ]] && echo "Mise à jour terminée" || echo "Installation terminée") : MyApps $VERSION.${c_off}"
[[ $UPDATE -eq 1 ]] && exit 0
echo
echo "  Portail : $URL"
if [[ -f "$DATA_DIR/setup-code" ]]; then
  echo
  echo -e "  ${c_warn}Code d'installation : $(cat "$DATA_DIR/setup-code")${c_off}"
  echo "  Ouvrez le portail : l'assistant de premier démarrage le demande pour créer"
  echo "  le compte administrateur. Il est détruit dès que ce compte existe."
  echo "  Pour le relire : sudo cat $DATA_DIR/setup-code"
fi
echo
echo "  Journal       : sudo journalctl -u $SVC -f"
echo "  Redémarrer    : sudo systemctl restart $SVC"
echo "  Mises à jour  : Administration › Mises à jour (notification seule par défaut)"
echo
