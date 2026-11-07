"""Adaptateur Arborescence (https://www.arborescence.ma), pépinière au Maroc, prix en dirhams (MAD).

Méthode : sitemap public des produits (`/sitemap-products.xml`, un <image:title> « NOM LATIN (NOM COMMUN) »
par fiche) pour trouver les fiches, puis la variable `window.__variants__` de chaque fiche (un format par
variante : contenant, taille, prix, prix d'origine, promotion). Pas de JSON-LD sur le site.

Particularités :
- une page regroupe souvent plusieurs entrées du sitemap (chaque variante a son propre « product_id ») :
  les fiches déjà vues dans une page précédente ne sont pas relues ;
- le site n'affiche pas le stock (« dans la limite des stocks disponibles ») : tout format vendu
  est noté « En stock », avec une mention dans Notes.
"""
import datetime as dt
import html
import json
import logging
import os
import re

import config
from core import matching as M
from core.formatting import Offre, prix_fr, repartis
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Arborescence"
BASE = "https://www.arborescence.ma"
SITEMAP = BASE + "/sitemap-products.xml"
DEVISE = "MAD"
NOTE_STOCK = "Stock non affiché par le site (vendu « dans la limite des stocks disponibles »)"
ID_PRODUIT = re.compile(r"/produit/(\d+)/")


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def contenant_arbo(c):
    """« CT.10L » -> « Pot 10 L » ; « Sachet ou Godet » -> « Godet » ; « Standard » -> ''."""
    c = (c or "").strip()
    m = re.match(r"(?:CT|C)\.?\s*([\d.,]+)\s*L\b", c, re.I)
    if m:
        return f"Pot {m.group(1).replace('.', ',')} L"
    m = re.match(r"(?:P|POT)\.?\s*([\d.,]+)\s*CM\b", c, re.I)
    if m:
        return f"Pot {m.group(1)} cm"
    cl = c.lower()
    if "godet" in cl:
        return "Godet"
    if "motte" in cl:
        return "Motte"
    if "racine" in cl:
        return "Racines nues"
    if "sachet" in cl or "graine" in cl:
        return "Sachet de graines"
    return ""


def taille_arbo(t):
    """« HT.80-100Cm » -> « 80-100 cm » ; « HT.90Cm » -> « 90 cm » ; autre -> texte brut."""
    t = (t or "").strip()
    m = re.match(r"(?:HT|H)\.?\s*([\d.,]+)\s*(?:-\s*([\d.,]+))?\s*(cm|m)\b", t, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)} {m.group(3).lower()}" if m.group(2) else f"{m.group(1)} {m.group(3).lower()}"
    return t


def _deplier(titre):
    """« PASSIFLORA EDULIS / QUADRANGULARIS » -> « PASSIFLORA EDULIS / PASSIFLORA QUADRANGULARIS »."""
    latin = re.sub(r"\([^)]*\)", " ", titre)
    parties = [p.strip() for p in latin.split("/") if p.strip()]
    if len(parties) < 2 or not parties[0].split():
        return titre
    genre = parties[0].split()[0]
    return titre + " / " + " / ".join(f"{genre} {p}" for p in parties[1:])


def _litres(cont):
    m = re.search(r"Pot ([\d,]+) L", cont)
    return float(m.group(1).replace(",", ".")) if m else (0.3 if cont == "Godet" else 0.0)


class Adaptateur:
    def __init__(self):
        self.http = ClientPoli(BASE)
        self.entrees = None  # [[url, titre]]
        self.url_par_id = {}

    # ---------- sitemap ----------
    def _sitemap(self):
        if self.entrees is not None:
            return self.entrees
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        cache = os.path.join(config.DOSSIER_CACHE, f"sitemap_arbo_{dt.date.today():%Y-%m-%d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                entrees = json.load(f)
            log.info("Sitemap lu depuis le cache %s (%d URL)", cache, len(entrees))
        else:
            xml = self.http.get(SITEMAP).text
            entrees = []
            for bloc in re.findall(r"<url>(.*?)</url>", xml, re.S):
                u = re.search(r"<loc>([^<]+)</loc>", bloc)
                t = re.search(r"<image:title>([^<]*)</image:title>", bloc)
                if u and "/produit/" in u.group(1):
                    entrees.append([html.unescape(u.group(1)).strip(), html.unescape(t.group(1)).strip() if t else ""])
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(entrees, f, ensure_ascii=False)
            log.info("Sitemap : %d fiches produit", len(entrees))
        self.entrees = entrees
        for url, _ in entrees:
            m = ID_PRODUIT.search(url)
            if m:
                self.url_par_id.setdefault(m.group(1), url)
        return entrees

    def candidats(self, plante):
        vus, res = set(), []
        for url, titre in self._sitemap():
            mode = M.est_candidat(plante, _deplier(titre), url)
            if mode and url not in vus:
                vus.add(url)
                res.append((url, titre, mode))
        if not res:  # repli : nom commun et autres noms (le nom latin est vérifié sur la fiche)
            for url, titre in self._sitemap():
                if titre and M.candidat_nom_commun(plante, titre) and url not in vus:
                    vus.add(url)
                    res.append((url, titre, "nom commun"))
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    @staticmethod
    def _variantes(page):
        m = re.search(r"window\.__variants__\s*=\s*(\[.*?\]);\s*$", page, re.M)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except ValueError:
            return None

    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        ids_vus = set()
        cands = self.candidats(plante)
        log.info("%s %s : %d fiche(s) candidate(s)", plante.id, plante.nom_latin, len(cands))
        for url, titre_sm, mode in cands:
            m = ID_PRODUIT.search(url)
            if m and m.group(1) in ids_vus:
                continue  # variante d'une page déjà lue
            try:
                page = self.http.get(url).text
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            variantes = self._variantes(page) or []
            ids_vus.update(str(v.get("product_id")) for v in variantes)
            if m:
                ids_vus.add(m.group(1))
            o, a = self._analyser(plante, url, page, variantes, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", DEVISE, "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _analyser(self, plante, url, page, variantes, aujourdhui):
        h1 = " ".join(_texte(x) for x in re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S))
        if not variantes:
            return [], [(url, f"Pas de formats en vente (window.__variants__) sur « {h1} »")]
        # Titre « NOM LATIN (NOM COMMUN) » ; parfois deux espèces : « PASSIFLORA EDULIS / QUADRANGULARIS »
        latin_h1 = re.sub(r"\([^)]*\)", " ", h1).strip()
        parties = [p.strip() for p in latin_h1.split("/") if p.strip()]
        if len(parties) > 1 and parties[0].split():
            genre_h1 = parties[0].split()[0]
            parties = [parties[0]] + [p if len(p.split()) > 1 and p.split()[0].lower() == genre_h1.lower()
                                      else f"{genre_h1} {p}" for p in parties[1:]]
        textes = [h1] + parties
        notes_page = []
        if len(parties) > 1:
            notes_page.append(f"Fiche à plusieurs espèces : « {h1} »")
        equiv = M.equivalence_utilisee(plante, textes)
        approche = None if M.latin_sur_fiche(plante, textes) else M.latin_approche(plante, textes)
        if equiv:
            notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if approche:
            notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
        elif not M.latin_sur_fiche(plante, textes):
            var = M.variete_fiches(plante, latin_h1)
            if M.contient(M.norm(latin_h1), plante.genre) and var:
                notes_page.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                                  f"rapproché par la variété {var} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {h1} » : aucun prix relevé")]
        if M.est_melange(h1):
            notes_page.append(f"Lot/mélange de la même espèce (« {h1} »)")

        # La variété éventuelle est dans la partie latine qui correspond à la plante
        partie = next((p for p in parties if M.latin_sur_fiche(plante, [p])), parties[0] if parties else latin_h1)
        variete, connue = M.variete_du_titre(plante, partie)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        lignes = []
        for v in variantes:
            notes = list(notes_page)
            brut_c = (v.get("contenant") or "").strip()
            cont = contenant_arbo(brut_c)
            if not cont:
                notes.append("Contenant non précisé" + (f" (« {brut_c} »)" if brut_c and brut_c != "Standard" else ""))
            elif brut_c.lower() == "sachet ou godet":
                notes.append("Contenant indiqué « Sachet ou Godet » sur le site")
            taille = taille_arbo(v.get("taille"))
            if v.get("couleur"):
                notes.append(f"Couleur : {v['couleur']}")
            if v.get("fm") and v["fm"] != "Standard":
                notes.append(f"Forme : {v['fm']}")
            prix, orig = v.get("price"), v.get("original_price")
            try:
                if v.get("promo") and orig and float(orig) > float(prix):
                    notes.append(f"Prix barré avant promotion : {prix_fr(orig)}")
            except ValueError:
                pass
            notes.append(NOTE_STOCK)
            lien = self.url_par_id.get(str(v.get("product_id")), url)
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, lien, prix_fr(prix), DEVISE,
                      cont, "En stock", "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, taille)
            o.tri = _litres(cont) * 1000 + float(prix or 0)
            lignes.append(o)

        garde, coupe = repartis(lignes, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
