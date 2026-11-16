"""Adaptateur Promofleur (https://www.promofleur.fr, PrestaShop, jardinerie : oliviers, palmiers, plantes méditerranéennes).

Méthode : la recherche (/search, controller=search) est interdite par robots.txt et le sitemap public
(1_fr_0_sitemap.xml) est figé depuis 2024 (les fiches récentes n'y sont pas). L'index vient donc des pages
catégories du menu (pages ?page=N autorisées), mis en cache 7 jours dans cache/. Les catégories sans plantes
(fleuristerie, pots, mobilier, animaux, sapins coupés…) ne sont pas lues.
Une fiche = un format (taille dans le titre : « Hauteur 110/120 cm », « C45 L », « Stipe 20/40 cm »).
Chaque fiche candidate est ouverte : nom latin dans le titre <h1> ou la description courte (« Olivier
centenaire, Olea europaea »), qui seul valide le rapprochement. Prix et disponibilité viennent des données
produit (div#product-details data-product).

Livraison : seules les fiches qu'on peut mettre dans le panier (available_for_order = 1) reçoivent un prix.
Les autres (« Disponible uniquement en magasin », sans bouton panier) donnent une ligne sans prix, notée
« à récupérer à la pépinière ».
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
from core.formatting import Offre, contenant, hauteur, prix_fr
from core.http import InterditParRobots, ClientPoli

log = logging.getLogger("scraper")

NOM = "Promofleur"
PREFIXE = "PMF"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.promofleur.fr"
DUREE_CACHE_JOURS = 7
# Catégories du menu sans plantes à vendre (adresse « /<id>-<nom> »)
CATEGORIES_EXCLUES = re.compile(
    r"bouquet|deuil|mariage|fleuristerie|salon|barbecue|chiens|veterinaire|pot|toscane|graphit|muret|bac-|"
    r"jardiniere|statue|gravier|basalt|ecorce|terreau|jardinage|amenagement|artificiel|floque|sapin-coupe|"
    r"sapins-de-noel")
# Fiches sans plante (outils, terreaux, cartes cadeaux…), reconnues à leur titre
EXCLUS = re.compile(r"\b(carte cadeau|terreau|engrais|croquettes?|royal canin|pot |jardini[eè]re|bougie|"
                    r"coupé|artificiel|floqué)\b", re.I)
MAGASIN = "Non livrable : disponible uniquement en magasin, à récupérer à la pépinière (pas d'ajout au panier)"


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("promofleur", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None
        self.pages = {}

    # ---------- index du catalogue (pages catégories) ----------
    def _categories(self):
        t = self.http.get(BASE + "/").text
        cats = sorted(set(re.findall(r'href="(https://www\.promofleur\.fr/\d+-[a-z0-9-]+)"', t)))
        return [c for c in cats if not CATEGORIES_EXCLUES.search(c.rsplit("/", 1)[-1])]

    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_pmf_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits = {}
        for cat in self._categories():
            page = 1
            while page <= 30:
                t = self.http.get(cat + (f"?page={page}" if page > 1 else "")).text
                nouveaux = 0
                for bloc in re.split(r"js-product-miniature-wrapper-", t)[1:]:
                    bloc = bloc.split("</article>")[0]
                    m = re.search(r'product-title">\s*<a href="([^"#]+)[^"]*"[^>]*>(.*?)</a>', bloc, re.S)
                    ident = re.search(r'data-id-product="(\d+)"', bloc)
                    # Un sujet au choix (olivier « Choix 14 ») est listé une fois par déclinaison : une seule fiche
                    cle = ident.group(1) if ident else (m.group(1) if m else "")
                    if not m or cle in produits:
                        continue
                    rubrique = re.search(r'product-category-name[^>]*>(.*?)</div>', bloc, re.S)
                    produits[cle] = {"url": m.group(1), "titre": _texte(m.group(2)),
                                            "rubrique": _texte(rubrique.group(1)) if rubrique else ""}
                    nouveaux += 1
                n = re.search(r"Affichage \d+-(\d+) de (\d+)", t)
                # Page suivante seulement si le site la propose pour cette catégorie (et si elle apporte du neuf)
                suivante = f'href="{cat}?page={page + 1}"' in t
                if not n or int(n.group(1)) >= int(n.group(2)) or not suivante or not nouveaux:
                    break
                page += 1
        produits = list(produits.values())
        f = os.path.join(config.DOSSIER_CACHE, f"index_pmf_{dt.date.today().isoformat()}.json")
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
            slug = re.sub(r"^\d+(-\d+)?-|\.html$", "", p["url"].rsplit("/", 1)[-1]).replace("-", " ")
            tn = M.norm(f"{p['titre']} {p['rubrique']} {slug}")
            if (any(M.contient(tn, n) for n in M.noms_latins(plante)) or M.contient(tn, plante.genre)
                    or any(M.contient(tn, n) for n in noms_communs)):
                res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def _page(self, url):
        if url not in self.pages:
            t = self.http.get(url).text
            h1 = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S)
            desc = re.search(r'id="product-description-short-\d+"[^>]*>(.*?)</div>', t, re.S)
            d = re.search(r'id="product-details"\s+data-product="([^"]*)"', t)
            d = json.loads(html.unescape(d.group(1))) if d else {}
            choix = re.findall(r'<option value="\d+" title="([^"]*)"', t)
            self.pages[url] = {"nom": _texte(h1.group(1)) if h1 else "", "desc": _texte(desc.group(1)) if desc else "",
                               "data": d, "choix": [_texte(c) for c in choix]}
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
        else:
            # Formats livrables du moins cher au plus cher, puis les lignes sans prix (magasin, rupture)
            offres = sorted((o for o in offres if o.prix), key=lambda o: o.tri) + [o for o in offres if not o.prix]
        return offres, anomalies

    def _variete(self, plante, titre):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        v = M.variete_fiches(plante, titre)
        if v:
            return v, True
        m = re.search(r"['‘’\"“”]([^'‘’\"“”]{2,40})['‘’\"“”]", titre)
        if m:
            return m.group(1).strip(), False
        return "", True

    def _analyser(self, plante, url, page, aujourdhui):
        titre, desc, d = page["nom"] or url, page["desc"], page["data"]
        notes = []
        textes = [titre, desc]
        if M.latin_sur_fiche(plante, textes):
            equiv = M.equivalence_utilisee(plante, textes)
            if equiv:
                notes.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, textes)
            if not approche:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {titre} » : aucun prix relevé")]
            notes.append(f"Nom latin écrit « {approche} » sur la fiche")
        if M.est_melange(titre):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        if not d:
            return [], [(url, f"Données produit absentes (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, titre)
        if variete and not connue:
            notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        var_col = variete or "Espèce type"

        format_brut = f"{titre} {desc}"
        cont = contenant(titre) or contenant(desc)
        m = re.search(r"\bC\s?(\d+(?:[.,]\d+)?)\s*L\b", titre)
        if not cont and m:
            cont = f"Pot {m.group(1).replace('.', ',')} L"
        h = hauteur(titre) or hauteur(re.sub(r"\bH\.\s*", "Hauteur ", titre)) or hauteur(desc) or ""
        detail = re.sub(r"^.*?(?=\b(?:Hauteur|H\.|Stipe|Diamètre|Circonférence|\d+\s*-\s*\d+\s*ans)\b)", "", titre)
        if detail and detail != titre:
            notes.append(f"Format : {detail}")
        age = re.search(r"\b(\d+\s*-\s*\d+)\s*ans\b", format_brut)
        if age and "ans" not in detail:
            notes.append(f"Âge : {age.group(1)} ans")

        if str(d.get("available_for_order")) != "1":
            # Pas de bouton panier : ni livraison ni prix relevé
            o = Offre(plante.id, plante.nom_commun, var_col, NOM, url, "", "EUR", cont, "Rupture", "", aujourdhui,
                      "", "À vérifier", " ; ".join(notes + [MAGASIN]), nom_latin, h)
            return [o], []
        prix = d.get("price_amount")
        dispo = d.get("availability") in ("available", "in_stock", "last_remaining_items") and prix
        if not dispo:
            o = Offre(plante.id, plante.nom_commun, var_col, NOM, url, "", "EUR", cont, "Rupture", "", aujourdhui,
                      "", "À vérifier", " ; ".join(notes + ["Rupture de stock"]), nom_latin, h)
            return [o], []
        barre = d.get("price_without_reduction")
        if d.get("has_discount") and barre and float(barre) > float(prix) + 0.005:
            notes.append(f"Promotion (prix barré {prix_fr(barre)} €)")
        if len(page["choix"]) > 1:
            notes.append(f"{len(page['choix'])} sujets au choix sur la fiche (prix du sujet affiché par défaut)")
        o = Offre(plante.id, plante.nom_commun, var_col, NOM, url, prix_fr(prix), "EUR", cont, "En stock", "",
                  aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, h)
        o.tri = float(prix)
        return [o], []
