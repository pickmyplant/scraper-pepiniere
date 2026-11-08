"""Extrait un lot de plantes de la BDD sans passer par la conversation (zéro token de données).

Source : l'onglet « Export » de BDD Plante (Plantes A:F + Variétés de Fiches, jointure par ID),
publié sur le web au format CSV. Le lien est lu dans BDD_EXPORT_URL ou passé avec --url.

Utilisation : python outils/extraire_lot.py P082 P120 [--url <lien CSV publié>]
Écrit data/plantes_P082-P120.csv et affiche une seule ligne de bilan.
"""
import argparse
import csv
import io
import os
import re
import sys

import requests

COLONNES = ["ID", "Nom commun", "Autres noms", "Synonymes", "Genre", "Espèce", "Variétés"]


def num(i):
    m = re.fullmatch(r"P(\d+)", (i or "").strip())
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("debut")
    ap.add_argument("fin")
    ap.add_argument("--url", default=os.environ.get("BDD_EXPORT_URL"))
    ap.add_argument("--sortie", default=None)
    a = ap.parse_args()
    if not a.url:
        sys.exit("Lien manquant : définir BDD_EXPORT_URL ou passer --url.")
    r = requests.get(a.url, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    lignes = list(csv.DictReader(io.StringIO(r.text)))
    manque = [c for c in COLONNES if lignes and c not in lignes[0]]
    if manque:
        sys.exit(f"Colonnes absentes de l'onglet Export : {', '.join(manque)}")
    d, f = num(a.debut), num(a.fin)
    lot = [l for l in lignes if (n := num(l["ID"])) is not None and d <= n <= f]
    sortie = a.sortie or f"data/plantes_{a.debut}-{a.fin}.csv"
    with open(sortie, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLONNES, extrasaction="ignore")
        w.writeheader()
        w.writerows(lot)
    sans_var = sum(1 for l in lot if not l["Variétés"].strip())
    attendus = {f"P{n:03d}" for n in range(d, f + 1)}
    absents = sorted(attendus - {l["ID"] for l in lot})
    print(f"{sortie} : {len(lot)} plantes ; {sans_var} sans Variétés"
          + (f" ; absents : {', '.join(absents)}" if absents else ""))


if __name__ == "__main__":
    main()
