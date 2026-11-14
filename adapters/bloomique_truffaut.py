"""Bloomique vu sur la marketplace Truffaut (https://www.truffaut.com).

Les fiches Truffaut « Vendu par Bloomique » sont relevées (demande de l'utilisatrice du 14/11/2026),
pour comparer ensuite avec le site propre de Bloomique.
Même méthode que adapters/truffaut.py (index du sitemap, JSON-LD), mais seules les fiches dont le
vendeur est Bloomique sont gardées.
"""
import dataclasses
import html
import re

from adapters import truffaut as T

NOM = "Bloomique (via Truffaut)"
PREFIXE = "BLQ"  # préfixe des identifiants de relevé (outils/lot.sh)
VENDEUR = re.compile(r'offer-seller-name">Vendu par <a[^>]*title="([^"]+)"')


class Adaptateur(T.Adaptateur):
    def releve(self, plante, aujourdhui):
        offres, anomalies = super().releve(plante, aujourdhui)
        return [dataclasses.replace(o, pepiniere=NOM) for o in offres], anomalies

    def _analyser(self, plante, url, page, aujourdhui):
        v = VENDEUR.search(page)
        vendeur = html.unescape(v.group(1)) if v else "Truffaut"
        if "bloomique" not in vendeur.lower():
            return [], [(url, f"Vendu par {vendeur} : non relevé (seul Bloomique est suivi ici)")]
        offres, anomalies = super()._analyser(plante, url, VENDEUR.sub('offer-seller-name">', page), aujourdhui)
        for o in offres:
            o.notes = " ; ".join(x for x in ("Vendu par Bloomique sur Truffaut (marketplace)", o.notes) if x)
        return offres, anomalies
