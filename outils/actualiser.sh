#!/usr/bin/env bash
# Relevé automatique complet d'une pépinière (toutes les plantes de la BDD), sans Claude.
# Usage : outils/actualiser.sh <pepiniere> [partie total]
#   <pepiniere> : nom du fichier adapters/<pepiniere>.py
#   partie/total : pour les sites trop longs, ne relever qu'une plante sur <total> (ex. 1 3, 2 3, 3 3)
# Écrit releves/<PREFIXE>-<date>-AUTO[partie].tsv et .json, variétés déjà nettoyées, statut Actif.
# Ne fait ni commit ni push (.github/workflows/actualiser.yml s'en charge). Code 2 : site bloqué, rien à importer.
set -uo pipefail
cd "$(dirname "$0")/.."
PEP="${1:?pépinière manquante}"; PARTIE="${2:-1}"; TOTAL="${3:-1}"
PREFIXE="$(grep -m1 '^PREFIXE' "adapters/$PEP.py" | cut -d'"' -f2)"
ID="$PREFIXE-$(date +%F)-AUTO"; [ "$TOTAL" -gt 1 ] && ID="$ID$PARTIE"
python outils/extraire_lot.py P001 P999 > /dev/null || exit 1   # bilan non affiché (liste des numéros libres très longue)
CSV="data/plantes_P001-P999.csv"
if [ "$TOTAL" -gt 1 ]; then
  python - "$CSV" "$PARTIE" "$TOTAL" <<'PY' || exit 1
import csv, sys
chemin, partie, total = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
lignes = list(csv.reader(open(chemin, encoding="utf-8")))
garde = [lignes[0]] + [l for i, l in enumerate(lignes[1:]) if i % total == partie - 1]
csv.writer(open(chemin, "w", encoding="utf-8", newline="")).writerows(garde)
print("Partie %d/%d : %d plantes" % (partie, total, len(garde) - 1))
PY
fi
python run.py --pepiniere "$PEP" --plantes "$CSV" --releve "$ID" --par "Automatique" --silencieux
CODE=$?
if [ "$CODE" -ne 0 ]; then
  rm -f "releves/$ID.tsv" "releves/$ID.json" "releves/${ID}_anomalies.tsv"
  echo "ARRÊT (code $CODE) : relevé $ID incomplet, non gardé."; exit "$CODE"
fi
python outils/corriger_offres.py --releve "releves/$ID.tsv" --actif || exit 1
python outils/resume.py "releves/$ID.tsv"
