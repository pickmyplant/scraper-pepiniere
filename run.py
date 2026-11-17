"""Relevé des offres d'une pépinière pour les plantes de la BDD.

Exemple : python run.py --ids P006,P007,P013,P084,P116
"""
import argparse
import datetime as dt
import json
import logging
import os
import subprocess
import sys

import config
from core.bdd import lire_plantes
from core.export import ecrire_tsv
from core.http import Bloque


def _adaptateurs():
    """Tout fichier adapters/<nom>.py est un adaptateur : rien à déclarer ici (évite les conflits entre branches)."""
    dossier = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapters")
    return {f[:-3]: f"adapters.{f[:-3]}" for f in sorted(os.listdir(dossier))
            if f.endswith(".py") and not f.startswith("_")}


ADAPTATEURS = _adaptateurs()


def journal(silencieux=False):
    os.makedirs(config.DOSSIER_LOGS, exist_ok=True)
    fichier = os.path.join(config.DOSSIER_LOGS, f"releve_{dt.datetime.now():%Y-%m-%d_%H%M%S}.log")
    ecran = logging.StreamHandler()
    if silencieux:  # à l'écran : seulement avertissements et erreurs ; tout le détail reste dans logs/
        ecran.setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(fichier, encoding="utf-8"), ecran])
    return fichier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pepiniere", default="promessedefleurs", choices=ADAPTATEURS)
    ap.add_argument("--ids", default="", help="IDs séparés par des virgules (défaut : toutes les plantes du CSV)")
    ap.add_argument("--plantes", default=config.FICHIER_PLANTES)
    ap.add_argument("--sortie", default=None, help="défaut : offres_<pepiniere>_test.tsv")
    ap.add_argument("--releve", default=None,
                    help="identifiant du relevé (ex. PLF-2026-11-06-A4) : écrit releves/<ID>.tsv et releves/<ID>.json "
                         "pour le bouton « Importer un relevé » de la Sheet")
    ap.add_argument("--par", default="Claude A", help="qui fait le relevé (pour le registre)")
    ap.add_argument("--remarques", default="", help="remarques pour le registre")
    ap.add_argument("--silencieux", action="store_true", help="n'afficher que les avertissements et erreurs")
    args = ap.parse_args()

    if args.releve:
        os.makedirs("releves", exist_ok=True)
        args.sortie = os.path.join("releves", f"{args.releve}.tsv")
    args.sortie = args.sortie or f"offres_{args.pepiniere}_test.tsv"
    fichier_log = journal(args.silencieux)
    log = logging.getLogger("scraper")
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    plantes = lire_plantes(args.plantes, ids or None, config.FICHIER_EQUIVALENCES, config.FICHIER_EQUIVALENCES_LATIN)
    manquants = set(ids) - {p.id for p in plantes}
    if manquants:
        log.warning("IDs absents du fichier plantes : %s", ", ".join(sorted(manquants)))

    module = __import__(ADAPTATEURS[args.pepiniere], fromlist=["Adaptateur"])
    aujourdhui = dt.date.today().strftime("%d/%m/%Y")
    offres, anomalies, en_erreur = [], [], []
    try:
        ad = module.Adaptateur()
        for p in plantes:
            try:
                o, a = ad.releve(p, aujourdhui)
            except Bloque:
                raise
            except Exception as e:
                # Erreur propre à une plante (page en erreur 500, fiche inattendue...) : le lot continue ;
                # la plante est retirée du relevé, ses anciennes lignes restent donc dans Offres.
                log.warning("ERREUR sur %s (%s) : %r ; plante non relevée", p.id, p.nom_latin, e)
                en_erreur.append(p.id)
                anomalies.append((p.id, "", f"Plante non relevée : {e.__class__.__name__} {e}"[:300]))
                continue
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
    if en_erreur:
        log.warning("Plantes en erreur, non relevées (anciennes lignes gardées) : %s", ", ".join(en_erreur))
        plantes = [p for p in plantes if p.id not in en_erreur]
        args.remarques = (args.remarques + " " if args.remarques else "") + \
            "Non relevées (erreur, à relancer) : " + ", ".join(en_erreur) + "."
    if args.releve:
        ecrire_fiche_releve(args, module, plantes, offres)


def ecrire_fiche_releve(args, module, plantes, offres):
    """Fiche lue par le bouton d'import de la Sheet (Apps Script)."""
    try:
        branche = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                 text=True, check=True).stdout.strip()
    except Exception:
        branche = ""
    fiche = {
        "releve": args.releve,
        "pepiniere": getattr(module, "NOM", args.pepiniere),
        "date": dt.date.today().strftime("%d/%m/%Y"),
        "par": args.par,
        "plantes": [p.id for p in plantes],
        "nb_lignes": len(offres),
        "fichier": args.sortie.replace(os.sep, "/"),
        "branche": branche,
        "remarques": args.remarques,
    }
    with open(os.path.join("releves", f"{args.releve}.json"), "w", encoding="utf-8") as f:
        json.dump(fiche, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
