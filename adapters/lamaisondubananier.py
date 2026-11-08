"""Adaptateur La Maison du Bananier (https://www.lamaisondubananier.com, PrestaShop).

Méthode : la recherche du site est interdite par robots.txt. L'index des fiches est fait
à partir des listes de catégories (pagination ?page=N, autorisée) et du sitemap public
(daté de mars 2025, incomplet : complété par les catégories). L'index est mis en cache
7 jours dans cache/. Sur chaque fiche : titre <h1> « Nom latin 'Variété' / Nom commun »
et tableau des formats (Taille de pot, Taille de plante, Prix, quantité disponible).
Pas de JSON-LD sur ce site.
"""
import datetime as dt
import glob
import html
import json
import logging
import math
import os
import re

import config
from core import matching as M
from core.formatting import Offre, contenant, forme, hauteur, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "La Maison du Bananier"
BASE = "https://www.lamaisondubananier.com"
SITEMAP = BASE + "/1_fr_0_sitemap.xml"
PAR_PAGE = 12
# Catégories sans plantes (soins, engrais…) : non parcourues
CATEGORIES_EXCLUES = re.compile(r"soins|engrais|substrat|terreau|maladies|aucun", re.I)
FICHE = re.compile(r"^https://www\.lamaisondubananier\.com/[a-z0-9-]+/(\d+)(?:-\d+)?-[^/]*\.html$")
DUREE_CACHE_JOURS = 7


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


def _sans_ancre(u):
    return html.unescape(u).split("#", 1)[0].strip()


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("lamaisondubananier", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.entrees = None

    # ---------- index des fiches ----------
    def _index(self):
        if self.entrees is not None:
            return self.entrees
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_lmb_*.json")), reverse=True):
            d = dt.date.fromisoformat(os.path.basename(f)[10:20])
            if d >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.entrees = json.load(fh)
                log.info("Index lu depuis le cache %s (%d fiches)", f, len(self.entrees))
                return self.entrees
        fiches = {}  # id produit -> [url, titre]

        def noter(url, titre):
            url = _sans_ancre(url)
            m = FICHE.match(url)
            if not m:
                return
            ancien = fiches.get(m.group(1))
            if not ancien or (titre and not ancien[1]):
                fiches[m.group(1)] = [url, titre]

        # 1) sitemap (ancien mais donne les titres complets)
        xml = self.http.get(SITEMAP).text
        for bloc in re.findall(r"<url>(.*?)</url>", xml, re.S):
            u = re.search(r"<loc><!\[CDATA\[(.*?)\]\]></loc>", bloc)
            t = re.search(r"<image:title><!\[CDATA\[(.*?)\]\]></image:title>", bloc)
            if u:
                noter(u.group(1), _texte(t.group(1)) if t else "")
        nb_sitemap = len(fiches)

        # 2) catégories (parcours en largeur depuis l'accueil)
        a_voir, vues = [], set()

        def categories(page):
            for u in re.findall(r'href="(https://www\.lamaisondubananier\.com/(\d+)-([a-z0-9-]+))"', page):
                if u[1] not in vues and not CATEGORIES_EXCLUES.search(u[2]):
                    vues.add(u[1])
                    a_voir.append(u[0])

        categories(self.http.get(BASE + "/").text)
        while a_voir:
            cat = a_voir.pop(0)
            try:
                page = self.http.get(cat).text
            except InterditParRobots:
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                log.warning("Catégorie illisible %s : %s", cat, e.__class__.__name__)
                continue
            categories(page)
            m = re.search(r"Il y a (\d+) produits?", page)
            nb = int(m.group(1)) if m else 0
            pages = [page]
            for n in range(2, math.ceil(nb / PAR_PAGE) + 1):
                try:
                    pages.append(self.http.get(f"{cat}?page={n}").text)
                except InterditParRobots:
                    break
            for p in pages:
                for bloc in re.findall(r'class="[^"]*product-title[^"]*"[^>]*>(.*?)</', p, re.S):
                    a = re.search(r'href="([^"]+)"[^>]*>(.*)', bloc, re.S)
                    if a:
                        noter(a.group(1), _texte(a.group(2)))
        self.entrees = sorted(fiches.values())
        cache = os.path.join(config.DOSSIER_CACHE, f"index_lmb_{dt.date.today():%Y-%m-%d}.json")
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(self.entrees, f, ensure_ascii=False)
        log.info("Index : %d fiches (%d par le sitemap, %d catégories)", len(self.entrees), nb_sitemap, len(vues))
        return self.entrees

    def candidats(self, plante):
        vus, res = set(), []
        for url, titre in self._index():
            mode = M.est_candidat(plante, titre, url)
            if mode and url not in vus:
                vus.add(url)
                res.append((url, mode))
        if not res:  # repli : nom commun et autres noms (le nom latin est vérifié sur la fiche)
            for url, titre in self._index():
                slug = url.rsplit("/", 1)[-1].replace("-", " ")
                if (M.candidat_nom_commun(plante, titre) or M.candidat_nom_commun(plante, slug)) and url not in vus:
                    vus.add(url)
                    res.append((url, "nom commun"))
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        cands = self.candidats(plante)
        log.info("%s %s : %d fiche(s) candidate(s)", plante.id, plante.nom_latin, len(cands))
        vues = set()
        for url, mode in cands:
            try:
                r = self.http.get(url)
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            final = _sans_ancre(r.url)
            if not FICHE.match(final):
                anomalies.append((url, f"Fiche disparue (redirigée vers {final})"))
                continue
            if final in vues:
                continue
            vues.add(final)
            o, a = self._analyser(plante, final, r.text, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    @staticmethod
    def _formats(page):
        """Lignes du tableau des formats : (contenant, taille, [prix], quantité, en_stock)."""
        i = page.find('<div class="an_bootstraptabs">')
        if i < 0:
            return []
        j = page.find('class="list-logo"', i)
        bloc = page[i: j if j > 0 else i + 50000]
        res = []
        for item in re.split(r'<div class="an_table-item', bloc)[1:]:
            if item.startswith(" head"):
                continue
            def col(nom):
                m = re.search(rf'<div class="{nom}">(.*?)(?=<div class="(?:contenance|hauteur|prix|add-cart|qte)">|$)', item, re.S)
                return m.group(1) if m else ""
            prix = [float(p.replace(".", "").replace(",", ".")) for p in re.findall(r"([\d.]+,\d{2})\s*€", col("prix"))]
            qte = re.search(r"<!--\s*(-?\d+)\s*-->", col("qte"))
            stock = "outofstock" not in col("add-cart") and bool(prix) and 'class="red"' not in col("qte")
            res.append((_texte(col("contenance")), _texte(col("hauteur")), prix, int(qte.group(1)) if qte else None, stock))
        return res

    def _analyser(self, plante, url, page, aujourdhui):
        h1 = _texte(" ".join(re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S)[:1]))
        latin = h1.split(" / ", 1)[0]
        desc = re.search(r'id="product-description-short-\d+"[^>]*>(.*?)</div>', page, re.S)
        textes = [h1, _texte(desc.group(1)) if desc else ""]
        notes_page = []
        equiv = M.equivalence_utilisee(plante, textes)
        approche = None if M.latin_sur_fiche(plante, textes) else M.latin_approche(plante, textes)
        if equiv:
            notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if approche:
            notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
        elif not M.latin_sur_fiche(plante, textes):
            var = M.variete_fiches(plante, h1)
            if M.contient(M.norm(h1), plante.genre) and var:
                notes_page.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                                  f"rapproché par la variété {var} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {h1} » : aucun prix relevé")]
        if M.est_melange(h1):
            notes_page.append(f"Lot/mélange de la même espèce (« {h1} »)")

        variete, connue = M.variete_du_titre(plante, latin)
        variete = re.sub(r"[®™]", "", variete).strip(" '\"‘’")
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        var_col = variete or "Espèce type"

        lignes = []
        for cont_brut, taille, prix, qte, stock in self._formats(page):
            notes = list(notes_page)
            if not prix or re.search(r"indisponible|en cours de culture", cont_brut, re.I):
                if cont_brut and not re.search(r"indisponible", cont_brut, re.I):
                    notes.append(f"Format : « {cont_brut} »")
                lignes.append(Offre(plante.id, plante.nom_commun, var_col, NOM, url, "", "EUR", contenant(cont_brut),
                                    "Rupture", "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, ""))
                continue
            cont = contenant(cont_brut)
            if not cont:
                notes.append(f"Format non reconnu : « {cont_brut} »")
            if len(set(prix)) > 1:
                notes.append(f"Promotion (prix barré {prix_fr(max(prix))} €)")
            if re.search(r"uniquement a la p[eé]pini[eè]re|contactez", f"{cont_brut} {taille}", re.I):
                notes.append("Disponible uniquement à la pépinière (retrait sur place)")
            h = hauteur(taille) or (re.sub(r"\s*cm\b", " cm", taille) if re.search(r"\d+\s*(?:/|-|à)\s*\d+\s*cm", taille) else None)
            f = forme(f"{cont_brut} {taille}")
            if taille and not h and not f and "contactez" not in taille.lower():
                notes.append(f"Taille : {taille}")
            if h and f:
                notes.append(f"Forme : {f}")
            o = Offre(plante.id, plante.nom_commun, var_col, NOM, url, prix_fr(min(prix)), "EUR", cont,
                      "En stock" if stock else "Rupture", "", aujourdhui, "", "À vérifier", " ; ".join(notes),
                      nom_latin, h or f or "")
            o.tri = taille_contenant(cont) * 1000 + min(prix)
            lignes.append(o)

        if not lignes:
            return [Offre(plante.id, plante.nom_commun, var_col, NOM, url, "", "EUR", "", "Rupture", "", aujourdhui,
                          "", "À vérifier", " ; ".join(notes_page + ["Aucun format affiché sur la fiche"]),
                          nom_latin, "")], []
        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            o = lignes[0]
            return [Offre(plante.id, plante.nom_commun, var_col, NOM, url, "", "EUR", o.contenant, "Rupture", "",
                          aujourdhui, "", "À vérifier", " ; ".join(notes_page + ["Aucun format en stock"]),
                          nom_latin, "")], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
