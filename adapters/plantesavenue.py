"""Adaptateur Plantes Avenue (https://plantes-avenue.fr, PrestaShop).

Méthode : la recherche (/recherche, ?search_query=) est interdite par robots.txt ; l'index vient des
sitemaps produits (1_fr_0_sitemap.xml, 1_fr_1_sitemap.xml, déclarés dans robots.txt), mis en cache
7 jours dans cache/. Ils donnent l'adresse et le titre des images (nom commun, souvent suivi de
« , Genre »). Les adresses sont en nom commun français (« passiflore-edulis », « corete-du-japon… ») :
une fiche est candidate si le titre ou l'adresse contient le genre (ou un mot qui commence comme lui :
Passiflore / Passiflora) avec l'espèce, un nom latin, ou un nom commun de la BDD.
Chaque fiche candidate est ouverte : le nom latin vient des caractéristiques Genre / Espèce / Variété
(JSON data-product), qui seules valident le rapprochement ; les formats (« Conditionnement »), prix
et disponibilités viennent du tableau des déclinaisons (#product_combinations).
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
from core.http import InterditParRobots, ClientPoli

log = logging.getLogger("scraper")

NOM = "Plantes Avenue"
PREFIXE = "PAV"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://plantes-avenue.fr"
SITEMAPS = [BASE + "/1_fr_0_sitemap.xml", BASE + "/1_fr_1_sitemap.xml"]
DUREE_CACHE_JOURS = 7
# Rubriques sans plante
RUBRIQUES_EXCLUES = {"engrais", "arrosage", "accessoires", "outillage", "terreaux", "paillages", "soins-des-plantes",
                     "cheques-cadeaux", "gazon", "livre", "coachings-amenagement"}
DATA_PRODUIT = re.compile(r'id="product-details" data-product="([^"]*)"')
LIGNE = re.compile(r'<tr class="product-combinations-line">(.*?)</tr>', re.S)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


def _prix(txt):
    m = re.search(r"(\d[\d\s.]*,\d{2}|\d+(?:\.\d+)?)\s*€", txt or "")
    if not m:
        return None
    return float(m.group(1).replace(" ", "").replace(".", "").replace(",", ".") if "," in m.group(1)
                 else m.group(1))


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("plantesavenue", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None
        self.pages = {}

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_pav_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits = []
        for sm in SITEMAPS:
            for bloc in re.findall(r"<url>(.*?)</url>", self.http.get(sm).text, re.S):
                m = re.search(r"<loc><!\[CDATA\[(.*?)\]\]>", bloc)
                if not m or not m.group(1).endswith(".html") or "/content/" in m.group(1):
                    continue
                url = m.group(1)
                titres = sorted({_texte(t) for t in re.findall(r"<image:title><!\[CDATA\[(.*?)\]\]>", bloc)})
                produits.append({"url": url, "rubrique": url.split("/")[3], "titre": " | ".join(titres)})
        f = os.path.join(config.DOSSIER_CACHE, f"index_pav_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        noms_communs = [plante.nom_commun] + plante.autres_noms
        genre, espece = M.norm(plante.genre), M.norm(plante.espece)
        res = []
        for p in self._index():
            if p["rubrique"] in RUBRIQUES_EXCLUES:
                continue
            slug = re.sub(r"^\d+-|-\d{8,13}$", "", p["url"].rsplit("/", 1)[-1][:-5]).replace("-", " ")
            tn = M.norm(f"{p['titre']} {slug}")
            mots = tn.split()
            if any(M.contient(tn, n) for n in M.noms_latins(plante)) or M.contient(tn, plante.genre):
                res.append(p)
                continue
            # Genre écrit en français (Passiflore) + espèce
            # (ou + variété de l'onglet Fiches : « Passiflore à fruits flavicarpa »)
            if any(len(w) >= 5 and w[:5] == genre[:5] for w in mots) and (
                    (espece and espece in mots) or M.variete_fiches(plante, tn)):
                res.append(p)
                continue
            # Nom commun, sauf si le titre se termine par un autre genre (« Spirée du Japon …, Spiraea »)
            fin = re.search(r",\s*([A-Z][a-z]+)\s*(?:\||$)", p["titre"])
            if fin and M.norm(fin.group(1))[:5] != genre[:5]:
                continue
            if any(M.contient(tn, n) for n in noms_communs):
                res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    def _page(self, url):
        if url not in self.pages:
            t = self.http.get(url).text
            m = DATA_PRODUIT.search(t)
            d = json.loads(html.unescape(m.group(1))) if m else {}
            car = {f.get("name"): _texte(str(f.get("value") or "")) for f in d.get("features") or []}
            i = t.find('id="product_combinations"')
            j = t.find("</table>", i) if i >= 0 else -1
            lignes = []
            for r in LIGNE.findall(t[i:j]) if i >= 0 else []:
                nom = re.search(r'<span class="combination-name">(.*?)</span>', r, re.S)
                prix = re.search(r'<span class="price">(.*?)</span>', r, re.S)
                barre = re.search(r'class="regular-price[^"]*">(.*?)</', r, re.S)
                lignes.append({"titre": _texte(nom.group(1)) if nom else "",
                               "prix": _prix(_texte(prix.group(1))) if prix else None,
                               "barre": _prix(_texte(barre.group(1))) if barre else None,
                               "dispo": 'class="instock"' in r})
            if not lignes and d.get("price_amount"):
                attr = " ".join(a.get("name", "") for a in (d.get("attributes") or {}).values())
                lignes.append({"titre": attr, "prix": d.get("price_amount"),
                               "barre": d.get("price_without_reduction") if d.get("has_discount") else None,
                               "dispo": d.get("availability") == "available" and (d.get("quantity") or 0) > 0})
            self.pages[url] = {"nom": _texte(d.get("name") or ""), "car": car, "lignes": lignes}
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

    def _analyser(self, plante, url, page, aujourdhui):
        titre, car = page["nom"] or url, page["car"]
        genre, espece, var_site = car.get("Genre", ""), car.get("Espèce", ""), car.get("Variété", "")
        # Fiche où l'épithète est rangée dans « Variété » (ex. Crithmum / maritimum) : la prendre comme espèce
        if not espece and M.norm(var_site) == M.norm(plante.espece) and M.norm(genre) == M.norm(plante.genre):
            espece, var_site = var_site, ""
        latin = " ".join(x for x in (genre, espece) if x)
        notes_page = []
        if not latin:
            return [], [(url, f"Pas de nom latin sur la fiche « {titre} » : aucun prix relevé")]
        if M.latin_sur_fiche(plante, [latin]):
            equiv = M.equivalence_utilisee(plante, [latin])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, [latin])
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif (M.norm(genre) == M.norm(plante.genre) and not M.norm(espece)
                  and M.variete_fiches(plante, f"{var_site} {titre}")):
                notes_page.append(f"Espèce absente de la fiche (« {latin} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                ecrit = f"{latin} {var_site}".strip()
                return [], [(url, f"Nom latin « {ecrit} » (fiche « {titre} ») différent de {plante.nom_latin}")]
        if M.est_melange(titre):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        variete = M.variete_fiches(plante, var_site) or M.variete_fiches(plante, titre)
        if not variete and var_site:
            variete = var_site.strip(" '\"‘’")
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
            # « Pot de 3L - Echelle bambou 60/120 cm » : le tuteur n'est pas une hauteur de plante
            h = hauteur(brut) or forme(brut) or ""
            if re.search(r"tuteur|[ée]chelle|bambou", brut, re.I):
                notes.append(re.sub(r"^.*?[-_]\s*", "", brut, count=1).strip() or brut)
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
