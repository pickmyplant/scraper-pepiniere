"""Adaptateur Jardinerie.fr (https://jardinerie.fr, Nuxt, prix en EUR, livraison FR, BE, NL et DE).

Méthode : l'API du site (beheerpaneel.gardencentershops.com) est interdite par son robots.txt ; on lit donc les pages
publiques. L'index vient des grandes catégories (une page chacune, toutes les fiches sans pagination) : chaque
vignette donne l'adresse /p/<slug>, le nom commun (<h3>), le nom latin (<i>, « Prunus laurocerasus « Gajo » ») et
le stock (« Bien approvisionné » / « Temporairement indisponible »). Les fiches du sitemap absentes des catégories
sont ajoutées avec le slug pour seul titre. Index mis en cache 7 jours.
Chaque fiche candidate est ouverte : les formats sont les boutons radio du bloc d'options (« 10 - 20 cm en pot »,
« 20 - 40 cm en pot ») avec leur prix et, en promotion, le prix barré (classe line-through).
Le stock n'est affiché que par fiche, pas par format.
"""
import datetime as dt
import glob
import html
import json
import logging
import os
import re

import config
from core import matching as M
from core.formatting import Offre, contenant, prix_fr, repartis, taille_contenant
from core.http import Bloque, ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Jardinerie.fr"
PREFIXE = "JFR"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://jardinerie.fr"
CATEGORIES = ["plantes-d-exterieur", "plantes-d-interieur", "terrasses-et-balcons", "plantes-aromatiques",
              "potager", "fruits", "bulbes-a-fleurs"]
DUREE_CACHE_JOURS = 7
EXCLUS = re.compile(r"\b(engrais|terreau|semence|gazon|s[ée]cateur|arrosoir|gants?|pelle|transplantoir|"
                    r"pulv[ée]risateur|vaporisateur|taille[- ]haie|cisaille|couteaux?|billes|b[aâ]tonnets|pommeau)\b",
                    re.I)
VIGNETTE = re.compile(r'<a href="(/p/[^"?#]+)"[^>]*>\s*<img[^>]*>\s*<h3[^>]*>(.*?)</h3>\s*<i[^>]*>(.*?)</i>(.*?)</a>',
                      re.S)
OPTION = re.compile(r'<label[^>]*>\s*<input type="radio"[^>]*>\s*<span class="ml-2">(.*?)</span>\s*'
                    r'<span class="ml-auto[^"]*">(.*?)</span>\s*</label>', re.S)
CULTIVAR = re.compile(r"[«'‘’\"]\s*([^«»'‘’\"]{2,40}?)\s*[»'‘’\"]")
HAUTEUR = re.compile(r"(\d+)\s*-\s*(\d+)\s*cm", re.I)
INDISPONIBLE = "Temporairement indisponible"
# Noms latins francisés sur le site, remis en latin avant comparaison
LATIN_FRANCISE = {"Aglaonème": "Aglaonema"}


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _latin(txt):
    for fr, la in LATIN_FRANCISE.items():
        txt = re.sub(rf"\b{fr}\b", la, txt or "")
    return txt


def _prix(txt):
    m = re.search(r"(\d[\d\s.]*,\d{2}|\d+(?:\.\d+)?)", txt or "")
    if not m:
        return None
    v = m.group(1).replace(" ", "")
    return float(v.replace(".", "").replace(",", ".") if "," in v else v)


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("jardinerie", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None
        self.pages = {}

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_jfr_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits, vus = [], set()
        for c in CATEGORIES:
            t = self.http.get(f"{BASE}/{c}").text
            for u, h3, latin, reste in VIGNETTE.findall(t):
                if u in vus:
                    continue
                vus.add(u)
                produits.append({"url": BASE + u, "titre": _texte(h3), "latin": _texte(latin),
                                 "dispo": INDISPONIBLE not in reste})
        try:
            sm = self.http.get(f"{BASE}/sitemap.xml").text
            for u in re.findall(r"<loc>https://jardinerie\.fr(/p/[^<]+)</loc>", sm):
                if u not in vus:
                    vus.add(u)
                    produits.append({"url": BASE + u, "titre": u[3:].replace("-", " "), "latin": "", "dispo": True})
        except Exception as e:  # le sitemap n'est qu'un complément
            log.warning("Sitemap illisible : %s", e)
        if len(produits) < 100:
            raise RuntimeError(f"Index trop court ({len(produits)} fiches) : structure des catégories changée ?")
        f = os.path.join(config.DOSSIER_CACHE, f"index_jfr_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        res = []
        for p in self._index():
            if EXCLUS.search(p["titre"]):
                continue
            slug = p["url"].rsplit("/", 1)[-1].replace("-", " ")
            if (M.est_candidat(plante, f"{p['titre']} {_latin(p['latin'])}", slug)
                    or (not p["latin"] and M.candidat_nom_commun(plante, f"{p['titre']} {slug}"))):
                res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def _page(self, url):
        if url not in self.pages:
            t = self.http.get(url).text
            fin = min([i for i in (t.find("Un petit plus pratique"), t.find("Alternatives")) if i > 0] or [len(t)])
            corps = t[:fin]
            formats = []
            for lib, prix in OPTION.findall(corps):
                b = re.search(r'class="[^"]*line-through[^"]*"[^>]*>(.*?)</', prix, re.S)
                spans = [_texte(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", prix, re.S)]
                spans = [x for x in spans if _prix(x) is not None]
                p = _prix(spans[-1]) if spans else _prix(_texte(prix))
                formats.append({"titre": _texte(lib), "prix": p, "barre": _prix(_texte(b.group(1))) if b else None})
            if not formats:  # fiche sans choix de format : prix total affiché
                m = re.search(r"Total:\s*(?:<[^>]+>\s*)*([\d.,]+)", corps)
                if m:
                    formats.append({"titre": "", "prix": _prix(m.group(1)), "barre": None})
            self.pages[url] = {"formats": formats, "indispo": INDISPONIBLE in corps}
        return self.pages[url]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for p in self.candidats(plante):
            o, a = self._analyser(plante, p, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, latin, titre):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        v = M.variete_fiches(plante, latin) or M.variete_fiches(plante, titre)
        if v:
            return v, True
        m = CULTIVAR.search(latin) or CULTIVAR.search(titre)
        if m:
            return m.group(1).strip(), False
        for n in M.noms_latins(plante) + [plante.genre]:
            r = M._apres(latin, n)
            if r is not None:
                r = re.sub(r"[®™«»]", "", r).strip(" '\"‘’()")
                return r, not r
        return "", True

    def _analyser(self, plante, p, aujourdhui):
        url, titre, latin = p["url"], p["titre"], _latin(p["latin"] or p["titre"])
        notes_page = []
        if M.latin_sur_fiche(plante, [latin]):
            equiv = M.equivalence_utilisee(plante, [latin])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, [latin])
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(latin), plante.genre) and M.variete_fiches(plante, latin):
                notes_page.append(f"Espèce absente de la fiche (« {latin} ») ; rapproché par la variété de l'onglet Fiches")
            elif not p["latin"] and M.candidat_nom_commun(plante, titre):
                notes_page.append(f"Nom latin absent de l'index ; rapproché par le nom commun (« {titre} »)")
            else:
                return [], [(url, f"Nom latin « {latin} » différent de {plante.nom_latin}")]
        if M.est_melange(titre):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        try:
            page = self._page(url)
        except InterditParRobots:
            return [], [(url, "Interdit par robots.txt, non lu")]
        except Bloque:
            raise
        except Exception as e:
            return [], [(url, f"Fiche illisible : {e}")]
        variete, connue = self._variete(plante, latin, titre)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        dispo = "Rupture" if (page["indispo"] or not p["dispo"]) else "En stock"

        lignes = []
        for v in page["formats"]:
            if not v["prix"]:
                continue
            notes = list(notes_page)
            fmt = v["titre"]
            cont = contenant(fmt)
            m = HAUTEUR.search(fmt)
            if not cont:
                if m and re.fullmatch(r"\s*\d+\s*-\s*\d+\s*cm\s*", fmt):
                    notes.append("Contenant non précisé (hauteur seule)")
                else:
                    notes.append(f"Format non reconnu : « {fmt} »" if fmt else "Format non indiqué")
            h = f"{m.group(1)}-{m.group(2)} cm" if m else ""
            if v["barre"] and v["barre"] > v["prix"] + 0.005:
                notes.append(f"Promotion (prix barré {prix_fr(v['barre'])} €)")
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(v["prix"]),
                      "EUR", cont, dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, h)
            o.tri = taille_contenant(cont) * 1000 + float(m.group(2) if m else 0) + float(v["prix"]) / 1000
            lignes.append(o)
        if not lignes:
            return [], [(url, "Aucun format ni prix sur la fiche")]
        if dispo == "Rupture":
            return [Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, "", "EUR", "",
                          "Rupture", "", aujourdhui, "", "À vérifier",
                          " ; ".join(notes_page + ["Temporairement indisponible"]), nom_latin, "")], []
        garde, coupe = repartis(lignes, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
