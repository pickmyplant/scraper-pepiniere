"""Adaptateur Le Jardin de Pascal (https://le-jardin-de-pascal.com, PrestaShop, arbres, arbustes, grimpantes).

Méthode : la recherche (/recherche, controller=search) est interdite par robots.txt et le sitemap déclaré
pointe vers un autre domaine ; l'index vient donc de la catégorie racine du catalogue
(/2-category-2-shop-2-fr?resultsPerPage=200&page=N, environ 1 400 fiches en 7 pages), mis en cache 7 jours.
Le titre des fiches est « Nom latin ‘Variété’ (Nom commun ‘Variété’) ». Une fiche est candidate si son titre
ou son adresse contient le genre, un nom latin ou un nom commun de la BDD.
Chaque fiche candidate est ouverte : le nom latin est la partie du titre <h1> avant la parenthèse, ou\ndans la parenthèse pour les fiches écrites « Nom commun (Nom latin) ». Les formats
(« En pot de 10-12 litres buisson hauteur (hors pot) 060-080 cm »), prix et prix barrés viennent du tableau
des déclinaisons (module displaycombinations : une ligne <tr id="comb_…"> par format en vente ; les formats
indisponibles n'y figurent pas). Une fiche sans tableau prend les données produit (data-product).
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
from core.formatting import Offre, contenant, forme, hauteur, lot, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Le Jardin de Pascal"
PREFIXE = "JDP"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://le-jardin-de-pascal.com"
CATALOGUE = BASE + "/2-category-2-shop-2-fr?resultsPerPage=200&page={n}"
DUREE_CACHE_JOURS = 7
# Fiches sans plante (outils, terreaux, cartes cadeaux…), reconnues à leur titre
EXCLUS = re.compile(r"\b(carte cadeau|ch[eè]que cadeau|terreau|engrais|sécateur|secateur|gant|tuteur|paillage|"
                    r"arrosoir|livre)\b", re.I)
MINIATURE = re.compile(r'<h3 class="product-title[^"]*"[^>]*>\s*<a href="([^"#]+)[^"]*"[^>]*>(.*?)</a>', re.S)
LIGNE = re.compile(r'<tr id="comb_\d+"[^>]*>(.*?)</tr>', re.S)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


def _prix(txt):
    m = re.search(r"(\d[\d\s.]*,\d{2}|\d+(?:\.\d+)?)\s*€", txt or "")
    if not m:
        return None
    v = m.group(1).replace(" ", "")
    return float(v.replace(".", "").replace(",", ".") if "," in v else v)


def _sans_zeros(txt):
    """« 060-080 cm » -> « 60-80 cm »."""
    return re.sub(r"\b0+(\d)", r"\1", txt or "")


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("lejardindepascal", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None
        self.pages = {}

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_jdp_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits, vus, n = [], set(), 1
        while n <= 30:
            t = self.http.get(CATALOGUE.format(n=n)).text
            nouveaux = 0
            for u, titre in MINIATURE.findall(t):
                if u not in vus:
                    vus.add(u)
                    produits.append({"url": u, "titre": _texte(titre)})
                    nouveaux += 1
            if not nouveaux or f"page={n + 1}" not in t:
                break
            n += 1
        if not produits:
            raise RuntimeError("Catalogue vide : structure de la page catégorie changée ?")
        f = os.path.join(config.DOSSIER_CACHE, f"index_jdp_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        noms_communs = [n for n in [plante.nom_commun] + plante.autres_noms if n]
        res = []
        for p in self._index():
            if EXCLUS.search(p["titre"]):
                continue
            slug = re.sub(r"^\d+-\d+-", "", p["url"].rsplit("/", 1)[-1][:-5]).replace("-", " ")
            tn = M.norm(f"{p['titre']} {slug}")
            if (any(M.contient(tn, n) for n in M.noms_latins(plante))
                    or (plante.genre and M.contient(tn, plante.genre))
                    or any(M.contient(tn, n) for n in noms_communs)):
                res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def _page(self, url):
        if url not in self.pages:
            t = self.http.get(url).text
            h1 = re.search(r'<h1[^>]*id="product-title"[^>]*>(.*?)</h1>', t, re.S)
            nom = _texte(h1.group(1)) if h1 else ""
            # « Nom latin (Nom commun) » le plus souvent, parfois « Nom commun (Nom latin) »
            m = re.match(r"(.*?)\s*\((.*)\)\s*$", nom)
            latin = [m.group(1).strip(), m.group(2).strip()] if m else [nom]
            lignes = []
            for tr in LIGNE.findall(t):
                fmt = [_texte(x) for x in re.findall(r'<p class="d_c_datas"[^>]*>(.*?)</p>', tr, re.S)]
                fmt = next((re.sub(r"^-?\s*:\s*", "", x) for x in fmt if x and not x.startswith("Prix")), "")
                prix = re.search(r'<td class="td_price[^"]*">(.*?)</td>', tr, re.S)
                prix = prix.group(1) if prix else tr
                p = re.search(r'our_price_display product-price">(.*?)</span>', prix, re.S)
                b = re.search(r'class="prixBarre[^"]*">(.*?)</span>', prix, re.S)
                lignes.append({"titre": fmt, "prix": _prix(_texte(p.group(1))) if p else None,
                               "barre": _prix(_texte(b.group(1))) if b else None,
                               "dispo": "add-to-cart" in tr and "disabled" not in tr})
            if not lignes:
                d = re.search(r'data-product="([^"]*)"', t)
                d = json.loads(html.unescape(d.group(1))) if d else {}
                if d.get("price_amount"):
                    attr = " ".join(a.get("name", "") for a in (d.get("attributes") or {}).values())
                    lignes.append({"titre": attr, "prix": d.get("price_amount"),
                                   "barre": d.get("price_without_reduction") if d.get("has_discount") else None,
                                   "dispo": d.get("availability") != "unavailable"})
            self.pages[url] = {"nom": nom, "latin": latin, "lignes": lignes}
        return self.pages[url]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for p in self.candidats(plante):
            url = p["url"]
            try:
                page = self._page(url)
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
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, latin, titre):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        v = M.variete_fiches(plante, latin) or M.variete_fiches(plante, titre)
        if v:
            return v, True
        for n in M.noms_latins(plante) + [plante.genre]:
            reste = M._apres(latin, n)
            if reste is not None:
                reste = re.sub(r"[®™]", "", reste).strip(" '\"‘’()")
                return reste, not reste
        return "", True

    def _analyser(self, plante, url, page, aujourdhui):
        titre = page["nom"] or url
        parties = [re.sub(r"\s+", " ", x.replace("×", " ")).strip() for x in page["latin"] if x] or [titre]
        # La partie qui porte le nom latin : celle qui correspond à la plante, sinon celle qui commence par le genre
        latin = next((x for x in parties if M.latin_sur_fiche(plante, [x]) or M.latin_approche(plante, [x])),
                     next((x for x in parties if M.norm(x).split()[:1] == [M.norm(plante.genre)]), parties[0]))
        notes_page = []
        if M.latin_sur_fiche(plante, [latin]):
            equiv = M.equivalence_utilisee(plante, [latin])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, [latin])
            mots = M.norm(latin).split()
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif mots[:1] == [M.norm(plante.genre)] and M.variete_fiches(plante, f"{latin} {titre}"):
                notes_page.append(f"Espèce absente de la fiche (« {latin} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {latin} » (fiche « {titre} ») différent de {plante.nom_latin}")]
        if M.est_melange(titre):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, latin, titre)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        lignes = []
        for v in page["lignes"]:
            notes = list(notes_page)
            brut = v["titre"]
            cont = contenant(brut)
            nb = lot(brut)
            if nb:
                notes.append(f"lot de {nb}")
            if not cont:
                notes.append(f"Format non reconnu : « {brut} »" if brut else "Format non indiqué")
            if v["barre"] and v["prix"] and v["barre"] > v["prix"]:
                notes.append(f"Promotion (prix barré {prix_fr(v['barre'])} €)")
            h = _sans_zeros(hauteur(brut) or "") or forme(brut) or ""
            if re.search(r"hauteur du tronc", brut, re.I):
                # Arbre en tige : la hauteur donnée est celle du tronc, pas de la plante
                h = f"Tige, tronc {h}"
            for mot, lib in [("circonf[ée]rence du tronc", "Circonférence du tronc"),
                             ("diam[èe]tre de la t[êe]te", "Diamètre de la tête")]:
                m = re.search(mot + r"\s*([\d/ -]+?)\s*cm", brut, re.I)
                if m:
                    notes.append(f"{lib} {_sans_zeros(m.group(1).strip())} cm")
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(v["prix"]),
                      "EUR", cont, "En stock" if v["dispo"] else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, h)
            o.tri = taille_contenant(cont) * 1000 + float(v["prix"] or 0)
            lignes.append(o)
        if not lignes:
            return [], [(url, "Aucun format sur la fiche")]
        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            return [Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, "", "EUR", "",
                          "Rupture", "", aujourdhui, "", "À vérifier",
                          " ; ".join(notes_page + ["Aucun format en stock"]), nom_latin, "")], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
