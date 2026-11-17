"""Adaptateur Roses André Ève (https://www.roses-andre-eve.com, PrestaShop : rosiers, vivaces, iris, arbustes, petits fruits).

Méthode : la recherche du site est interdite par robots.txt (/recherche) ; le catalogue vient du sitemap public
(/1_fr_0_sitemap.xml : adresse + titre et légende de l'image, ex. « Lavandula Grosso » / « Lavandula Grosso, aussi
appelée lavandin… »). Les fiches de rosiers ne citent pas « Rosa » : le mot est ajouté pour la présélection.
Une fiche est candidate si ce texte ou son adresse contient un nom latin de la BDD (ou le genre et une variété de Fiches).
Chaque fiche est ouverte une fois : un bloc HTML par format (div.item-combination : « Racines nues », « Conteneur 2 L »),
avec prix TTC et disponibilité (« En rupture de stock », « Pré-commander » + date de livraison, ou « Ajouter au panier »).
La pré-commande est relevée « En stock » (commande possible), la date de disponibilité est mise dans Notes.
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

NOM = "Roses André Ève"
PREFIXE = "RAE"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.roses-andre-eve.com"
SITEMAP = BASE + "/1_fr_0_sitemap.xml"
FICHE = re.compile(r"^https://www\.roses-andre-eve\.com/([a-z0-9-]+)/\d+-[^/]+\.html$")
HORS_PLANTES = {"outillage", "deco-art-de-la-table", "soins-et-entretien", "livres", "cosmetiques", "alimentaire", "featured"}


def _texte(x):
    x = html.unescape(html.unescape(x or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).replace("\xa0", " ").strip()


def _rosa(t):
    """« R. banksiae Lutea » -> « Rosa banksiae Lutea » ; titre sans genre -> « Rosa <titre> »."""
    t = re.sub(r"(^|[\s(])R\.\s*(?=[a-z×x])", r"\1Rosa ", t)
    return t if re.match(r"(?i)rosa\b", t) else "Rosa " + t


def _produit(page):
    """Product du JSON-LD de la fiche, sinon {}."""
    for b in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(b)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            return d
    return {}


def _formats(page):
    """[(format, prix, disponibilité, note)] lus dans les blocs div.item-combination."""
    i = page.find('id="product_combinations"')
    if i < 0:
        return []
    zone = page[i:]
    fin = zone.find("product-additional-info")
    zone = zone[: fin if fin > 0 else len(zone)]
    res = []
    for b in re.split(r'<div class="item-combination d-flex', zone)[1:]:
        nom = re.search(r'<span class="name-attr">(.*?)</span>', b, re.S)
        prix = re.search(r'<span class="price">\s*([\d\s.,]+)\s*€', b)
        if not nom or not prix:
            continue
        dispo = re.search(r'product-combination-availability([^"]*)"[^>]*>(.*?)</span>', re.sub(r"<svg.*?</svg>", "", b, flags=re.S), re.S)
        classe, texte = (dispo.group(1), _texte(dispo.group(2))) if dispo else ("", "")
        note = ""
        if "out-of-stock" in classe or "rupture" in texte.lower():
            etat = "Rupture"
        elif "preorder" in classe or "Pré-commander" in b:
            etat = "En stock"
            note = f"Pré-commande ({texte[0].lower() + texte[1:]})" if texte else "Pré-commande"
        elif re.search(r"add_to_cart_\d+", b) or "Ajouter au panier" in b:
            etat = "En stock"
        else:
            etat = "Rupture"
        res.append((_texte(nom.group(1)), float(re.sub(r"\s", "", prix.group(1)).replace(",", ".")), etat, note))
    return res


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("rosesandreeve", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.catalogue = None
        self.fiches = {}

    # ---------- catalogue ----------
    def _catalogue(self):
        if self.catalogue is None:
            t = self.http.get(SITEMAP).text
            self.catalogue, vus = [], set()
            for bloc in re.findall(r"<url>(.*?)</url>", t, re.S):
                loc = re.search(r"<loc>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</loc>", bloc, re.S)
                url = loc.group(1).strip() if loc else ""
                m = FICHE.match(url)
                if not m or m.group(1) in HORS_PLANTES or url in vus:
                    continue
                vus.add(url)
                titres = []
                for balise in ("image:title", "image:caption"):
                    t2 = re.search(rf"<{balise}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{balise}>", bloc, re.S)
                    if t2 and _texte(t2.group(1)) not in titres:
                        titres.append(_texte(t2.group(1)))
                rosier = m.group(1).startswith("rosiers")
                titre = titres[0] if titres else ""
                self.catalogue.append({"url": url, "rosier": rosier, "nom": titre,
                                       "titre": " | ".join(_rosa(x) if rosier else x for x in titres)})
            log.info("Roses André Ève : %d fiches de plantes dans le sitemap", len(self.catalogue))
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
                    page = self.http.get(url).text
                    d = _produit(page)
                    desc = re.search(r'<meta name="description" content="([^"]*)"', page)
                    self.fiches[url] = (d, _texte(desc.group(1)) if desc else "", _formats(page))
                d, desc, formats = self.fiches[url]
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
            o, a = self._analyser(plante, url, p, d, desc, formats, aujourdhui)
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
                reste = q.group(1) if q else re.split(r"\s(?:ou|-|–)\s|,|\(", reste)[0]
                reste = re.sub(r"[®™]", "", reste).strip(" '\"‘’()")
                return reste, not reste
        return "", True

    def _analyser(self, plante, url, p, d, desc, formats, aujourdhui):
        nom = _texte(d.get("name")) or p["nom"]
        textes = [_rosa(nom), _rosa(p["nom"])] if p["rosier"] else [nom, p["nom"]]
        tout = textes + [desc, _texte(d.get("description"))]
        notes_page = []
        if M.latin_sur_fiche(plante, tout):
            equiv = M.equivalence_utilisee(plante, tout)
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, tout)
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

        if not formats:
            return [], [(url, "Aucun format sur la fiche")]
        lignes = []
        for fmt, prix, dispo, note in formats:
            fmt_pot = re.sub(r"(?i)\bconteneur\b", "Pot", fmt)
            cont = contenant(fmt_pot) or contenant(fmt)
            notes = list(notes_page) + ([note] if note else [])
            nb = lot(fmt)
            if nb:
                notes.append(f"lot de {nb}")
            if not cont:
                notes.append(f"Format non reconnu : « {fmt} »" if fmt else "Format non indiqué")
            h = hauteur(fmt)
            f = forme(fmt)
            if h and f:
                notes.append(f"Forme : {f}")
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(prix),
                      "EUR", cont, dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin,
                      h or f or "")
            o.tri = taille_contenant(cont) * 1000 + prix
            lignes.append(o)
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
