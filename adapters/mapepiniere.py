"""Adaptateur Ma Pépinière (https://ma-pepiniere.fr), boutique Sylius.

Méthode : recherche du site en GET (/fr/search, autorisée par robots.txt) par nom latin puis
par nom commun ; seules les fiches dont l'adresse cite le genre ou un nom commun sont lues.
Sur chaque fiche : nom latin dans le <small> du titre (h1), offres dans le JSON-LD Product
(Offer seule ou AggregateOffer), une offre par format.
"""
import html
import json
import logging
import re
from urllib.parse import quote_plus, urljoin

import config
from core import matching as M
from core.formatting import Offre, contenant, forme, hauteur, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Ma Pépinière"
PREFIXE = "MPE"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://ma-pepiniere.fr"
RECHERCHE = BASE + "/fr/search?bitbag_elasticsearch_search%5Bbox%5D%5Bquery%5D={q}"
MAX_FICHES = 25


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("mapepiniere", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.pages = {}
        self.recherches = {}

    # ---------- recherche ----------
    def _chercher(self, terme):
        if terme in self.recherches:
            return self.recherches[terme]
        r = self.http.get(RECHERCHE.format(q=quote_plus(terme)))
        res = []
        for l in re.findall(r'href="(/fr/produit/[^"?#]+)', r.text):
            u = urljoin(BASE, l)
            if u not in res:
                res.append(u)
        log.info("Recherche « %s » : %d fiche(s)", terme, len(res))
        self.recherches[terme] = res
        return res

    def candidats(self, plante):
        espece = re.sub(r"[×x]\s+", "", plante.espece).strip()
        noms = [plante.nom_commun] + plante.autres_noms[:3]
        trouves = []
        for terme in [f"{plante.genre} {espece}"] + plante.synonymes_latins[:1] + [plante.nom_commun]:
            for u in self._chercher(re.sub(r"[×x]\s+", "", terme).strip()):
                if u not in trouves:
                    trouves.append(u)
        # Pré-filtre sur l'adresse : genre ou nom commun (la recherche renvoie beaucoup de bruit)
        garde = []
        for u in trouves:
            slug = M.norm(u.rsplit("/", 1)[-1].replace("-", " "))
            if M.contient(slug, plante.genre) or any(M.contient(slug, n) for n in noms) \
                    or any(M.contient(slug, s.split()[0]) for s in plante.synonymes_latins if s.split()):
                garde.append(u)
        return garde[:MAX_FICHES]

    # ---------- fiche ----------
    def _page(self, url):
        if url not in self.pages:
            self.pages[url] = self.http.get(url).text
        return self.pages[url]

    @staticmethod
    def _produit(page):
        for b in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                d = json.loads(b)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("@type") == "Product":
                return d
        return None

    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for url in self.candidats(plante):
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

    def _variete(self, plante, textes):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        for texte in textes:
            v = M.variete_fiches(plante, texte)
            if v:
                return v, True
        latin = textes[0] if textes else ""
        for n in M.noms_latins(plante):
            reste = M._apres(latin, n)
            if reste is not None:
                reste = reste.strip(" '\"‘’()")
                return (reste, False) if reste else ("", True)
        return "", True

    def _analyser(self, plante, url, page, aujourdhui):
        m = re.search(r'<h1 id="sylius-product-name".*?</h1>', page, re.S)
        h1 = m.group(0) if m else ""
        sm = re.search(r"<small[^>]*>(.*?)</small>", h1, re.S)
        latin = _texte(sm.group(1)) if sm else ""
        titre = _texte(re.sub(r"<small.*?</small>", "", h1, flags=re.S))
        p = self._produit(page)
        desc = _texte(p.get("description")) if p else ""
        textes = [latin, titre]
        notes_page = []
        if M.latin_sur_fiche(plante, textes):
            equiv = M.equivalence_utilisee(plante, textes)
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, textes)
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.latin_sur_fiche(plante, [desc]) and not latin:
                notes_page.append("Nom latin lu dans la description seulement")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {titre} / {latin} » : aucun prix relevé")]
        if not p:
            return [], [(url, "Pas de données produit (JSON-LD)")]
        if M.est_melange(titre):
            notes_page.append(f"Lot/mélange de la même espèce (« {titre} »)")

        variete, connue = self._variete(plante, [latin, titre])
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        of = p.get("offers") or {}
        liste = of.get("offers") if isinstance(of, dict) and "offers" in of else ([of] if isinstance(of, dict) else of)
        lignes = []
        for o in liste or []:
            fmt = _texte(o.get("name"))
            notes = list(notes_page)
            fn = re.sub(r"\bPots\b", "Pot", fmt, flags=re.I)
            cont = contenant(fn)
            h, f = hauteur(fn), forme(fn)
            if not h and cont.endswith(" L"):  # « Pot 20 Litres 175/200 cm » : hauteur sans le mot
                mh = re.search(r"l(?:itres?)?\s+(\d+)\s*/\s*(\d+)\s*(cm|m)\b", fn, re.I)
                if mh:
                    h = f"{mh.group(1)}-{mh.group(2)} {mh.group(3)}"
            if h and f:
                notes.append(f"Forme : {f}")
            if not cont:
                notes.append(f"Format non reconnu : « {fmt} »")
            dispo = "En stock" if str(o.get("availability", "")).endswith("InStock") else "Rupture"
            lien = o.get("url") or url
            ligne = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, lien, prix_fr(o.get("price")),
                          o.get("priceCurrency") or "EUR", cont, dispo, "", aujourdhui, "", "À vérifier",
                          " ; ".join(notes), nom_latin, h or f or "")
            ligne.tri = taille_contenant(cont) * 1000 + float(o.get("price") or 0)
            lignes.append(ligne)

        if not lignes:
            return [], [(url, "Aucune offre dans les données produit")]
        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            return [Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, "", "EUR", "", "Rupture",
                          "", aujourdhui, "", "À vérifier", " ; ".join(notes_page + ["Aucun format en stock"]),
                          nom_latin, "")], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
