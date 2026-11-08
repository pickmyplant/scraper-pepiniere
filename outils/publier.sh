#!/usr/bin/env bash
# Publie le travail de la branche courante sur la branche commune, pour que toute nouvelle
# conversation reparte avec tous les adaptateurs et tous les relevés.
# Usage : outils/publier.sh   (appelé automatiquement à la fin de outils/lot.sh)
set -uo pipefail
cd "$(dirname "$0")/.."
CIBLE="claude/vibrant-keller-jibr76"
BR="$(git rev-parse --abbrev-ref HEAD)"
for t in 2 4 8 16 0; do
  git fetch -q origin "$CIBLE" || { sleep "$t"; continue; }
  if ! git merge -q --no-edit "origin/$CIBLE" >/dev/null 2>&1; then
    echo "CONFLIT avec $CIBLE ($(git diff --name-only --diff-filter=U | tr '\n' ' ')) : fusion annulée, à résoudre." >&2
    git merge --abort
    exit 1
  fi
  [ "$BR" != "$CIBLE" ] && git push -q origin "HEAD:$BR"
  git push -q origin "HEAD:$CIBLE" && { echo "Publié sur $CIBLE."; exit 0; }
  [ "$t" -eq 0 ] && break
  sleep "$t"   # quelqu'un a poussé entre-temps : on refusionne
done
echo "Publication sur $CIBLE échouée." >&2
exit 1
