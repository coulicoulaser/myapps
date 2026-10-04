# MyApps — portail d'applications

Portail d'applications internes en libre-service : des **dashboards** de tuiles, rangées
par **sections**, visibles selon les **groupes** de chaque utilisateur. Thème sombre
« liquid glass » à vos couleurs, responsive.

- Comptes locaux, **Active Directory / LDAP** (groupes synchronisés) et **SSO OIDC**
  (Authentik, Keycloak, Entra ID…)
- Droits par groupe + exceptions par utilisateur (Refuser > Autoriser > groupe)
- Recherche d'applications (touche `/`) + recherche web au choix
- Météo du site de l'utilisateur (Open-Meteo, sans clé)
- Logo automatique des applications, envoi d'images, glisser-déposer des tuiles
- **Assistant de premier démarrage** : identité, fonds, premier dashboard, premières applications

## Installation (Debian 12+ / Ubuntu 22.04+)

Copier le dossier (ou l'archive `dist/myapps-x.y.z.tar.gz`) sur la machine, puis :

```bash
sudo ./install.sh
```

Le script pose quelques questions (nom de domaine, HTTPS, port) puis installe tout :
paquets système (Python 3.10+, venv, sqlite3, et nginx/certbot si besoin), compte système
dédié, environnement Python isolé, service systemd durci et synchro AD nocturne.

À la fin, il affiche l'**adresse du portail** et un **code d'installation**. Ouvrez
l'adresse : l'assistant demande ce code pour créer le compte administrateur, puis guide
la personnalisation. Le code est détruit dès que l'administrateur existe.

### Sans questions

```bash
# Accès direct par IP et port
sudo ./install.sh -y --port 8080

# Derrière nginx avec HTTPS Let's Encrypt (DNS pointant vers la machine, port 80 ouvert)
sudo ./install.sh -y --domain portail.exemple.fr --https --email it@exemple.fr
```

| Option | Rôle | Défaut |
|---|---|---|
| `--domain NOM` | nginx en frontal sur le port 80 pour ce nom | aucun (accès direct au port) |
| `--https` | certificat Let's Encrypt + redirection HTTP → HTTPS | non |
| `--email` | e-mail Let's Encrypt | aucun |
| `--port N` | port de l'application | 8080 |
| `--listen ADRESSE` | adresse d'écoute sans nginx géré (reverse proxy existant) | `0.0.0.0` |
| `--adopt` | reprendre un service homonyme installé autrement (sauvegardé, secret JWT repris) | — |
| `--name NOM` | nom du service et du compte système (plusieurs installations possibles) | `myapps` |
| `--dir` / `--data` | dossiers du code / des données | `/opt/<nom>` / `/var/lib/<nom>` |
| `-y` | aucune question | — |

Le script refuse d'écraser un service ou un site nginx homonyme qu'il n'a pas créé (sauf `--adopt` pour un service).

### Emplacements

| Quoi | Où |
|---|---|
| Code + environnement Python | `/opt/myapps/releases/<version>`, version active : `/opt/myapps/current` |
| Base SQLite, images envoyées, sauvegardes | `/var/lib/myapps` |
| Configuration (secret JWT, chemins) | `/etc/myapps/myapps.env`, options d'installation : `/etc/myapps/install.conf` |
| Services | `myapps.service`, `myapps-adsync.timer` (03:30), `myapps-update.timer` (03:00), `myapps-update.path` |
| nginx (si domaine) | `/etc/nginx/sites-available/myapps.conf` |

### Mises à jour

**Depuis l'interface** (Administration › Mises à jour). Chaque nuit, MyApps consulte
les versions publiées sur [github.com/coulicoulaser/myapps](https://github.com/coulicoulaser/myapps/releases).

| Mode | Comportement |
|---|---|
| Notification seule (défaut) | une pastille prévient les administrateurs ; installation d'un clic |
| Automatique la nuit | installation entre 3 h et 4 h (jamais pour une version majeure) |
| Désactivées | aucune connexion au serveur de mises à jour |

Déroulé d'une installation : vérification de la signature du manifeste et de l'empreinte
de l'archive → sauvegarde de la base → installation dans `/opt/myapps/releases/<version>`
→ bascule du lien `current` → contrôle que le portail répond → sinon, retour automatique
à la version précédente avec la base d'avant.

**À la main** : `sudo ./install.sh --update` depuis le dossier d'une nouvelle version
(options reprises de `/etc/myapps/install.conf`), ou le service directement :

```bash
sudo systemctl start myapps-update.service    # vérifie (et installe en mode automatique)
sudo journalctl -u myapps-update -u myapps-update-request
```

Retour manuel à une version gardée : `sudo ln -sfn releases/<version> /opt/myapps/current && sudo systemctl restart myapps`
(et restaurer la sauvegarde de `/var/lib/myapps/backups` si le schéma de la base a changé).

### Exploitation

```bash
sudo journalctl -u myapps -f          # journal
sudo systemctl restart myapps         # redémarrer
sudo cat /var/lib/myapps/setup-code   # code d'installation (tant qu'aucun admin n'existe)
```

Sauvegarde à chaud de la base (elle est en mode WAL : ne pas la copier avec `cp`) :

```bash
sudo sqlite3 /var/lib/myapps/myapps.db ".backup '/root/myapps-$(date +%F).db'"
```

Désinstallation :

```bash
sudo systemctl disable --now myapps myapps-adsync.timer myapps-update.timer myapps-update.path
sudo rm -f /etc/systemd/system/myapps.service /etc/systemd/system/myapps-adsync.* /etc/systemd/system/myapps-update*
sudo rm -f /etc/nginx/sites-enabled/myapps.conf /etc/nginx/sites-available/myapps.conf
sudo systemctl daemon-reload
sudo rm -rf /opt/myapps /etc/myapps        # garder /var/lib/myapps si besoin des données
sudo userdel myapps
```

## Configuration

Tout se règle dans l'interface, **Administration** :

- **Réglages** : identité (nom, logo, couleur), fonds d'écran, Active Directory, SSO,
  relance de l'assistant.
- **Applications**, **Sections**, **Dashboards** : contenu du portail.
- **Groupes** : « Tout le monde » (intégré : tous les connectés), groupes locaux, groupes AD.
- **Utilisateurs** : comptes locaux, rôle, groupes locaux, exceptions.
- **Sites (météo)** : lieux affichés dans la barre du haut.

### Active Directory

Serveur, Base DN, compte de service (bind), filtres. À chaque connexion et chaque nuit,
nom, e-mail, groupes AD et site sont relus dans l'annuaire. **Groupes › Synchroniser
depuis l'AD** importe la liste des groupes ; 👥 crée les comptes des membres d'un groupe.

### SSO (OpenID Connect)

Créer chez le fournisseur une application OIDC (client confidentiel), avec l'URI de
redirection `https://<portail>/api/auth/sso/callback`, puis renseigner Issuer, Client ID et
Secret dans Réglages. Le claim des groupes (`groups` par défaut) alimente les groupes.

## Modèle de droits

1. Un élément est visible si **un groupe** de l'utilisateur y a accès (« Tout le monde »
   compte pour tous).
2. **Exceptions par utilisateur** : Refuser > Autoriser > règle de groupe.
3. Les **administrateurs** voient tout et gèrent le portail.

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
cd backend
JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))") \
  ../.venv/bin/uvicorn app:app --reload --port 8080      # données dans ./data
../.venv/bin/python -m pytest -q                          # tests API
```

Parcours navigateur simulé (jsdom, Node 18+) contre un serveur jetable :
`tests-e2e/run.sh` (assistant complet, reprise, utilisateur simple).

- Backend : FastAPI + SQLModel + SQLite (Postgres possible via `DATABASE_URL`).
- Frontend : HTML/CSS/JS sans étape de build (`frontend/`), servi par l'API.
- Version = premier `## [x.y.z]` de `CHANGELOG.md` (affichée dans Administration › Changelog).

## Publier une version

1. Ajouter en tête de `CHANGELOG.md` une section `## [X.Y.Z] — AAAA-MM-JJ` (les notes
   affichées dans l'administration des clients) ; `X.Y.Z-beta.N` pour une pré-version.
2. Si des dépendances ont changé : `scripts/lock.sh` puis les tests.
3. `git commit`, puis `git tag vX.Y.Z && git push origin main vX.Y.Z`.
4. GitHub Actions (`release.yml`) lance les tests, construit l'archive, signe le
   manifeste et publie la version. Les installations la voient à leur prochaine vérification.

Mise en place, une seule fois, dans les réglages du dépôt GitHub :
- **Environments › New environment** `release`, avec **Required reviewers** (vous) : la clé
  de signature n'est utilisée qu'après votre validation de la publication ;
- dans cet environnement, **secret** `MYAPPS_SIGNING_KEY` = contenu de la clé secrète
  (`MYAPPS-SIGNING-KEY-V1:…`) ;
- facultatif : **variable** `MYAPPS_MIN_FROM` = version minimale depuis laquelle la mise à
  jour automatique est permise (pour une version qui exige une étape manuelle).

La clé publique correspondante est `backend/update_key.pub`. Une nouvelle paire :
`python3 scripts/sign-release.py keygen <dossier>` ; changer de clé impose une
installation manuelle de la version qui embarque la nouvelle clé publique.

Construire et signer localement (sans publier) :

```bash
scripts/build-release.sh
MYAPPS_SIGNING_KEY_FILE=/chemin/myapps-release.key python3 scripts/sign-release.py sign
minisign -Vm dist/latest.json -p backend/update_key.pub     # vérification indépendante
```
