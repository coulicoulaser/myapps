#!/usr/bin/env bash
# Construit l'archive d'une version et son manifeste (non signé) dans dist/ :
#   dist/myapps-<version>.tar.gz   archive installable (install.sh à la racine)
#   dist/latest.json               manifeste lu par les installations (updater.py)
#   dist/notes.md                  notes de version (section du CHANGELOG)
# La version vient du premier « ## [x.y.z] » de CHANGELOG.md.
# Variables : MYAPPS_REPO (défaut coulicoulaser/myapps), MIN_FROM (version minimale
# depuis laquelle la mise à jour automatique est permise, vide = toutes).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${MYAPPS_REPO:-coulicoulaser/myapps}"
VERSION="$(sed -n 's/^## \[\([0-9][0-9.]*[0-9A-Za-z.-]*\)\].*/\1/p' CHANGELOG.md | head -1)"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || { echo "version introuvable dans CHANGELOG.md" >&2; exit 1; }
NAME="myapps-$VERSION"
mkdir -p dist
rm -f dist/myapps-*.tar.gz dist/latest.json dist/latest.json.minisig dist/notes.md

# Archive reproductible : ordre, dates et propriétaires fixés.
EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}"
tar --sort=name --mtime="@$EPOCH" --owner=0 --group=0 --numeric-owner \
    --exclude=./.venv --exclude=./data --exclude=./dist --exclude=./.git --exclude=./.github \
    --exclude=node_modules --exclude=__pycache__ --exclude=.pytest_cache --exclude='*.pyc' \
    --transform "s,^\.,$NAME," -cf - . | gzip -n -9 > "dist/$NAME.tar.gz"

SHA="$(sha256sum "dist/$NAME.tar.gz" | cut -d' ' -f1)"
SIZE="$(stat -c %s "dist/$NAME.tar.gz")"
python3 - "$VERSION" "$REPO" "$SHA" "$SIZE" "${MIN_FROM:-}" <<'PY'
import json, sys, datetime
sys.path.insert(0, "backend")
import changelog
version, repo, sha, size, min_from = sys.argv[1:6]
notes = changelog.section(version)
manifest = {
    "name": "myapps",
    "version": version,
    "published": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
    "archive": {"url": f"https://github.com/{repo}/releases/download/v{version}/myapps-{version}.tar.gz",
                "sha256": sha, "size": int(size)},
    "min_from": min_from,
    "notes": notes,
}
open("dist/latest.json", "w").write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
open("dist/notes.md", "w").write(notes + "\n")
PY
echo "dist/$NAME.tar.gz  ($SIZE octets, sha256 $SHA)"
echo "dist/latest.json   (à signer : python3 scripts/sign-release.py sign)"
