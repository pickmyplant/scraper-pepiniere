"""Relevé des offres d'une pépinière pour les plantes de la BDD.

Exemple : python run.py --ids P006,P007,P013,P084,P116
"""
import argparse
import datetime as dt
import logging
import os
import sys

import config
from core.bdd import lire_plantes
from core.export import ecrire_tsv
from core.http import Bloque

ADAPTATEURS = {"promessedefleurs": "adapters.promessedefleurs"}


def journal():
    os.makedirs(config.DOSSIER_LOGS, exist_ok=True)
    fichier = os.path.join(config.DOSSIER_LOGS, f"releve_{dt.datetime.now():%Y-%m-%d_%H%M%S}.log")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(fichier, encoding="utf-8"), logging.StreamHandler()])
    return fichier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pepiniere", default="promessedefleurs", choices=ADAPTATEURS)
    ap.add_argument("--ids", default="", help="IDs séparés par des virgules (défaut : toutes les plantes du CSV)")
    ap.add_argument("--plantes", default=config.FICHIER_PLANTES)
    ap.add_argument("--sortie", default="offres_promessedefleurs_test.tsv")
    args = ap.parse_args()

    fichier_log = journal()
    log = logging.getLogger("scraper")
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    plantes = lire_plantes(args.plantes, ids or None, config.FICHIER_EQUIVALENCES)
    manquants = set(ids) - {p.id for p in plantes}
    if manquants:
        log.warning("IDs absents du fichier plantes : %s", ", ".join(sorted(manquants)))

    module = __import__(ADAPTATEURS[args.pepiniere], fromlist=["Adaptateur"])
    aujourdhui = dt.date.today().strftime("%d/%m/%Y")
    offres, anomalies = [], []
    try:
        ad = module.Adaptateur()
        for p in plantes:
            o, a = ad.releve(p, aujourdhui)
            offres += o
            anomalies += [(p.id, url, raison) for url, raison in a]
    except Bloque as e:
        log.error("ARRÊT : le site bloque l'accès (%s). Aucun contournement tenté.", e)
        ecrire_tsv(args.sortie, offres)
        sys.exit(2)

    ecrire_tsv(args.sortie, offres)
    with open(os.path.splitext(args.sortie)[0] + "_anomalies.tsv", "w", encoding="utf-8") as f:
        f.write("ID\tURL\tRaison\n")
        for a in anomalies:
            f.write("\t".join(a) + "\n")
    log.info("%d ligne(s) écrite(s) dans %s ; %d anomalie(s) ; %d requête(s) ; journal : %s",
             len(offres), args.sortie, len(anomalies), ad.http.nb_requetes, fichier_log)


if __name__ == "__main__":
    main()
