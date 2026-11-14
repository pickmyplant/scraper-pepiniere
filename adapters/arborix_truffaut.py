"""Arborix vu sur la marketplace Truffaut (https://www.truffaut.com).

Le site propre d'Arborix (arborix.be) est fermé aux robots (défi Cloudflare, 14/11/2026) : on relève
les fiches Truffaut « Vendu par Arborix », pour comparer ensuite avec les prix du site Arborix.
Index : la page vendeur Arborix de Truffaut (/vendeurs-marketplace/..., autorisée par robots.txt,
environ 2 800 produits, 58 pages), mise en cache 7 jours dans cache/. Fiches lues comme dans
adapters/truffaut.py (JSON-LD) ; seules les fiches dont le vendeur est Arborix sont gardées.
"""
import collections
import dataclasses
import datetime as dt
import glob
import html
import json
import logging
import os
import re

import config

from adapters import truffaut as T

NOM = "Truffaut"
PREFIXE = "ARX"  # préfixe des identifiants de relevé (outils/lot.sh)
VENDEUR = re.compile(r'offer-seller-name">Vendu par <a[^>]*title="([^"]+)"')
PAGE_VENDEUR = T.BASE + "/vendeurs-marketplace/arborix-plus-vert-moin-cher.html"
log = logging.getLogger("scraper")


class Adaptateur(T.Adaptateur):
    def _index(self):
        """Fiches Arborix seulement (page vendeur), au lieu des 330 000 fiches du sitemap."""
        if self.urls is not None:
            return self.urls
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=T.DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_arx_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.urls = json.load(fh)
                log.info("Index Arborix lu depuis le cache %s (%d fiches)", f, len(self.urls))
                return self.urls
        lien = re.compile(r'href="(https://www\.truffaut\.com/[^"/?#]+\.html)"')
        page1 = self.http.get(PAGE_VENDEUR).text
        nb = max([int(x) for x in re.findall(r"\?p=(\d+)", page1)] or [1])
        pages = [set(lien.findall(page1))]
        for i in range(2, nb + 1):
            pages.append(set(lien.findall(self.http.get(f"{PAGE_VENDEUR}?p={i}").text)))
        # Liens du menu et du pied de page : présents sur presque toutes les pages
        compte = collections.Counter(u for p in pages for u in p)
        self.urls = sorted(u for u, n in compte.items() if n <= max(2, nb // 4))
        with open(os.path.join(config.DOSSIER_CACHE, f"index_arx_{dt.date.today():%Y-%m-%d}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(self.urls, f)
        log.info("Index Arborix : %d fiches sur %d pages", len(self.urls), nb)
        return self.urls

    def releve(self, plante, aujourdhui):
        offres, anomalies = super().releve(plante, aujourdhui)
        # Complément du relevé Truffaut : pas de ligne « Non trouvé » (déjà dans les relevés TRU)
        offres = [o for o in offres if o.lien]
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
