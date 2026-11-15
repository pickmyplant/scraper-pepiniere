#!/usr/bin/env bash
# Relevé automatique complet d'une pépinière (toutes les plantes de la BDD), sans Claude.
# Usage : outils/actualiser.sh <pepiniere>   (nom du fichier adapters/<pepiniere>.py)
# Écrit releves/<PREFIXE>-<date>-AUTO.tsv et .json, variétés déjà nettoyées. Ne fait ni commit ni push
# (le relevé programmé .github/workflows/actualiser.yml s'en charge). Code 2 : site bloqué, rien à importer.
set -uo pipefail
cd "$(dirname "$0")/.."
PEP="${1:?pépinière manquante}"
PREFIXE="$(grep -m1 '^PREFIXE' "adapters/$PEP.py" | cut -d'"' -f2)"
ID="$PREFIXE-$(date +%F)-AUTO"
python outils/extraire_lot.py P001 P999 || exit 1
python run.py --pepiniere "$PEP" --plantes data/plantes_P001-P999.csv --releve "$ID" --par "Automatique" --silencieux
CODE=$?
if [ "$CODE" -ne 0 ]; then
  rm -f "releves/$ID.tsv" "releves/$ID.json" "releves/${ID}_anomalies.tsv"
  echo "ARRÊT (code $CODE) : relevé $ID incomplet, non gardé."; exit "$CODE"
fi
python outils/corriger_offres.py --releve "releves/$ID.tsv" --actif || exit 1
python outils/resume.py "releves/$ID.tsv"
