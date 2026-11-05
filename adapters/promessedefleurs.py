"""Adaptateur Promesse de Fleurs (https://www.promessedefleurs.com).

Méthode : sitemap public (titres « Nom – Nom latin ») pour trouver les fiches, puis
JSON-LD schema.org (ProductGroup / hasVariant / offers) de chaque fiche. La recherche
du site est interdite par robots.txt et n'est donc pas utilisée.
"""
import datetime as dt
import html
import json
import logging
import os
import re

import csv

import config
from core import matching as M
from core.formatting import (Offre, contenant, forme, hauteur, lot, prix_fr, repartis,
                             taille_contenant)
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Promesse de Fleurs"
BASE = "https://www.promessedefleurs.com"
SITEMAP = BASE + "/media/sitemap/fr/sitemap.xml"
EXCLUS = ("/conseil-plantes-jardin/",)


class Adaptateur:
    def __init__(self):
        self.http = ClientPoli(BASE)
        self.entrees = None
        self.pages = {}  # url -> HTML, pour ce relevé seulement
        self.acceptees = self._lire_acceptees()

    @staticmethod
    def _lire_acceptees():
        """(ID, URL) -> (variété sur le site, nom dans Fiches, raison)."""
        chemin = getattr(config, "FICHIER_FICHES_ACCEPTEES", "")
        if not chemin or not os.path.exists(chemin):
            return {}
        with open(chemin, encoding="utf-8", newline="") as f:
            return {(r["ID"].strip(), r["URL"].strip()): (r["Variété sur le site"].strip(),
                                                          (r.get("Nom dans Fiches") or "").strip(),
                                                          (r.get("Raison") or "").strip())
                    for r in csv.DictReader(f) if r.get("ID") and r.get("URL")}

    # ---------- sitemap ----------
    def _sitemap(self):
        if self.entrees is not None:
            return self.entrees
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        cache = os.path.join(config.DOSSIER_CACHE, f"sitemap_pdf_{dt.date.today():%Y-%m-%d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                self.entrees = json.load(f)
            log.info("Sitemap lu depuis le cache %s (%d URL)", cache, len(self.entrees))
            return self.entrees
        index = self.http.get(SITEMAP).text
        entrees = []
        for loc in re.findall(r"<loc>([^<]+)</loc>", index):
            xml = self.http.get(html.unescape(loc)).text
            for bloc in re.findall(r"<url>(.*?)</url>", xml, re.S):
                u = re.search(r"<loc>([^<]+)</loc>", bloc)
                t = re.search(r"<image:title>([^<]*)</image:title>", bloc)
                if u and u.group(1).endswith(".html") and not any(x in u.group(1) for x in EXCLUS):
                    entrees.append([html.unescape(u.group(1)), html.unescape(t.group(1)).strip() if t else ""])
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(entrees, f, ensure_ascii=False)
        log.info("Sitemap : %d URL produit/catégorie", len(entrees))
        self.entrees = entrees
        return entrees

    def candidats(self, plante):
        vus, res = set(), []
        for url, titre in self._sitemap():
            mode = M.est_candidat(plante, titre, url)
            if not mode and (plante.id, url) in self.acceptees:
                mode = "acceptée"
            if mode and url not in vus:
                vus.add(url)
                res.append((url, titre, mode))
        if not res:  # repli : nom commun et autres noms
            for url, titre in self._sitemap():
                if titre and M.candidat_nom_commun(plante, titre) and url not in vus:
                    vus.add(url)
                    res.append((url, titre, "nom commun"))
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche produit ----------
    @staticmethod
    def _jsonld_produit(page):
        for bloc in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                d = json.loads(bloc)
            except ValueError:
                continue
            for n in d.get("@graph", [d]) if isinstance(d, dict) else d:
                if isinstance(n, dict) and n.get("@type") in ("ProductGroup", "Product"):
                    return n
        return None

    def releve(self, plante, aujourdhui):
        """Renvoie (offres, anomalies)."""
        offres, anomalies = [], []
        cands = self.candidats(plante)
        log.info("%s %s : %d fiche(s) candidate(s)", plante.id, plante.nom_latin, len(cands))
        for url, titre_sm, mode in cands:
            try:
                # Une fiche déjà lue pendant ce relevé (pour une autre plante) n'est pas redemandée
                if url not in self.pages:
                    self.pages[url] = self.http.get(url).text
                page = self.pages[url]
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:  # réseau, HTTP 404…  (Bloque remonte via run.py)
                if e.__class__.__name__ == "Bloque":
                    raise
                detail = f" ({e})" if e.__class__.__name__ == "PageEnErreur" else ""
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}{detail}"))
                continue
            o, a = self._analyser(plante, url, page, mode, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        elif config.MAX_LIGNES_PAR_PLANTE:
            offres, coupe = repartis(offres, config.MAX_LIGNES_PAR_PLANTE)
            if coupe:
                offres[-1].notes = "; ".join(x for x in [offres[-1].notes, "autres formats disponibles"] if x)
        return offres, anomalies

    def _analyser(self, plante, url, page, mode, aujourdhui):
        p = self._jsonld_produit(page)
        if not p:
            return [], [(url, "Pas de données produit (JSON-LD) : page catégorie ou article")]
        nom = html.unescape(p.get("name", "")).strip()
        h1 = " ".join(re.sub(r"<[^>]+>", " ", x) for x in re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S))
        props = [f"{x.get('name')} {x.get('value')}" for x in p.get("additionalProperty", []) if isinstance(x, dict)]
        textes = [nom, h1, p.get("description", "")] + props
        notes_prod = []

        var_fiches = M.variete_fiches(plante, nom)
        acceptee = self.acceptees.get((plante.id, url))
        if acceptee:
            v_site, v_fiches, raison = acceptee
            precision = f" (Fiches : {v_fiches})" if v_fiches else ""
            notes_prod.append(f"Fiche acceptée malgré un nom écrit autrement : {raison}{precision}")
        elif not M.latin_sur_fiche(plante, textes):
            tn = M.norm(" ".join(textes))
            if M.contient(tn, plante.genre) and var_fiches:
                notes_prod.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                                  f"rapproché par la variété {var_fiches} de l'onglet Fiches")
            elif plante.genre_seul and M.contient(tn, plante.genre) and not M.autre_espece(plante, tn):
                notes_prod.append(f"Espèce non précisée sur la fiche (genre {plante.genre} seul) : "
                                  f"rattaché à {plante.nom_latin}")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {nom} » : aucun prix relevé")]

        if acceptee:
            variete, connue = acceptee[0], False
        elif M.est_melange(nom):
            notes_prod.append(f"Lot/mélange de la même espèce (« {nom} »)")
            variete, connue = "Mélange", False
        else:
            variete, connue = M.variete_du_titre(plante, nom)
        if variete and not connue:
            notes_prod.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete and variete != "Mélange" else plante.nom_latin
        variete_col = variete or "Espèce type"

        variantes = p.get("hasVariant") or [p]
        lignes = []
        for v in variantes:
            desc = html.unescape(v.get("disambiguatingDescription") or "")
            if not desc and v is not p:
                desc = html.unescape(v.get("name", "")).replace(nom, "").strip(" -")
            offres_v = v.get("offers") or []
            offres_v = offres_v if isinstance(offres_v, list) else [offres_v]
            for of in offres_v:
                dispo = "En stock" if str(of.get("availability", "")).endswith("InStock") else "Rupture"
                notes = list(notes_prod)
                cont = contenant(desc) or ("Sachet de graines" if "graine" in M.norm(nom) else "")
                h, f = hauteur(desc), forme(desc)
                if h and f:
                    notes.append(f"Forme : {f}")
                n_lot = lot(desc) or lot(nom)
                if n_lot:
                    notes.append(f"lot de {n_lot}")
                prix = of.get("price")
                barres, degressifs = [], []
                for ps in of.get("priceSpecification", []) or []:
                    t = str(ps.get("priceType", ""))
                    q = (ps.get("eligibleQuantity") or {}).get("value")
                    if t.endswith("StrikethroughPrice") or (t.endswith("ListPrice") and prix is not None
                                                             and float(ps.get("price", 0)) > float(prix)):
                        barres.append(prix_fr(ps.get("price")))
                    elif q and float(q) > 1:
                        degressifs.append(f"{prix_fr(ps.get('price'))} € l'unité dès {int(float(q))}")
                if barres:
                    notes.append(f"Prix barré avant promotion : {barres[0]}")
                if degressifs:
                    notes.append("Prix dégressifs : " + ", ".join(degressifs))
                if not cont:
                    notes.append(f"Format non reconnu : « {desc} »")
                o = Offre(plante.id, plante.nom_commun, variete_col, NOM, url, prix_fr(prix), "EUR", cont,
                          dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin,
                          h or f or "")
                o.tri = taille_contenant(cont) * 1000 + float(prix or 0)
                lignes.append(o)

        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            n = " ; ".join(notes_prod + ["Aucun format en stock"])
            return [Offre(plante.id, plante.nom_commun, variete_col, NOM, url, "", "EUR", "", "Rupture", "",
                          aujourdhui, "", "À vérifier", n, nom_latin, "")], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
