"""Adaptateur Les Senteurs du Quercy (https://www.senteursduquercy.com, PrestaShop, pépinière de plantes
de terrain sec : lavandes, iris, sauges, thyms, graminées, cactées rustiques…).

Méthode : la recherche (controller=search, /recherche) est interdite par robots.txt et il n'y a pas de
sitemap public. L'index vient de la catégorie « Catalogue » (/143-catalogue, toutes les plantes, 50 par page,
pages ?page=N autorisées), mis en cache 7 jours dans cache/. La liste donne le titre (nom latin + nom commun)
et le conditionnement (« Pot 1,4L anti-chignon »).
Chaque fiche candidate est ouverte : Genre / Espèce / Cultivar du tableau des caractéristiques et le titre
<h1> valident le rapprochement ; prix et disponibilité viennent des données produit (data-product).
Une fiche = un format. « En culture » = pas encore vendable (Rupture).
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

NOM = "Les Senteurs du Quercy"
PREFIXE = "SDQ"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.senteursduquercy.com"
CATALOGUE = BASE + "/143-catalogue"
DUREE_CACHE_JOURS = 7
# Fiches sans plante (outils, cartes cadeaux, livres…), reconnues à leur titre
EXCLUS = re.compile(r"\b(carte cadeau|outil|s[ée]cateur|livre|terreau|engrais|paillage|coffret)\b", re.I)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("senteursduquercy", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None
        self.pages = {}

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_sdq_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits, page, total = {}, 1, None
        while page <= 80:
            t = self.http.get(CATALOGUE + (f"?page={page}" if page > 1 else "")).text
            nouveaux = 0
            for bloc in re.split(r'js-product-miniature', t)[1:]:
                ident = re.search(r'data-id-product="(\d+)"', bloc)
                m = re.search(r'product-title"[^>]*>\s*<a[^>]*href="([^"#]+)[^"]*"[^>]*>(.*?)</a>', bloc, re.S)
                if not m or not ident or ident.group(1) in produits:
                    continue
                cond = re.search(r'type_conditionnement"><a[^>]*>(.*?)</a>', bloc, re.S)
                produits[ident.group(1)] = {"url": m.group(1), "titre": _texte(m.group(2)),
                                            "conditionnement": _texte(cond.group(1)) if cond else ""}
                nouveaux += 1
            n = re.search(r"Affichage \d+-(\d+) de (\d+)", t)
            total = int(n.group(2)) if n else total
            if not n or int(n.group(1)) >= int(n.group(2)) or not nouveaux:
                break
            page += 1
        produits = list(produits.values())
        if total and len(produits) < total * 0.95:
            log.warning("Index incomplet : %d produits sur %d annoncés", len(produits), total)
        f = os.path.join(config.DOSSIER_CACHE, f"index_sdq_{dt.date.today().isoformat()}.json")
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
            slug = re.sub(r"^\d+-|\.html$", "", p["url"].rsplit("/", 1)[-1]).replace("-", " ")
            tn = M.norm(f"{p['titre']} {slug}")
            # Les titres portent le nom latin complet : le genre seul ne suffit que si l'espèce est inconnue
            if (any(M.contient(tn, n) for n in M.noms_latins(plante))
                    or (not plante.espece and M.contient(tn, plante.genre))
                    or any(M.contient(tn, n) for n in noms_communs)):
                res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def _page(self, url):
        if url not in self.pages:
            t = self.http.get(url).text
            h1 = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S)
            d = re.search(r'id="product-details"[^>]*data-product="([^"]*)"', t) or \
                re.search(r'data-product="([^"]*)"', t)
            d = json.loads(html.unescape(d.group(1))) if d else {}
            carac = {}
            fiche = re.search(r'<dl class="data-sheet">(.*?)</dl>', t, re.S)
            if fiche:
                for k, v in re.findall(r'<dt class="name">(.*?)</dt>\s*<dd class="value">(.*?)</dd>', fiche.group(1), re.S):
                    carac[_texte(k).rstrip(" :")] = _texte(v)
            self.pages[url] = {"nom": _texte(h1.group(1)) if h1 else "", "data": d, "carac": carac}
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
            o, a = self._analyser(plante, p, page, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        else:
            offres = sorted((o for o in offres if o.prix), key=lambda o: o.tri) + [o for o in offres if not o.prix]
        return offres, anomalies

    def _variete(self, plante, titre, cultivar):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        v = M.variete_fiches(plante, f"{titre} '{cultivar}'" if cultivar else titre)
        if v:
            return v, True
        if cultivar:
            return cultivar, False
        m = M._CITE.search(titre)   # apostrophe intérieure gardée (19/11/2026)
        if m:
            return m.group(1).strip(), False
        return "", True

    def _analyser(self, plante, p, page, aujourdhui):
        url = p["url"]
        titre, d, carac = page["nom"] or p["titre"] or url, page["data"], page["carac"]
        notes = []
        botanique = " ".join(x for x in (carac.get("Genre", ""), carac.get("Espèce", "")) if x)
        textes = [titre, botanique]
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
        variete, connue = self._variete(plante, titre, carac.get("Cultivar", ""))
        if variete and not connue:
            notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        var_col = variete or "Espèce type"

        cond = p.get("conditionnement", "")
        cont = contenant(cond) or contenant(titre)
        if cond and not cont:
            notes.append(f"Conditionnement : {cond}")
        h = hauteur(titre) or ""
        if d.get("attributes"):
            notes.append("Plusieurs déclinaisons sur la fiche (prix de la déclinaison affichée par défaut)")

        prix = d.get("price_amount")
        dispo = d.get("availability") in ("available", "in_stock", "last_remaining_items") and prix \
            and str(d.get("available_for_order", "1")) == "1"
        if not dispo:
            msg = d.get("availability_message") or "Rupture de stock"
            o = Offre(plante.id, plante.nom_commun, var_col, NOM, url, "", "EUR", cont, "Rupture", "", aujourdhui,
                      "", "À vérifier", " ; ".join(notes + [f"Non disponible à la vente (« {msg} »)"]), nom_latin, h)
            return [o], []
        if d.get("availability") == "last_remaining_items":
            notes.append("Dernières plantes en stock")
        barre = d.get("price_without_reduction")
        if d.get("has_discount") and barre and float(barre) > float(prix) + 0.005:
            notes.append(f"Promotion (prix barré {prix_fr(barre)} €)")
        o = Offre(plante.id, plante.nom_commun, var_col, NOM, url, prix_fr(prix), "EUR", cont, "En stock", "",
                  aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, h)
        o.tri = float(prix)
        return [o], []
