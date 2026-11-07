"""Adaptateur Clorofila (https://www.clorofila.ma), pépinière au Maroc (WooCommerce), prix en dirhams (MAD).

Méthode : sitemap Yoast des produits (`/product-sitemap.xml`, URL seules, sans titre) ; les fiches sont
présélectionnées sur le slug (nom latin, ou nom commun / autres noms, souvent en français seul :
« avocatier-zutano »), puis lues : JSON-LD Product (nom, description où figure en général le nom latin,
offre) et `data-product_variations` (un format par variante : hauteur, circonférence…, prix, stock).
L'API Store de WooCommerce (/wp-json/wc/store/…) répond 403 : elle n'est pas utilisée.

Particularités :
- certaines fiches ne donnent que le genre (« Bananier (musa) », « Nom scientifique : Musa ») : gardées
  si le nom commun de la plante est dans le titre, avec une mention dans Notes ;
- le contenant n'est presque jamais indiqué ; la hauteur vient de l'attribut « hauteur ».
"""
import datetime as dt
import html
import json
import logging
import os
import re

import config
from core import matching as M
from core.formatting import Offre, contenant, prix_fr, repartis
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Clorofila"
BASE = "https://www.clorofila.ma"
SITEMAP = BASE + "/product-sitemap.xml"
DEVISE = "MAD"
ATTR_HAUTEUR = ("hauteur", "taille")
FICHES_VALIDEES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fiches_validees.csv")


def _fiches_validees():
    """(ID, URL) des fiches sans nom latin acceptées par l'utilisatrice (data/fiches_validees.csv)."""
    import csv
    try:
        with open(FICHES_VALIDEES, newline="", encoding="utf-8") as f:
            return {(r["ID"].strip(), r["URL"].strip().rstrip("/")) for r in csv.DictReader(f)}
    except FileNotFoundError:
        return set()


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _slug(url):
    return url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")


def _nom_attr(cle):
    """« attribute_pa_hauteur » -> « hauteur »."""
    n = re.sub(r"^(attribute_)?(pa_)?", "", cle).replace("-", " ").replace("_", " ")
    return {"circonference": "circonférence"}.get(n, n)


class Adaptateur:
    def __init__(self):
        self.http = ClientPoli(BASE)
        self.urls = None

    # ---------- sitemap ----------
    def _sitemap(self):
        if self.urls is not None:
            return self.urls
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        cache = os.path.join(config.DOSSIER_CACHE, f"sitemap_clorofila_{dt.date.today():%Y-%m-%d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                self.urls = json.load(f)
            log.info("Sitemap lu depuis le cache %s (%d URL)", cache, len(self.urls))
            return self.urls
        xml = self.http.get(SITEMAP).text
        urls = [html.unescape(u).strip() for u in re.findall(r"<loc>([^<]+/produit/[^<]+)</loc>", xml)]
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(urls, f, ensure_ascii=False)
        log.info("Sitemap : %d fiches produit", len(urls))
        self.urls = urls
        return urls

    def candidats(self, plante):
        """Union : nom latin dans le slug, ou nom commun / autres noms (le latin est vérifié sur la fiche)."""
        res, vus = [], set()
        for url in self._sitemap():
            mode = M.est_candidat(plante, "", url.rstrip("/"))
            if not mode and M.candidat_nom_commun(plante, _slug(url)):
                mode = "nom commun"
            if mode and url not in vus:
                vus.add(url)
                res.append((url, mode))
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    @staticmethod
    def _produit(page):
        for bloc in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                d = json.loads(bloc)
            except ValueError:
                continue
            for n in d.get("@graph", [d]) if isinstance(d, dict) else d:
                if isinstance(n, dict) and n.get("@type") == "Product":
                    return n
        return None

    @staticmethod
    def _variations(page):
        m = re.search(r'data-product_variations="([^"]*)"', page)
        if not m:
            return None
        try:
            v = json.loads(html.unescape(m.group(1)))
        except ValueError:
            return None
        return v if isinstance(v, list) else None

    @staticmethod
    def _libelles(page):
        """{attribut: {valeur: libellé}} d'après les listes déroulantes de la fiche."""
        res = {}
        for nom, corps in re.findall(r'<select[^>]*name="(attribute_[^"]+)"[^>]*>(.*?)</select>', page, re.S):
            res[nom] = {v: _texte(t) for v, t in re.findall(r'<option value="([^"]+)"[^>]*>(.*?)</option>', corps, re.S)}
        return res

    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        cands = self.candidats(plante)
        log.info("%s %s : %d fiche(s) candidate(s)", plante.id, plante.nom_latin, len(cands))
        for url, mode in cands:
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
            o, a = self._analyser(plante, url, page, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", DEVISE, "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, nom, desc):
        """Cultivar entre apostrophes après le nom latin dans la description, sinon d'après le titre."""
        for n in M.noms_latins(plante):
            m = re.search(re.escape(n) + r"\s*['‘’]([^'‘’\n]{2,40})['‘’]", desc, re.I)
            if m:
                v = m.group(1).strip()
                connue = M.variete_fiches(plante, v)
                return (connue, True) if connue else (v, False)
        # Titre sans les noms latins (« Manguier mangifera indica », « Bananier (musa) »)
        titre = nom
        approche = M.latin_approche(plante, [nom])  # « Avocatier persea america »
        for n in sorted(M.noms_latins(plante) + [plante.genre] + ([approche] if approche else []),
                        key=len, reverse=True):
            titre = re.sub(r"\(?\s*" + re.escape(n) + r"\s*\)?", " ", titre, flags=re.I)
        titre = re.sub(r"\([^()]*\)", " ", titre)  # parenthèses : nom latin, parfois mal écrit (« psidum »)
        titre = re.sub(r"\(\s*\)|\s+", " ", titre).strip(" -–,()")
        v, connue = M.variete_du_titre(plante, titre)
        if v and not connue:
            if titre.lower() == v.lower() or len(v.split()) > 3:
                return "", True  # rien d'identifiable après le nom commun
            v = v[:1].upper() + v[1:]
        return v, connue

    def _analyser(self, plante, url, page, aujourdhui):
        p = self._produit(page)
        if not p:
            return [], [(url, "Pas de données produit (JSON-LD)")]
        nom = _texte(p.get("name", ""))
        desc = html.unescape(p.get("description", "") or "")
        textes = [nom, _slug(url), desc[:3000]]
        notes_page = []

        equiv = M.equivalence_utilisee(plante, textes)
        approche = None if M.latin_sur_fiche(plante, textes) else M.latin_approche(plante, textes)
        if equiv:
            notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if approche:
            notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
        elif not M.latin_sur_fiche(plante, textes):
            tn = M.norm(" ".join(textes))
            var = M.variete_fiches(plante, nom)
            if M.contient(tn, plante.genre) and var:
                notes_page.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                                  f"rapproché par la variété {var} de l'onglet Fiches")
            elif (plante.id, url.rstrip("/")) in _fiches_validees():
                notes_page.append(f"Nom latin absent de la fiche « {nom} » ; fiche validée par l'utilisatrice "
                                  "(data/fiches_validees.csv)")
            elif M.contient(tn, plante.genre) and M.candidat_nom_commun(plante, nom):
                notes_page.append(f"Espèce non précisée sur la fiche (genre {plante.genre} seul, "
                                  f"titre « {nom} ») : à confirmer")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {nom} » : aucun prix relevé")]
        if M.est_melange(nom):
            notes_page.append(f"Lot/mélange de la même espèce (« {nom} »)")

        variete, connue = self._variete(plante, nom, desc)
        if variete and not connue and re.fullmatch(r"[A-Z][a-z]+ [a-z]+", variete) \
                and variete.split()[0].lower() != plante.genre.lower():
            return [], [(url, f"Autre plante sur la fiche « {nom} » ({variete}) : aucun prix relevé")]
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        variete_col = variete or "Espèce type"

        # Formats : variantes WooCommerce, sinon l'offre unique du JSON-LD
        formats = []  # (attributs {nom: libellé}, prix, prix_regulier, en_stock)
        variations = self._variations(page)
        if variations:
            lib = self._libelles(page)
            for v in variations:
                if not v.get("variation_is_active", True):
                    continue
                attrs = {_nom_attr(k): lib.get(k, {}).get(val, val.replace("-", " ")) if val else "toutes"
                         for k, val in (v.get("attributes") or {}).items()}
                formats.append((attrs, v.get("display_price"), v.get("display_regular_price"),
                                bool(v.get("is_in_stock"))))
        else:
            offres_ld = p.get("offers") or []
            for of in offres_ld if isinstance(offres_ld, list) else [offres_ld]:
                if of.get("@type") == "AggregateOffer":
                    continue
                formats.append(({}, of.get("price"), None, str(of.get("availability", "")).endswith("InStock")))
        if not formats:
            return [], [(url, f"Aucun prix lisible sur « {nom} »")]

        lignes = []
        for attrs, prix, regulier, stock in formats:
            notes = list(notes_page)
            haut = re.sub(r"(\d)\.(\d)", r"\1,\2", next((attrs.pop(a) for a in ATTR_HAUTEUR if a in attrs), ""))
            cont = ""
            for a in list(attrs):
                c = contenant(f"{a} {attrs[a]}")
                if c and c != "Pot":
                    cont = c
                    attrs.pop(a)
                    break
            notes += [f"{a[:1].upper() + a[1:]} : {val}" for a, val in attrs.items()]
            try:
                if regulier and prix and float(regulier) > float(prix):
                    notes.append(f"Prix barré avant promotion : {prix_fr(regulier)}")
            except ValueError:
                pass
            if not cont:
                notes.append("Contenant non précisé")
            o = Offre(plante.id, plante.nom_commun, variete_col, NOM, url, prix_fr(prix), DEVISE, cont,
                      "En stock" if stock else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, haut)
            o.tri = float(prix or 0)
            lignes.append(o)

        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            n = " ; ".join(notes_page + ["Aucun format en stock"])
            return [Offre(plante.id, plante.nom_commun, variete_col, NOM, url, "", DEVISE, "", "Rupture", "",
                          aujourdhui, "", "À vérifier", n, nom_latin, "")], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
