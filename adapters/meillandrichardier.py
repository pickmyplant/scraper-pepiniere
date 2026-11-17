"""Adaptateur Meilland Richardier (https://www.meillandrichardier.com, Magento, rosiers, arbustes, fruitiers, vivaces).

Méthode : la recherche du site est interdite par robots.txt (/catalogsearch/) ; le catalogue vient du sitemap
public (/media/sitemap-mr/fr/sitemap.xml : adresse + titre et légende de l'image, ex. « Kerria japonica
'Pleniflora' ou Corête du Japon » / « Passiflore caerulea Purple Haze »). Une fiche est candidate si ce texte ou
son adresse contient un nom latin de la BDD (ou le genre et une variété de l'onglet Fiches).
Chaque fiche est ouverte une fois : JSON-LD schema.org ProductGroup, un Product par format (hasVariant), avec
prix, disponibilité (InStock / OutOfStock) et format (disambiguatingDescription, ex. « Pot de 2L/3L (Hauteur
livrée env. 20/30cm) », « Racines nues, Buisson, 40/60 »). Prix barré : priceSpecification StrikethroughPrice.
"""
import html
import json
import logging
import re

import config
from core import matching as M
from core.formatting import Offre, contenant, forme, hauteur, lot, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Meilland Richardier"
PREFIXE = "MRI"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.meillandrichardier.com"
SITEMAP = BASE + "/media/sitemap-mr/fr/sitemap.xml"


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _groupe(page):
    """ProductGroup (ou Product seul) du JSON-LD de la fiche, sinon None."""
    for b in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(b)
        except ValueError:
            continue
        noeuds = d.get("@graph", [d]) if isinstance(d, dict) else d
        for n in noeuds if isinstance(noeuds, list) else []:
            if isinstance(n, dict) and n.get("@type") in ("ProductGroup", "Product"):
                return n
    return None


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("meillandrichardier", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.catalogue = None
        self.fiches = {}

    # ---------- catalogue ----------
    def _catalogue(self):
        if self.catalogue is None:
            t = self.http.get(SITEMAP).text
            self.catalogue = []
            for bloc in re.findall(r"<url>(.*?)</url>", t, re.S):
                loc = re.search(r"<loc>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</loc>", bloc, re.S)
                if not loc or not re.search(r"^https://[^/]+/[^/]+\.html$", loc.group(1).strip()):
                    continue
                titres = []
                for balise in ("image:title", "image:caption"):
                    m = re.search(rf"<{balise}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{balise}>", bloc, re.S)
                    if m and _texte(m.group(1)) not in titres:
                        titres.append(_texte(m.group(1)))
                self.catalogue.append({"url": loc.group(1).strip(), "titre": " | ".join(titres),
                                       "legende": titres[-1] if titres else ""})
            log.info("Meilland Richardier : %d fiches dans le sitemap", len(self.catalogue))
        return self.catalogue

    def candidats(self, plante):
        res = [p for p in self._catalogue() if M.est_candidat(plante, p["titre"], p["url"])]
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for p in self.candidats(plante):
            url = p["url"]
            try:
                if url not in self.fiches:
                    self.fiches[url] = _groupe(self.http.get(url).text)
                g = self.fiches[url]
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                if getattr(getattr(e, "response", None), "status_code", None) == 404:
                    anomalies.append((url, "Fiche retirée du site (404, encore dans le sitemap)"))
                    continue
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            if not g:
                anomalies.append((url, "Pas de données produit (JSON-LD)"))
                continue
            o, a = self._analyser(plante, url, g, p["legende"], aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, textes):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        for t in textes:
            v = M.variete_fiches(plante, t)
            if v:
                return v, True
        for t in textes:
            t = re.sub(r"(?i)(^|\s)\(?[x×]\)?(?=\s)", " ", t)
            for n in M.noms_latins(plante):
                reste = M._apres(t, re.sub(r"(^|\s)[x×](?=\s)", " ", n))
                if reste is None:
                    continue
                q = re.match(r"\s*['‘’]([^'‘’]+)['‘’]", reste)
                if q:
                    reste = q.group(1)
                else:
                    reste = re.split(r"\s(?:ou|-|–|livré|en pot)\s|,", reste)[0]
                reste = re.sub(r"[®™]", "", reste).strip(" '\"‘’()")
                return reste, not reste
        return "", True

    def _analyser(self, plante, url, g, legende, aujourdhui):
        nom = _texte(g.get("name"))
        desc = _texte(g.get("description"))
        textes = [nom, legende]
        notes_page = []
        if M.latin_sur_fiche(plante, textes + [desc]):
            equiv = M.equivalence_utilisee(plante, textes + [desc])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, textes + [desc])
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(" ".join(textes)), plante.genre) and M.variete_fiches(plante, " ".join(textes)):
                notes_page.append(f"Espèce absente de la fiche (« {nom} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {nom} » différent de {plante.nom_latin}")]
        if M.est_melange(nom):
            return [], [(url, f"Collection ou mélange (« {nom} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, textes)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        variantes = g.get("hasVariant") or ([g] if g.get("offers") else [])
        lignes = []
        for v in variantes:
            ofs = v.get("offers") or []
            of = ofs[0] if isinstance(ofs, list) and ofs else ofs if isinstance(ofs, dict) else None
            if not of or of.get("price") in (None, ""):
                continue
            prix = float(of["price"])
            fmt = _texte(v.get("disambiguatingDescription")) or _texte(v.get("name")).split(" - ", 1)[-1]
            notes = list(notes_page)
            cont = contenant(fmt)
            nb = lot(fmt)
            if nb:
                notes.append(f"lot de {nb}")
            if not cont:
                notes.append(f"Format non reconnu : « {fmt} »" if fmt else "Format non indiqué")
            barre = [float(s.get("price") or 0) for s in of.get("priceSpecification") or []
                     if isinstance(s, dict) and str(s.get("priceType", "")).endswith("StrikethroughPrice")]
            if barre and max(barre) > prix + 0.005:
                notes.append(f"Promotion (prix barré {prix_fr(max(barre))} €)")
            h = hauteur(fmt)
            if not h:
                m = re.search(r"(?:^|,)\s*(\d+)\s*/\s*(\d+)\s*(cm)?\s*$", fmt)
                h = f"{m.group(1)}-{m.group(2)} cm" if m else None
            f = forme(fmt)
            if h and f:
                notes.append(f"Forme : {f}")
            dispo = "En stock" if str(of.get("availability", "")).endswith("InStock") else "Rupture"
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(prix),
                      "EUR", cont, dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin,
                      h or f or "")
            o.tri = taille_contenant(cont) * 1000 + prix
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
