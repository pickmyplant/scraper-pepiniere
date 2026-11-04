"""Export TSV au format de l'onglet Offres."""
import csv

from core.formatting import COLONNES


def ecrire_tsv(chemin, offres):
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(COLONNES)
        for o in offres:
            w.writerow([str(v).replace("\t", " ").replace("\n", " ") for v in o.ligne()])
