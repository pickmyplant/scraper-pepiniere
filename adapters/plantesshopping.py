"""Adaptateur Plantes Shopping (https://www.plantes-shopping.fr, site sur mesure, prix en EUR).

Méthode : l'index vient du sitemap (/sitemap.xml, autorisé par robots.txt), mis en cache 7 jours dans cache/.
Les fiches produit sont « /article/<n>-<genre>-<espece>-<cultivar> » : la pré-sélection se fait sur ce slug.
Fiche : JSON-LD Product (nom « Genre espèce Cultivar - nom commun, accroche », prix, disponibilité)
et liste « Taille disponible » (« 60/80 cm en pot de 2 litres »). Presque toujours une seule taille ;
s'il y en a plusieurs, seul le prix de la taille affichée est lu (les autres viennent d'un appel AJAX),
avec une note.
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
from core.formatting import Offre, contenant, prix_fr, taille_contenant
from core.http import ClientPoli

log = logging.getLogger("scraper")

NOM = "Plantes Shopping"
PREFIXE = "PSH"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.plantes-shopping.fr"
SITEMAP = BASE + "/sitemap.xml"
DUREE_CACHE_JOURS = 7
CULTIVAR = re.compile(r"['‘’]([^'‘’]{2,40})['‘’]")
HAUTEUR = re.compile(r"(\d+)\s*(?:/|à|-)\s*(\d+)\s*(cm|m)\b", re.I)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _latin(titre):
    """Partie latine du titre : avant le premier « - »."""
    return re.split(r"\s+[-–]\s+", titre, 1)[0].strip()


def _prix(txt):
    m = re.search(r"(\d[\d\s]*(?:[.,]\d+)?)", txt or "")
    return float(m.group(1).replace(" ", "").replace(",", ".")) if m else None


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("plantesshopping", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.urls = None

    # ---------- index (sitemap) ----------
    def _index(self):
        if self.urls is not None:
            return self.urls
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_psh_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.urls = json.load(fh)
                log.info("Index lu depuis le cache %s (%d fiches)", f, len(self.urls))
                return self.urls
        texte = self.http.get(SITEMAP).text
        urls = sorted(set(re.findall(r"<loc>\s*(https?://[^<]+/article/[^<\s]+)\s*</loc>", texte)))
        f = os.path.join(config.DOSSIER_CACHE, f"index_psh_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(urls, fh, ensure_ascii=False)
        log.info("Index du sitemap : %d fiches (%s)", len(urls), f)
        self.urls = urls
        return urls

    def candidats(self, plante):
        res = []
        for u in self._index():
            slug = re.sub(r"^\d+-", "", u.rsplit("/", 1)[-1])
            if M.est_candidat(plante, slug.replace("-", " "), u):
                res.append(u)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for url in self.candidats(plante):
            o, a = self._analyser(plante, url, self.http.get(url).text, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, latin):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        v = M.variete_fiches(plante, latin)
        if v:
            return v, True
        m = CULTIVAR.search(latin)
        if m:
            return m.group(1).strip(), False
        for n in M.noms_latins(plante):
            r = M._apres(latin, n)
            if r is not None:
                r = re.sub(r"[®™]", "", r).strip(" '\"‘’()")
                return r, not r
        return "", True

    @staticmethod
    def _produit(page):
        for bloc in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
            try:
                d = json.loads(bloc, strict=False)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("@type") == "Product":
                return d
        return None

    def _analyser(self, plante, url, page, aujourdhui):
        d = self._produit(page)
        if not d:
            return [], [(url, "Pas de données produit (JSON-LD) sur la fiche")]
        titre = _texte(d.get("name"))
        latin = _latin(titre)
        notes = []
        if M.latin_sur_fiche(plante, [latin]):
            equiv = M.equivalence_utilisee(plante, [latin])
            if equiv:
                notes.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, [latin])
            if approche:
                notes.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(latin), plante.genre) and M.variete_fiches(plante, latin):
                notes.append(f"Espèce absente de la fiche (« {latin} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {latin} » différent de {plante.nom_latin}")]
        if M.est_melange(titre):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, latin)
        if variete and not connue:
            notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        off = d.get("offers") or {}
        if isinstance(off, list):
            off = off[0] if off else {}
        m = re.search(r'id="prix_article"[^>]*>([^<]*)', page)
        prix = _prix(m.group(1)) if m else None
        if prix is None:
            prix = _prix(str(off.get("price") or ""))
        if not prix:
            return [], [(url, "Prix absent de la fiche")]
        dispo = "En stock" if "InStock" in (off.get("availability") or "") else "Rupture"
        m = re.search(r'id="cadre_prix_barre"\s+class="([^"]*)"', page)
        if m and "hidden" not in m.group(1).split():
            b = re.search(r'id="prix_barre_article"[^>]*>([^<]*)', page)
            barre = _prix(b.group(1)) if b else None
            if barre and barre > prix + 0.005:
                notes.append(f"Promotion (prix barré {prix_fr(barre)} €)")

        options = re.findall(r'<option value="\d+"\s*(selected)?[^>]*>([^<]*)</option>',
                             page.split('id="select-gammes"', 1)[-1].split("</select>", 1)[0])
        tailles = [_texte(t) for _, t in options]
        choisie = next((_texte(t) for s, t in options if s), tailles[0] if tailles else "")
        if len(tailles) > 1:
            autres = [t for t in tailles if t != choisie]
            notes.append("autres tailles proposées (prix non relevé) : " + ", ".join(autres))
        cont = contenant(choisie)
        if not cont:
            notes.append(f"Format non reconnu : « {choisie} »" if choisie else "Format non indiqué")
        mh = HAUTEUR.search(choisie.split(" en ")[0]) if choisie else None
        h = f"{mh.group(1)}-{mh.group(2)} {mh.group(3)}" if mh else ""
        if dispo == "Rupture":
            notes.append("En cours de réapprovisionnement")
        o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(prix), "EUR", cont,
                  dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, h)
        o.tri = taille_contenant(cont) * 1000 + prix
        return [o], []
