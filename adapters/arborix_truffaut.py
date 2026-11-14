"""Arborix vu sur la marketplace Truffaut (https://www.truffaut.com).

Le site propre d'Arborix (arborix.be) est fermé aux robots (défi Cloudflare, 14/11/2026) : on relève
les fiches Truffaut « Vendu par Arborix », pour comparer ensuite avec les prix du site Arborix.
Même méthode que adapters/truffaut.py (index du sitemap, JSON-LD), mais seules les fiches dont le
vendeur est Arborix sont gardées.
"""
import dataclasses
import html
import re

from adapters import truffaut as T

NOM = "Arborix (via Truffaut)"
PREFIXE = "ARX"  # préfixe des identifiants de relevé (outils/lot.sh)
VENDEUR = re.compile(r'offer-seller-name">Vendu par <a[^>]*title="([^"]+)"')


class Adaptateur(T.Adaptateur):
    def releve(self, plante, aujourdhui):
        offres, anomalies = super().releve(plante, aujourdhui)
        return [dataclasses.replace(o, pepiniere=NOM) for o in offres], anomalies

    def _analyser(self, plante, url, page, aujourdhui):
        v = VENDEUR.search(page)
        vendeur = html.unescape(v.group(1)) if v else "Truffaut"
        if "arborix" not in vendeur.lower():
            return [], [(url, f"Vendu par {vendeur} : non relevé (seul Arborix est suivi ici)")]
        offres, anomalies = super()._analyser(plante, url, VENDEUR.sub('offer-seller-name">', page), aujourdhui)
        for o in offres:
            o.notes = " ; ".join(x for x in ("Vendu par Arborix sur Truffaut (marketplace)", o.notes) if x)
        return offres, anomalies
