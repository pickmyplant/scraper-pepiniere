#!/usr/bin/env bash
# Relevé automatique complet d'une pépinière (toutes les plantes de la BDD), sans Claude, par lots de 100 plantes.
# Usage : outils/actualiser.sh <pepiniere> [partie total]
#   <pepiniere> : nom du fichier adapters/<pepiniere>.py
#   partie/total : pour les sites trop longs, ce job ne fait qu'un lot sur <total> (lots partie, partie+total, ...)
# Chaque lot réussi devient un relevé à part : releves/auto_ok/<PREFIXE>-<date>-AUTO-L<n>.tsv et .json
# (variétés nettoyées, statut Actif), envoyé même si un lot suivant échoue ou si le job est coupé.
# Lot en erreur : on passe au suivant. Site bloqué (code 2 : 403, 429, captcha) : arrêt des lots restants.
# Les lots ratés sont listés à la fin (relance à la main : outils/lot.sh <ID> data/plantes_<lot>.csv).
# Ne fait ni commit ni push (.github/workflows/actualiser.yml s'en charge). Code 0 : tous les lots réussis.
set -uo pipefail
cd "$(dirname "$0")/.."
PEP="${1:?pépinière manquante}"; PARTIE="${2:-1}"; TOTAL="${3:-1}"
TAILLE_LOT="${TAILLE_LOT:-100}"   # modifiable pour un essai
PREFIXE="$(grep -m1 '^PREFIXE' "adapters/$PEP.py" | cut -d'"' -f2)"
DATE="$(date +%F)"
mkdir -p releves/auto_ok
python outils/extraire_lot.py P001 P999999 > /dev/null || exit 1   # bilan non affiché (liste des numéros libres très longue)

# Découpe en lots de 100 plantes (dans l'ordre de l'export) : data/auto_<PEP>_L<n>.csv ; affiche « n premier dernier »
LOTS="$(python - "data/plantes_P001-P999999.csv" "$PEP" "$TAILLE_LOT" "$PARTIE" "$TOTAL" <<'PY'
import csv, sys
chemin, pep, taille, partie, total = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
lignes = list(csv.reader(open(chemin, encoding="utf-8")))
tete, corps = lignes[0], lignes[1:]
for n, i in enumerate(range(0, len(corps), taille), start=1):
    if (n - 1) % total != partie - 1: continue
    lot = corps[i:i + taille]
    csv.writer(open(f"data/auto_{pep}_L{n}.csv", "w", encoding="utf-8", newline="")).writerows([tete] + lot)
    print(n, lot[0][0], lot[-1][0])
PY
)" || exit 1

OK=(); RATES=(); BLOQUE=""
while read -r N PREMIER DERNIER; do
  [ -n "$N" ] || continue
  ID="$PREFIXE-$DATE-AUTO-L$N"; CSV="data/auto_${PEP}_L$N.csv"
  if [ -n "$BLOQUE" ]; then RATES+=("$ID ($PREMIER-$DERNIER) : non lancé, site bloqué"); continue; fi
  echo "=== Lot $N : $PREMIER à $DERNIER ($ID)"
  python run.py --pepiniere "$PEP" --plantes "$CSV" --releve "$ID" --par "Automatique" --silencieux
  CODE=$?
  if [ "$CODE" -eq 0 ] && python outils/corriger_offres.py --releve "releves/$ID.tsv" --actif; then
    python outils/resume.py "releves/$ID.tsv"
    mv "releves/$ID.tsv" "releves/$ID.json" releves/auto_ok/
    rm -f "releves/${ID}_anomalies.tsv"
    OK+=("$ID")
  else
    rm -f "releves/$ID.tsv" "releves/$ID.json" "releves/${ID}_anomalies.tsv"
    RATES+=("$ID ($PREMIER-$DERNIER) : code $CODE")
    [ "$CODE" -eq 2 ] && BLOQUE=1 && echo "ARRÊT : site bloqué (code 2), lots suivants non lancés."
  fi
done <<< "$LOTS"
rm -f data/auto_"${PEP}"_L*.csv

BILAN="$PEP : ${#OK[@]} lot(s) réussi(s), ${#RATES[@]} raté(s)"
echo "$BILAN"
for r in "${RATES[@]+"${RATES[@]}"}"; do echo "  raté : $r"; done
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  { echo "### $BILAN"; for r in "${RATES[@]+"${RATES[@]}"}"; do echo "- raté : $r"; done; } >> "$GITHUB_STEP_SUMMARY"
fi
[ -n "$BLOQUE" ] && exit 2
[ "${#RATES[@]}" -eq 0 ] || exit 1
