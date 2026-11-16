"""Adaptateur Roué Pépinières (https://rouepepinieres.com, PrestaShop, Bretagne).

Méthode : la recherche (/recherche, ?search_query=, controller=search) est interdite par robots.txt ;
l'index vient du sitemap public (1_index_sitemap.xml -> 1_fr_0_sitemap.xml, environ 2 600 fiches), mis en
cache 7 jours dans cache/. Il donne l'adresse et le titre des images, presque toujours le nom commun
français (« Bananier du Japon », « Pommier d'ornement 'Evereste' ») ; l'adresse cite parfois le genre
(« 1300-bananier-musa-basjoo »). Une fiche est candidate si le titre ou l'adresse contient le genre ou un
nom latin, le genre écrit en français (Passiflore / Passiflora) avec l'espèce ou une variété de Fiches,
ou un nom commun de la BDD.
Chaque fiche candidate est ouverte : le nom latin vient du sous-titre <h2> (« Musa basjoo »,
« Malus Evereste® 'Perpetu' »), qui seul valide le rapprochement. Les formats (« En pot de 3 litres »,
avec parfois « Hauteur livrée 15/20 cm »), prix, prix barrés et disponibilités viennent de la liste des
déclinaisons (ul.color_to_pick_list : une ligne par pot, classe « no_stock » quand le pot est indisponible).
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

NOM = "Roué Pépinières"
PREFIXE = "ROU"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://rouepepinieres.com"
SITEMAPS = [BASE + "/1_fr_0_sitemap.xml"]
DUREE_CACHE_JOURS = 7
# Fiches sans plante (outils, terreaux, cartes cadeaux…), reconnues à leur titre
EXCLUS = re.compile(r"\b(carte cadeau|ch[eè]que cadeau|terreau|engrais|sécateur|secateur|gant|pot en|jardinière|"
                    r"tuteur|paillage|arrosoir|livre)\b", re.I)
LISTE = re.compile(r'<ul id="group_\d+" class="color_to_pick_list">(.*?)</ul>', re.S)
LIGNE = re.compile(r'<li class="([^"]*)">(.*?)</li>', re.S)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


def _prix(txt):
    m = re.search(r"(\d[\d\s.]*,\d{2}|\d+(?:\.\d+)?)\s*€", txt or "")
    if not m:
        return None
    v = m.group(1).replace(" ", "")
    return float(v.replace(".", "").replace(",", ".") if "," in v else v)


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("rouepepinieres", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None
        self.pages = {}

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_rou_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits = []
        for sm in SITEMAPS:
            for bloc in re.findall(r"<url>(.*?)</url>", self.http.get(sm).text, re.S):
                m = re.search(r"<loc><!\[CDATA\[(.*?)\]\]>", bloc)
                # Fiches produit seulement : « /<id>-<nom>.html » avec au moins une image
                if not m or not re.search(r"/\d+-[^/]+\.html$", m.group(1)) or "<image:image>" not in bloc:
                    continue
                titres = sorted({_texte(t) for t in re.findall(r"<image:title><!\[CDATA\[(.*?)\]\]>", bloc)} - {""})
                produits.append({"url": m.group(1), "titre": " | ".join(titres)})
        f = os.path.join(config.DOSSIER_CACHE, f"index_rou_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        noms_communs = [n for n in [plante.nom_commun] + plante.autres_noms if n]
        genre, espece = M.norm(plante.genre), M.norm(plante.espece)
        # Premier mot du nom commun (« Pommier » du Japon) : avec une variété de Fiches, il suffit
        tete = M.norm(plante.nom_commun).split()[:1]
        res = []
        for p in self._index():
            if EXCLUS.search(p["titre"]):
                continue
            slug = re.sub(r"^\d+-|-\d{8,13}$", "", p["url"].rsplit("/", 1)[-1][:-5]).replace("-", " ")
            tn = M.norm(f"{p['titre']} {slug}")
            mots = tn.split()
            if any(M.contient(tn, n) for n in M.noms_latins(plante)) or (genre and M.contient(tn, plante.genre)):
                res.append(p)
                continue
            var = M.variete_fiches(plante, tn)
            # Genre écrit en français (Passiflore) + espèce (à 2 lettres près : « passiflore-adulis ») ou variété de Fiches
            if genre and any(len(w) >= 5 and w[:5] == genre[:5] for w in mots) and (
                    (len(espece) >= 5 and any(M._distance(w, espece) <= 2 for w in mots)) or espece in mots or var):
                res.append(p)
                continue
            if var and tete and len(tete[0]) >= 5 and tete[0] in mots:
                res.append(p)
                continue
            if any(M.contient(tn, n) for n in noms_communs):
                res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def _page(self, url):
        if url not in self.pages:
            t = self.http.get(url).text
            h1 = re.search(r'<h1 class="h1">(.*?)</h1>', t, re.S)
            h2 = re.search(r'</h1>\s*<h2 class="h2">(.*?)</h2>', t, re.S)
            lignes = []
            m = LISTE.search(t)
            for classes, li in LIGNE.findall(m.group(1)) if m else []:
                nom = re.search(r'<span class="attribute-name[^"]*">(.*?)<span class="pastille_info', li, re.S)
                if not nom:
                    nom = re.search(r'title="([^"]*)"', li)
                prix = re.search(r'<div class="price_variant"[^>]*>(.*?)(?:<span class="old_price">|</div>)', li, re.S)
                barre = re.search(r'<span class="old_price">(.*?)</span>', li, re.S)
                dispo = re.search(r'js-product-availability">\s*<span class="([^"]*)"', li)
                lignes.append({"titre": _texte(nom.group(1)) if nom else "",
                               "prix": _prix(_texte(prix.group(1))) if prix else None,
                               "barre": _prix(_texte(barre.group(1))) if barre else None,
                               "dispo": "no_stock" not in classes.split()
                                        and not (dispo and "danger" in dispo.group(1))})
            if not lignes:
                # Fiche sans liste de pots : prix et disponibilité des données produit
                d = re.search(r'id="product-details"\s+data-product="([^"]*)"', t)
                d = json.loads(html.unescape(d.group(1))) if d else {}
                if d.get("price_amount"):
                    attr = " ".join(a.get("name", "") for a in (d.get("attributes") or {}).values())
                    lignes.append({"titre": attr, "prix": d.get("price_amount"),
                                   "barre": d.get("price_without_reduction") if d.get("has_discount") else None,
                                   "dispo": d.get("availability") == "available"})
            self.pages[url] = {"nom": _texte(h1.group(1)) if h1 else "",
                               "latin": _texte(h2.group(1)) if h2 else "", "lignes": lignes}
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
        titre, latin = page["nom"] or url, page["latin"]
        notes_page = []
        if not latin:
            return [], [(url, f"Pas de nom latin sur la fiche « {titre} » : aucun prix relevé")]
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
            h = hauteur(brut) or forme(brut) or ""
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
