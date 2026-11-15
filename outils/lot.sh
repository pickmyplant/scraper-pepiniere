#!/usr/bin/env bash
# Relevé d'un lot en une commande : relevé silencieux, résumé compact, commit et push.
# Usage : outils/lot.sh <ID_RELEVE> <CSV_PLANTES> [pepiniere] [par]
#   ex. : outils/lot.sh PLF-2026-11-06-A5 data/plantes_P082-P120.csv
# Pépinière : nom du fichier adapters/<nom>.py, ou préfixe de l'ID (ligne PREFIXE = "XXX" dans l'adaptateur).
set -euo pipefail
cd "$(dirname "$0")/.."
ID="${1:?ID du relevé manquant}"; CSV="${2:?CSV des plantes manquant}"
CLE="${3:-${ID%%-*}}"
if [ -f "adapters/$CLE.py" ]; then
  PEP="$CLE"
else
  PEP="$(grep -l "^PREFIXE = \"$CLE\"" adapters/*.py 2>/dev/null | head -1 | xargs -r basename -s .py)"
fi
[ -n "$PEP" ] || { echo "Pépinière inconnue : $CLE (ajouter PREFIXE = \"$CLE\" dans l'adaptateur)" >&2; exit 1; }
PAR="${4:-Claude A}"

set +e
python run.py --pepiniere "$PEP" --plantes "$CSV" --releve "$ID" --par "$PAR" --silencieux
CODE=$?
set -e
if [ "$CODE" -ne 0 ]; then
  echo "ARRÊT (code $CODE) : relevé incomplet, rien n'est commité. Voir le dernier fichier de logs/."
  exit "$CODE"
fi

# Variétés nettoyées et reliées aux fiches avant l'import (sans effet si BDD_EXPORT_URL est absent)
[ -n "${BDD_EXPORT_URL:-}" ] && python outils/corriger_offres.py --releve "releves/$ID.tsv" || echo "Variétés non corrigées (BDD_EXPORT_URL absent ou erreur)."

python outils/resume.py "releves/$ID.tsv"

git add data/equivalences_latin.csv adapters/ "releves/$ID.tsv" "releves/${ID}_anomalies.tsv" "releves/$ID.json" "$CSV"
git commit -q -m "Relevé $ID ($PEP, $(basename "$CSV" .csv))"
# Push sur la branche de la conversation et sur la branche commune (voir outils/publier.sh)
outils/publier.sh || echo "À FAIRE : publier sur la branche commune (outils/publier.sh), conflit à résoudre."
echo "Identifiant à importer : $ID"
