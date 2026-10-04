# Changelog — MyApps

Toutes les évolutions notables sont consignées ici.
Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/), versionnage [SemVer](https://semver.org/lang/fr/) (MAJOR.MINOR.PATCH).

## [1.3.2] — 2026-10-04
### Corrigé
- **Flou qui sautait pendant les animations** (changement de dashboard, entrée dans
  l'administration, étapes de l'assistant) : ces transitions passaient par l'API View
  Transitions du navigateur, qui anime des captures figées où le flou des éléments « verre »
  disparaît, puis revient d'un coup. Les vrais éléments sont maintenant animés directement
  (déplacement seul pour ce qui contient du verre), le flou reste net d'un bout à l'autre et
  l'entrée dans l'administration est fluide.

## [1.3.1] — 2026-10-04
### Corrigé
- **« Signature invalide » juste après une publication** : la recherche de mise à jour lisait
  le manifeste et sa signature par deux redirections « dernière version » de GitHub, que son
  cache pouvait faire pointer vers deux versions différentes pendant quelques minutes. Les deux
  fichiers sont maintenant lus dans le dossier d'une même version. La vérification de
  signature, elle, a bien fait son travail : rien d'incohérent n'a été installé.

## [1.3.0] — 2026-10-04
### Ajouté
- **Animations façon Flutter / Material 3** : effet d'encre au clic sur tuiles, boutons,
  onglets et menus ; tuiles qui apparaissent en cascade avec un léger rebond ; pastille
  d'onglet qui glisse d'un dashboard à l'autre ; fondu enchaîné au changement de dashboard ;
  glissement entre portail et administration et entre les étapes de l'assistant ; boîte de
  dialogue, menus et notifications animés à l'ouverture et à la fermeture.
- Réglage « Animations » (Réglages › Identité) pour les couper ; elles le sont d'office quand
  le système de l'utilisateur demande moins d'animations.

## [1.2.0] — 2026-10-04
### Ajouté
- **Couleur de fond** réglable (en-têtes, voile sur les images, menus), en plus de la couleur
  d'accent : Réglages › Identité et assistant de premier démarrage.
- **Polices** au choix pour les titres et le texte (15 polices Google Fonts, ou police du
  système par défaut, sans aucun appel externe).
- `install.sh --listen ADRESSE` : écoute sur une adresse choisie sans nginx géré par le
  script (derrière un reverse proxy existant).
- `install.sh --adopt` : reprend un service systemd homonyme installé autrement (ancien
  MyApps…) ; ses fichiers sont sauvegardés dans `/etc/<nom>/adopted-<date>/` et son
  `JWT_SECRET` est repris pour garder les sessions ouvertes.

### Corrigé
- `scripts/sign-release.py` sans argument affichait une erreur au lieu de l'aide (vérification
  statique de la CI en échec). Sans effet sur les installations.
- Actions GitHub passées aux versions Node 24 (checkout v5, setup-python v6, setup-node v5).

## [1.1.0] — 2026-10-04
### Ajouté
- **Mises à jour depuis GitHub** (Administration › Mises à jour). Chaque nuit, MyApps
  regarde s'il existe une nouvelle version publiée sur github.com/coulicoulaser/myapps.
  Par défaut il prévient seulement les administrateurs (pastille dans la barre du haut),
  qui installent d'un clic ; mode « automatique la nuit » ou « désactivé » au choix,
  canal stable ou bêta. Les versions majeures demandent toujours une confirmation.
- **Versions signées** : chaque version publiée porte un manifeste signé (format
  minisign) avec l'empreinte SHA-256 de son archive. Une archive modifiée, signée par une
  autre clé ou venue d'ailleurs est refusée ; une version plus ancienne n'est jamais installée.
- **Retour arrière automatique** : chaque version est installée à part
  (`releases/<version>`, lien `current`) ; si le portail ne répond pas après la mise à
  jour, la version précédente et la base sont rétablies. Les 3 dernières versions sont gardées.
- `install.sh --update` : met à jour avec les options de l'installation (`/etc/<nom>/install.conf`).
- Publication par GitHub Actions : tests, archive, signature et version GitHub à
  partir d'un tag `vX.Y.Z` ; tests automatiques à chaque push.

### Modifié
- Dépendances Python installées depuis `requirements.lock` (versions exactes et empreintes
  vérifiées) : une nouvelle version d'une dépendance ne peut plus casser une installation.
- Une installation 1.0.x passe automatiquement à l'organisation par versions.

## [1.0.0] — 2026-10-04
Première version.

### Ajouté
- **Portail** : dashboards de tuiles rangées par sections, fond par dashboard, recherche
  d'applications (touche « / ») et recherche web au choix (Google, DuckDuckGo, Bing, Qwant
  ou aucune), météo du site de l'utilisateur (Open-Meteo), glisser-déposer des tuiles et
  des sections en mode édition.
- **Droits** par groupe, avec le groupe intégré « Tout le monde » (tous les utilisateurs
  connectés), et exceptions par utilisateur (Refuser > Autoriser > groupe).
- **Comptes** locaux, Active Directory / LDAP (groupes et site synchronisés à la connexion
  et chaque nuit) et SSO OpenID Connect. Groupes locaux et groupes AD cohabitent.
- **Administration** : applications (logo automatique, envoi d'images), sections,
  dashboards, groupes, utilisateurs, sites météo, réglages, changelog.
- **Assistant de premier démarrage** : code d'installation, compte administrateur,
  identité (nom, logo, couleur d'accent), fonds d'écran (6 fonds proposés ou vos images),
  premier dashboard, premières applications (18 suggestions). Relançable depuis les réglages.
- **Script d'installation** `install.sh` (Debian 12+ / Ubuntu 22.04+) : paquets système,
  environnement Python isolé, compte système dédié, service systemd durci, synchro AD
  nocturne, nginx + HTTPS Let's Encrypt en option. Relancé depuis une nouvelle version, il
  met à jour l'installation après une sauvegarde de la base.

### Sécurité
- Compte administrateur créé uniquement avec le code d'installation (fichier `setup-code`
  lisible par root et le compte du service, et journal), 10 essais puis 15 min de blocage.
- Un compte local ne s'ouvre jamais par l'annuaire ou le SSO d'un compte homonyme.
- Filtres LDAP échappés, paramètre `state` OIDC vérifié, jeton SSO passé dans le fragment d'URL.
- Liens `javascript:`/`data:` refusés, URL d'images et couleurs validées.
- Images envoyées servies avec une CSP `sandbox`.
- Le dernier administrateur ne peut être ni supprimé, ni désactivé, ni rétrogradé.
