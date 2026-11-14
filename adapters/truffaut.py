"""Adaptateur Truffaut (https://www.truffaut.com, Magento + marketplace Mirakl).

Méthode : la recherche du site est interdite par robots.txt. L'index des fiches vient du
sitemap public des produits (environ 330 000 URL, mis en cache 7 jours dans cache/) ;
le nom latin est présent dans l'adresse de la plupart des fiches plantes. Une fiche = un
format (pot, hauteur) ; le prix et la disponibilité sont lus dans le JSON-LD Product,
le prix barré (« au lieu de ») dans la page.
Sont relevées les fiches « Vendu et expédié par Truffaut » et celles du vendeur marketplace Arborix
(son propre site est fermé aux robots ; décision du 14/11/2026, note « Vendu par Arborix ») : les fiches
des autres vendeurs marketplace (bloc « Vendu par <vendeur> ») sont écartées, ces vendeurs étant
suivis sur leur propre site.
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
from core.formatting import Offre, contenant, forme, hauteur, prix_fr
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Truffaut"
PREFIXE = "TRU"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.truffaut.com"
SITEMAP_INDEX = BASE + "/v6/sitemapindex.xml"
DUREE_CACHE_JOURS = 7
# Adresses sans plante vivante (graines exceptées : gardées, le contenant le dira)
# Vendeurs marketplace relevés quand même (nom en minuscules contenu dans le nom du vendeur)
VENDEURS_GARDES = ("arborix",)
EXCLUS = re.compile(r"artificiel|housse|cache-pot|poster|tableau|coussin|bougie|peluche|sticker|decor", re.I)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("truffaut", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.urls = None

    # ---------- index des fiches ----------
    def _index(self):
        if self.urls is not None:
            return self.urls
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_tru_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.urls = json.load(fh)
                log.info("Index lu depuis le cache %s (%d fiches)", f, len(self.urls))
                return self.urls
        urls = []
        for sm in re.findall(r"<loc>([^<]*sitemap_produits_\d+\.xml)</loc>", self.http.get(SITEMAP_INDEX).text):
            urls += re.findall(r"<loc>([^<]+\.html)</loc>", self.http.get(sm).text)
        self.urls = sorted(set(urls))
        with open(os.path.join(config.DOSSIER_CACHE, f"index_tru_{dt.date.today():%Y-%m-%d}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(self.urls, f)
        log.info("Index : %d fiches", len(self.urls))
        return self.urls

    def candidats(self, plante):
        res = []
        # Pré-filtre rapide (330 000 adresses) : un des genres des noms latins dans l'adresse
        genres = {M.norm(n).split()[0] for n in M.noms_latins(plante) if M.norm(n)}
        for url in self._index():
            if not any(g in url for g in genres) or EXCLUS.search(url):
                continue
            mode = M.est_candidat(plante, "", url)
            if mode:
                res.append((url, mode))
        if not res:  # repli : nom commun dans l'adresse (le nom latin est vérifié sur la fiche)
            for url in self._index():
                slug = url.rsplit("/", 1)[-1][:-5].replace("-", " ")
                if not EXCLUS.search(url) and M.candidat_nom_commun(plante, slug):
                    res.append((url, "nom commun"))
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        cands = self.candidats(plante)
        log.info("%s %s : %d fiche(s) candidate(s)", plante.id, plante.nom_latin, len(cands))
        for url, _mode in cands:
            try:
                r = self.http.get(url)
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                code = getattr(getattr(e, "response", None), "status_code", None)
                anomalies.append((url, f"Fiche retirée (HTTP {code})" if code in (404, 410)
                                  else f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            o, a = self._analyser(plante, url, r.text, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

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

    def _analyser(self, plante, url, page, aujourdhui):
        vendeur = re.search(r'offer-seller-name">Vendu par <a[^>]*title="([^"]+)"', page)
        note_vendeur = ""
        if vendeur:
            nom_vendeur = html.unescape(vendeur.group(1))
            if not any(v in nom_vendeur.lower() for v in VENDEURS_GARDES):
                return [], [(url, f"Vendu par {nom_vendeur} (marketplace) : non relevé")]
            note_vendeur = f"Vendu par {nom_vendeur.split(',')[0]} sur Truffaut (marketplace)"
        p = self._produit(page)
        h1 = _texte(" ".join(re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S)[:1]))
        if not p:
            return [], [(url, f"Pas de données produit (JSON-LD) sur la fiche « {h1} »")]
        nom = _texte(p.get("name")) or h1
        desc = _texte(p.get("description"))
        textes = [nom, h1, desc]
        notes = [note_vendeur] if note_vendeur else []
        equiv = M.equivalence_utilisee(plante, textes)
        approche = None if M.latin_sur_fiche(plante, textes) else M.latin_approche(plante, textes)
        if equiv:
            notes.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if approche:
            notes.append(f"Nom latin écrit « {approche} » sur la fiche")
        elif not M.latin_sur_fiche(plante, textes):
            var = M.variete_fiches(plante, nom)
            if M.contient(M.norm(nom), plante.genre) and var:
                notes.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                             f"rapproché par la variété {var} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {nom} » : aucun prix relevé")]
        if M.est_melange(nom) or re.search(r"\b(?:lot|set) de \d+|\d+ (?:pieces|plantes)\b", M.norm(nom)):
            notes.append(f"Lot/mélange (« {nom} »)")

        # Variété : ce qui suit le nom latin dans le titre, jusqu'au format
        titre = re.split(r"\s[-–]\s|\s(?:pot|godet|h\d|ø|taille|en pot|hauteur|couleur|\d+\s*graines)\b", nom, flags=re.I)[0]
        variete, connue = M.variete_du_titre(plante, titre)
        variete = re.sub(r"[®™*]", "", variete)
        variete = re.sub(r"^(?:var\.?|cv\.?|ssp\.?|subsp\.?)\s+", "", variete.strip(" '\"‘’():"), flags=re.I)
        variete = re.sub(r"\s*\((?:de greffe|greff[ée]e?)\)", "", variete, flags=re.I).strip(" '\"‘’():")
        if re.search(r"\d|\bcm\b|plante", variete, re.I):
            variete, connue = "", True
        if variete and not connue:
            notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        var_col = variete or "Espèce type"

        offre = p.get("offers") or {}
        if isinstance(offre, list):
            offre = offre[0] if offre else {}
        prix = offre.get("price")
        en_stock = str(offre.get("availability", "")).endswith("InStock")
        ancien = re.search(r'data-price-amount="([\d.]+)" data-price-type="oldPrice"', page)
        if ancien and prix and float(ancien.group(1)) > float(prix):
            notes.append(f"Promotion (prix barré {prix_fr(ancien.group(1))} €)")
        if re.search(r"graines?", nom, re.I) and not re.search(r"\bpot\b", nom, re.I):
            notes.append("Graines")

        fmt = re.sub(r"ø\s*", "pot ", f"{nom} {desc}", flags=re.I)
        cont = contenant(fmt)
        h = hauteur(fmt) or (re.search(r"\b(\d+\s*[-/]\s*\d+\s*cm)\b", nom) or re.search(r"\bh\s?(\d+\s*cm)", nom, re.I))
        if h is not None and not isinstance(h, str):
            h = re.sub(r"\s*/\s*", "-", h.group(1))
        f = forme(nom)
        if h and f:
            notes.append(f"Forme : {f}")
        if not cont:
            notes.append(f"Format non reconnu : « {nom} »")
        if not prix:
            return [], [(url, "Pas de prix dans les données produit")]
        return [Offre(plante.id, plante.nom_commun, var_col, NOM, url, prix_fr(prix) if en_stock else "", "EUR",
                      cont, "En stock" if en_stock else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, h or f or "")], []
