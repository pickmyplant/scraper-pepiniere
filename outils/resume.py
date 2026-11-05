"""Résumé compact d'un relevé, pour ne relire que l'essentiel.

Utilisation : python outils/resume.py releves/<ID>.tsv
(le fichier d'anomalies <ID>_anomalies.tsv est lu s'il existe)

Affiche une ligne par plante (offres en stock / ruptures / non trouvé) puis seulement
les cas à vérifier :
 - plante « Non trouvé » alors qu'une fiche du même genre a été écartée (autre espèce ? autre nom ?) ;
 - format non reconnu ;
 - nom latin écrit autrement sur la fiche (orthographe proche ou équivalence).
"""
import csv
import os
import re
import sys
from collections import OrderedDict


def lire(chemin):
    with open(chemin, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    tsv = sys.argv[1]
    lignes = lire(tsv)
    chemin_an = os.path.splitext(tsv)[0] + "_anomalies.tsv"
    anomalies = lire(chemin_an) if os.path.exists(chemin_an) else []

    par_plante = OrderedDict()
    for l in lignes:
        d = par_plante.setdefault(l["ID"], {"nom": l["Plante"], "latin": l["Nom latin"].split(" '")[0],
                                            "stock": 0, "rupture": 0, "nt": False})
        if l["Notes"] == "Non trouvé":
            d["nt"] = True
        elif l["Disponibilité"] == "En stock":
            d["stock"] += 1
        else:
            d["rupture"] += 1

    print(f"{len(lignes)} lignes, {len(par_plante)} plantes")
    trouves = [f"{i} {d['stock']}s{'/' + str(d['rupture']) + 'r' if d['rupture'] else ''}"
               for i, d in par_plante.items() if not d["nt"]]
    print("Trouvées (s = en stock, r = rupture) :", ", ".join(trouves) or "aucune")
    print("Non trouvées :", ", ".join(i for i, d in par_plante.items() if d["nt"]) or "aucune")

    a_voir = []
    for i, d in par_plante.items():
        if not d["nt"]:
            continue
        genre = d["latin"].split()[0].lower() if d["latin"] else ""
        meme_genre = []
        for a in anomalies:
            if a["ID"] != i:
                continue
            m = re.search(r"« ([^»]*) »", a["Raison"])
            titre = m.group(1) if m else ""
            latin_fiche = titre.split("/", 1)[1].strip() if "/" in titre else titre
            if genre and latin_fiche.lower().startswith(genre):
                meme_genre.append(latin_fiche[:40])
        if meme_genre:
            ex = " ; ".join(meme_genre[:2]) + (" ; …" if len(meme_genre) > 2 else "")
            a_voir.append(f"{i} {d['latin']} : {len(meme_genre)} fiche(s) du même genre écartée(s) ({ex})")
    for l in lignes:
        if "Format non reconnu" in l["Notes"]:
            a_voir.append(f"{l['ID']} : format non reconnu ({l['Lien du produit']})")
        if "Nom latin écrit" in l["Notes"]:
            n = re.search(r"Nom latin écrit « ([^»]*) »", l["Notes"]).group(1)
            a_voir.append(f"{l['ID']} {l['Nom latin'].split(' ' + chr(39))[0]} : accepté sous « {n} »")
    a_voir = list(OrderedDict.fromkeys(a_voir))
    print(f"À vérifier ({len(a_voir)}) :")
    for x in a_voir:
        print(" -", x)


if __name__ == "__main__":
    main()
