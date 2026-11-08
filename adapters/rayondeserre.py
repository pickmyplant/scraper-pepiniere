"""Adaptateur Rayon de Serre (https://www.rayon-de-serre.fr), boutique PrestaShop.

Méthode : sitemap public (URL « /<catégorie>/<id>-<genre-espece-variete>.html » et titre
de l'image = nom commun) pour trouver les fiches ; la recherche du site est interdite par
robots.txt. Chaque fiche porte un attribut data-product (JSON PrestaShop) : nom, référence
(= nom latin), prix TTC, réduction, stock. Une fiche = un seul format (jeune plant en godet).
"""
import datetime as dt
import html
import json
import logging
import os
import re

import config
from core import matching as M
from core.formatting import Offre, hauteur, prix_fr
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Rayon de Serre"
PREFIXE = "RDS"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.rayon-de-serre.fr"
SITEMAP_INDEX = BASE + "/1_index_sitemap.xml"
FICHE = re.compile(r"^https://www\.rayon-de-serre\.fr/[a-z0-9-]+/\d+-[a-z0-9-]+\.html$")


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("rayondeserre", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.entrees = None
        self.pages = {}

    # ---------- sitemap ----------
    def _sitemap(self):
        if self.entrees is not None:
            return self.entrees
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        cache = os.path.join(config.DOSSIER_CACHE, f"sitemap_rds_{dt.date.today():%Y-%m-%d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                self.entrees = [tuple(x) for x in json.load(f)]
            return self.entrees
        index = self.http.get(SITEMAP_INDEX).text
        entrees = []
        for sm in re.findall(r"<loc>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</loc>", index):
            xml = self.http.get(sm.strip()).text
            for bloc in re.findall(r"<url>(.*?)</url>", xml, re.S):
                m = re.search(r"<loc>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</loc>", bloc)
                if not m or not FICHE.match(m.group(1).strip()):
                    continue
                titre = re.search(r"<image:title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</image:title>", bloc)
                entrees.append((m.group(1).strip(), html.unescape(titre.group(1)) if titre else ""))
        log.info("Sitemap Rayon de Serre : %d fiche(s)", len(entrees))
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(entrees, f, ensure_ascii=False)
        self.entrees = entrees
        return entrees

    def candidats(self, plante):
        res = []
        for url, titre in self._sitemap():
            slug = re.sub(r"^\d+-", "", url.rsplit("/", 1)[-1][:-5])
            if M.est_candidat(plante, titre, slug):
                res.append(url)
        if not res:  # nom commun dans le titre de l'image ; le nom latin est ensuite vérifié sur la fiche
            res = [u for u, t in self._sitemap() if t and M.candidat_nom_commun(plante, t)]
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for url in self.candidats(plante):
            try:
                if url not in self.pages:
                    self.pages[url] = self.http.get(url).text
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            o, a = self._analyser(plante, url, self.pages[url], aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, reference):
        """(variété, connue_dans_Fiches) d'après la référence latine ; vide = espèce type."""
        v = M.variete_fiches(plante, reference)
        if v:
            return v, True
        for n in M.noms_latins(plante):
            reste = M._apres(reference, n)
            if reste is not None:
                reste = re.sub(r"^(?:f|var|subsp|ssp)\.?\s+", "", reste).strip(" '\"‘’()")
                if not reste or M.norm(reste) == M.norm(plante.espece):
                    return "", True
                return reste, False
        return "", True

    def _analyser(self, plante, url, page, aujourdhui):
        d = None
        for brut in re.findall(r'data-product="([^"]*)"', page):  # d'autres attributs data-product (produits liés)
            try:
                x = json.loads(html.unescape(brut))
            except ValueError:
                continue
            if isinstance(x, dict) and "id_product" in x:
                d = x
                break
        if not d:
            return [], [(url, "Pas de données produit (data-product) sur la fiche")]
        nom = _texte(d.get("name"))
        ref = _texte(d.get("reference"))
        court = _texte(d.get("description_short"))
        titre = _texte(d.get("meta_title"))
        desc = _texte(d.get("description"))
        textes = [ref, court, titre]
        notes = []
        equiv = M.equivalence_utilisee(plante, textes)
        if equiv:
            notes.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if not M.latin_sur_fiche(plante, textes):
            approche = M.latin_approche(plante, textes)
            var = M.variete_fiches(plante, " ".join(textes))
            if approche:
                notes.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(" ".join(textes)), plante.genre) and var:
                notes.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                             f"rapproché par la variété {var} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {ref or nom} » : aucun prix relevé")]
        if M.est_melange(nom):
            notes.append(f"Lot/mélange de la même espèce (« {nom} »)")
        if re.search(r'<select[^>]+data-product-attribute|<input[^>]+data-product-attribute', page):
            notes.append("Plusieurs formats sur la fiche : seul le format par défaut est relevé")

        # La référence est parfois inversée (« 'Giant' Passiflora edulis ») : le résumé court d'abord
        variete, connue = self._variete(plante, court or titre.split(":")[0] or ref)
        if variete and not connue:
            notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        # Format : « jeunes plants livrés en godets » ; hauteur livrée « Hauteur : environ 10 à 20 cm »
        corps = _texte(re.sub(r"<script.*?</script>", " ", page, flags=re.S))
        # (la description parle souvent de graines ou de pots de culture : elle n'est pas utilisée pour le format)
        cont = "Godet" if re.search(r"livrés en godets?", corps, re.I) else ""
        haut = None
        mh = re.search(r"Hauteur\s*:\s*(?:environ\s*)?[^.|]{0,60}", desc)
        if mh:
            haut = hauteur(mh.group(0))
        if not cont:
            notes.append("Format non indiqué sur la fiche")

        qte = int(d.get("quantity") or 0)
        dispo = "En stock" if d.get("availability") == "available" and qte > 0 else "Rupture"
        if dispo == "Rupture":
            notes.append("Aucun format en stock" + (" (réservation possible)" if d.get("available_for_order") == "1" else ""))
            return [Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, "", "EUR", cont,
                          "Rupture", "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, haut or "")], []
        if d.get("has_discount"):
            avant = d.get("price_without_reduction")
            notes.append(f"Prix barré {prix_fr(avant)} €" if avant else "Prix réduit")
        prix = d.get("price_amount")
        return [Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(prix), "EUR", cont,
                      dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, haut or "")], []
