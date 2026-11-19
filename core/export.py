"""Export TSV au format de l'onglet Offres."""
import csv

from core.formatting import COLONNES
from core.lots import regrouper_lots


def ecrire_tsv(chemin, offres):
    """Écrit le relevé ; les lignes « lot de N » sont regroupées (prix d'un plant, colonne Lot)."""
    lignes, _ = regrouper_lots([[str(v).replace("\t", " ").replace("\n", " ") for v in o.ligne()] for o in offres])
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(COLONNES)
        w.writerows(lignes)
