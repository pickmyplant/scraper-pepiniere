"""Adaptateur Attitude Jardin (https://attitudejardin.fr, WooCommerce).

Méthode : l'API Store publique de WooCommerce (/wp-json/wc/store/v1/products, 100 produits par page,
autorisée par robots.txt) donne tout le catalogue en quelques requêtes ; index mis en cache 1 jour.
Titres « Nom commun (Nom latin) ↕ 80-90 cm » ou « Nom latin (Nom commun) ↕ … » : une fiche = un format
(hauteur dans le titre, diamètre du pot dans la description courte).
Variantes : « Cache-pots adaptés » (accessoire) -> seul le prix sans cache-pot est relevé, l'option est
notée ; autres options (ex. « Option fructification ») -> une ligne par variante, prix lu sur
/wp-json/wc/store/v1/products/<id de la variante>.
Produits à prix 0 et non achetables (« Lire la suite ») : prix non affiché, ligne « À vérifier ».
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
from core.formatting import Offre, contenant, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Attitude Jardin"
PREFIXE = "ATJ"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://attitudejardin.fr"
API = BASE + "/wp-json/wc/store/v1/products"
DUREE_CACHE_JOURS = 1
ATTR_CACHE_POT = "cache-pot"
# Catégories sans plante
CATEGORIES_EXCLUES = {"cache-pots", "accessoires", "pots", "cartes cadeaux", "terreaux"}


def _texte(x):
    t = html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))
    return re.sub(r"\s+", " ", t).replace("\xa0", " ").strip()


def _prix(p):
    """Montant de l'API (en centimes) -> texte « 64,95 »."""
    pr = p.get("prices") or {}
    try:
        v = int(pr.get("price") or 0) / 10 ** int(pr.get("currency_minor_unit", 2))
    except (TypeError, ValueError):
        return "", ""
    reg = pr.get("regular_price")
    try:
        r = int(reg) / 10 ** int(pr.get("currency_minor_unit", 2)) if reg else 0
    except (TypeError, ValueError):
        r = 0
    return (f"{v:.2f}" if v else ""), (f"{r:.2f}" if r and r > v else "")


def _hauteur(titre, court):
    m = re.search(r"↕\s*([\d.,]+)\s*-\s*([\d.,]+)\s*(cm|m)\b", titre)
    if m:
        return f"{m.group(1)}-{m.group(2)} {m.group(3)}"
    m = re.search(r"hauteur\s*:?\s*\+?-?\s*([\d.,]+)\s*(?:-\s*([\d.,]+)\s*)?(cm|m)\b", court, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)} {m.group(3)}" if m.group(2) else f"{m.group(1)} {m.group(3)}"
    return ""


def _contenant(court, titre):
    m = re.search(r"diam[eè]tre du pot\s*(?:\(\s*[øØ⌀]\s*\))?\s*:?\s*([\d.,]+)\s*cm", court, re.I) \
        or re.search(r"[øØ⌀]\s*([\d.,]+)\s*cm", f"{court} {titre}")
    if m:
        return f"Pot {m.group(1)} cm"
    return contenant(court)


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("attitudejardin", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None
        self.variantes = {}

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS - 1)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_atj_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits = []
        for n in range(1, 100):
            lot = self.http.get(f"{API}?per_page=100&page={n}").json()
            if not lot:
                break
            for p in lot:
                produits.append({
                    "id": p["id"], "type": p.get("type"), "titre": _texte(p.get("name")),
                    "url": p.get("permalink"), "court": _texte(p.get("short_description")),
                    "texte": M.norm(_texte(p.get("description"))[:4000]),
                    "categories": [_texte(c.get("name")).lower() for c in p.get("categories") or []],
                    "prix": _prix(p), "stock": bool(p.get("is_in_stock")),
                    "achetable": bool(p.get("is_purchasable")),
                    "attributs": [{"nom": a.get("name"), "taxo": a.get("taxonomy") or "",
                                   "termes": {t["slug"]: _texte(t["name"]) for t in a.get("terms") or []}}
                                  for a in p.get("attributes") or [] if a.get("has_variations")],
                    "variantes": [{"id": v["id"], "attrs": {a["name"]: a["value"] for a in v.get("attributes") or []}}
                                  for v in p.get("variations") or []],
                })
            if len(lot) < 100:
                break
        f = os.path.join(config.DOSSIER_CACHE, f"index_atj_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        noms_communs = [plante.nom_commun] + plante.autres_noms
        res = []
        for p in self._index():
            if set(p["categories"]) & CATEGORIES_EXCLUES:
                continue
            tn = M.norm(f"{p['titre']} {p['url'].rstrip('/').rsplit('/', 1)[-1].replace('-', ' ')}")
            if (M.contient(tn, plante.genre) or any(M.contient(tn, n) for n in noms_communs)
                    or any(M.contient(p["texte"], n) for n in M.noms_latins(plante))):
                res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    def _variante(self, vid):
        if vid not in self.variantes:
            p = self.http.get(f"{API}/{vid}").json()
            self.variantes[vid] = (_prix(p), bool(p.get("is_in_stock")))
        return self.variantes[vid]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for p in self.candidats(plante):
            try:
                o, a = self._analyser(plante, p, aujourdhui)
            except InterditParRobots:
                o, a = [], [(p["url"], "Interdit par robots.txt, non lu")]
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                o, a = [], [(p["url"], f"Erreur de lecture : {e.__class__.__name__}")]
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, titre):
        """Cultivar d'après le titre sans la hauteur ni les parenthèses."""
        t = re.sub(r"↕.*$", "", titre).strip()
        v = M.variete_fiches(plante, t)
        if v:
            return v, True
        latin_paren = [x for x in re.findall(r"\(([^()]*)\)", t)]
        hors = re.sub(r"\([^()]*\)", " ", t)
        for seg in [hors] + latin_paren:
            for n in sorted(M.noms_latins(plante), key=len, reverse=True):
                reste = M._apres(seg, n)
                if reste is not None:
                    reste = re.split(r"\s[-–]\s|[-–]\s|\s[-–]", reste)[0].strip(" '\"‘’()®-–")
                    if len(reste.split()) > 3:
                        return "", True
                    return (reste[:1].upper() + reste[1:], False) if reste else ("", True)
        return "", True

    def _analyser(self, plante, p, aujourdhui):
        url, titre = p["url"], p["titre"]
        textes = [titre, p["court"], p["texte"]]
        notes_page = []
        if M.latin_sur_fiche(plante, textes):
            equiv = M.equivalence_utilisee(plante, textes)
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, textes)
            tn = M.norm(titre)
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(tn, plante.genre) and M.variete_fiches(plante, titre):
                notes_page.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                                  "rapproché par la variété de l'onglet Fiches")
            elif M.contient(tn, plante.genre) and M.candidat_nom_commun(plante, titre) \
                    and not re.search(rf"\b{re.escape(plante.genre)}\s+[a-z]{{4,}}", titre, re.I):
                notes_page.append(f"Espèce non précisée sur la fiche (genre {plante.genre} seul, "
                                  f"titre « {titre} ») : à confirmer")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {titre} » : aucun prix relevé")]
        if M.est_melange(titre):
            notes_page.append(f"Lot/mélange (« {titre} »)")
        variete, connue = self._variete(plante, titre)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        variete_col = variete or "Espèce type"
        haut = _hauteur(titre, p["court"])
        cont = _contenant(p["court"], titre)

        formats = []  # (notes, (prix, barré), en_stock)
        attrs_options = [a for a in p["attributs"] if ATTR_CACHE_POT not in a["taxo"]]
        cache_pot = next((a for a in p["attributs"] if ATTR_CACHE_POT in a["taxo"]), None)
        if p["type"] == "variable" and p["variantes"]:
            for v in p["variantes"]:
                notes = []
                if cache_pot:
                    val = v["attrs"].get(cache_pot["nom"], "")
                    if val and not val.startswith("sans"):
                        continue  # même plante avec un cache-pot (accessoire)
                for a in attrs_options:
                    val = v["attrs"].get(a["nom"], "")
                    notes.append(f"{a['nom']} : {a['termes'].get(val, val) or 'toutes'}")
                prix, stock = self._variante(v["id"])
                formats.append((notes, prix, stock))
            if cache_pot and len(cache_pot["termes"]) > 1:
                notes_page.append("cache-pot en option (prix sans cache-pot)")
        else:
            formats.append(([], p["prix"], p["stock"]))
        if not formats:
            return [], [(url, f"Aucun format lisible sur « {titre} »")]

        lignes = []
        for notes_f, (prix, barre), stock in formats:
            notes = notes_page + notes_f
            if not prix:
                notes.append("Prix non affiché (produit non achetable en ligne)")
            if barre:
                notes.append(f"Promotion (prix barré {prix_fr(barre)} €)")
            if not cont:
                notes.append("Contenant non précisé")
            o = Offre(plante.id, plante.nom_commun, variete_col, NOM, url, prix_fr(prix) if prix else "", "EUR",
                      cont, "En stock" if stock and prix else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, haut)
            o.tri = taille_contenant(cont) * 1000 + float(prix or 0)
            lignes.append(o)
        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            n = " ; ".join(notes_page + ["Aucun format en stock" if any(f[1][0] for f in formats)
                                         else "Prix non affiché (produit non achetable en ligne)"])
            return [Offre(plante.id, plante.nom_commun, variete_col, NOM, url, "", "EUR", cont, "Rupture", "",
                          aujourdhui, "", "À vérifier", n, nom_latin, haut)], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
