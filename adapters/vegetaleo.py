"""Adaptateur Vegetaleo (https://vegetaleo.com, Shopify, prix en EUR).

Méthode : l'index du catalogue vient de /products.json (pages de 250 produits, autorisé par robots.txt),
mis en cache 7 jours dans cache/. Il donne titre, formats (variantes), prix, prix barré et disponibilité ;
aucune page produit n'est ouverte.
Titre : « Nom commun Cultivar (Genre espèce 'Cultivar') », parfois sans parenthèses (« Hosta 'Guacamole' »,
« Crocosmia Lucifer ») : le nom latin est lu dans la parenthèse, sinon dans le titre entier.
Variante unique « format livré » : « pot de 3 litres », « godet de 8/9 cm », « godet forestier »,
« scion en racines nues », avec parfois « - hauteur entre 30 et 40 cm ».
Exclus : la carte cadeau et les collections ou mélanges.
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
from core.http import ClientPoli

log = logging.getLogger("scraper")

NOM = "Vegetaleo"
PREFIXE = "VEG"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://vegetaleo.com"
CATALOGUE = BASE + "/products.json?limit=250&page={n}"
DUREE_CACHE_JOURS = 7
TYPES_EXCLUS = {"carte cadeau"}
CULTIVAR = M._CITE  # apostrophe intérieure gardée (« Rambour d'Hiver ») : core/matching.py, 19/11/2026
# Port à domicile selon le seul poids du panier (poids des variantes de products.json), jamais offert, même prix en
# Corse ; seule option proposée (transporteur non nommé). Confirmé au panier et au paiement le 23/11/2026,
# y compris pour les poids aberrants du site (pot 3 L à 0 g → 7,90 ; godet à 1,5 kg → 10,90).
TRANCHES_PORT = [(500, 7.90), (750, 8.90), (1000, 9.90), (2000, 10.90), (3000, 11.90)]


def _port(grammes):
    """Port d'un panier ne contenant que ce format (poids en grammes)."""
    if grammes is None:
        return ""
    for plafond, port in TRANCHES_PORT:
        if grammes <= plafond:
            return prix_fr(port)
    kg = -(-int(grammes) // 1000)  # kg entamés
    if kg <= 20:
        return prix_fr(kg + 8.90)  # 4 kg -> 12,90 ... 20 kg -> 28,90
    if kg <= 28:
        return prix_fr(kg + 7.90)  # 20-21 kg -> 28,90 aussi ; 22 kg -> 29,90 ... 28 kg -> 35,90
    return prix_fr(36.90)


HAUTEUR = re.compile(r"hauteur(?: livrée)?\s+(?:entre\s+)?(\d+)\s*(?:et|à|-)\s*(\d+)\s*cm", re.I)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _latin(titre):
    """Partie latine du titre : la dernière parenthèse, sinon le titre entier."""
    m = re.findall(r"\(([^()]+)\)", titre)
    return m[-1].strip() if m else titre


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("vegetaleo", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_veg_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    produits = json.load(fh)
                if any("grammes" not in v for p in produits[:5] for v in p["variantes"]):
                    continue  # ancien cache sans poids
                self.produits = produits
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits = []
        for n in range(1, 100):
            lot = self.http.get(CATALOGUE.format(n=n)).json().get("products") or []
            if not lot:
                break
            for p in lot:
                if (p.get("product_type") or "").strip().lower() in TYPES_EXCLUS:
                    continue
                produits.append({
                    "handle": p["handle"], "titre": _texte(p.get("title")), "type": p.get("product_type") or "",
                    "variantes": [{"titre": v.get("title") or "", "prix": v.get("price"),
                                   "barre": v.get("compare_at_price"), "dispo": bool(v.get("available")),
                                   "grammes": v.get("grams")}
                                  for v in p.get("variants") or []],
                })
        f = os.path.join(config.DOSSIER_CACHE, f"index_veg_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        res = [p for p in self._index()
               if M.est_candidat(plante, p["titre"], p["handle"].replace("-", " "))]
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for p in self.candidats(plante):
            url = f"{BASE}/products/{p['handle']}"
            o, a = self._analyser(plante, url, p, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, titre):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        latin = _latin(titre)
        v = M.variete_fiches(plante, latin) or M.variete_fiches(plante, titre)
        if v:
            return v, True
        m = CULTIVAR.search(latin) or CULTIVAR.search(titre)
        if m:
            return m.group(1).strip(), False
        for n in M.noms_latins(plante) + [plante.genre]:
            r = M._apres(latin, n)
            if r is not None:
                r = re.sub(r"[®™]", "", r).strip(" '\"‘’()")
                return r, not r
        return "", True

    def _analyser(self, plante, url, p, aujourdhui):
        titre = p["titre"]
        latin = _latin(titre)
        notes_page = []
        if M.latin_sur_fiche(plante, [latin]):
            equiv = M.equivalence_utilisee(plante, [latin])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, [latin])
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(latin), plante.genre) and M.variete_fiches(plante, titre):
                notes_page.append(f"Espèce absente de la fiche (« {titre} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {latin} » différent de {plante.nom_latin}")]
        if M.est_melange(titre):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, titre)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        lignes = []
        for v in p["variantes"]:
            if not v["prix"]:
                continue
            notes = list(notes_page)
            brut = "" if v["titre"] == "Default Title" else v["titre"]
            fmt = brut.split(" - ")[0]
            cont = contenant(fmt)
            if re.search(r"\bscion\b", fmt, re.I):
                notes.append("scion" + (" ramifié" if "ramifi" in fmt.lower() else ""))
            elif re.search(r"godet forestier", fmt, re.I):
                notes.append("godet forestier")
            if not cont:
                notes.append(f"Format non reconnu : « {brut} »" if brut else "Format non indiqué")
            m = HAUTEUR.search(brut)
            h = f"{m.group(1)}-{m.group(2)} cm" if m else ""
            try:
                if v["barre"] and float(v["barre"]) > float(v["prix"]) + 0.005:
                    notes.append(f"Promotion (prix barré {prix_fr(v['barre'])} €)")
            except (TypeError, ValueError):
                pass
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(v["prix"]),
                      "EUR", cont, "En stock" if v["dispo"] else "Rupture", _port(v.get("grammes")), aujourdhui, "", "À vérifier",
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
