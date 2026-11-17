"""Adaptateur Arboriverse (https://arbrefruitiers.com, Shopify, grossiste en arbres fruitiers).

Méthode : tout le catalogue vient de /products.json (pages de 250 produits, autorisé par robots.txt),
mis en cache 7 jours dans cache/ ; aucune fiche n'est ouverte (titre, description, formats, prix et
disponibilité y sont). Rapprochement : nom latin dans le titre ou la description ; sinon nom commun
dans le titre (beaucoup de titres n'ont pas de nom latin), avec une note.
Particuliers seulement (B2C) : seuls les produits « vente en ligne » (prix ferme, achat direct) sont
relevés ; les produits « devis » (prix planchers pour gros volumes, 10 unités minimum) et les lots
de déstockage ou mélanges en vrac sont écartés. Prix affichés hors taxes (conditions du site).
Formats à date de livraison (« Disponible » / « 01/06/2027 ») : seul « Disponible » est relevé.
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
from core.formatting import Offre, contenant, forme, hauteur, prix_fr, repartis, taille_contenant
from core.http import ClientPoli

log = logging.getLogger("scraper")

NOM = "Arboriverse"
PREFIXE = "ARF"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://arbrefruitiers.com"
CATALOGUE = BASE + "/products.json?limit=250&page={n}"
DUREE_CACHE_JOURS = 7
TAG_B2C = "vente en ligne"
EXCLUS = re.compile(r"déstockage|destockage|\bmix\b|\b\d+\s+(?:scions|plants)\b", re.I)
# Début du format dans le titre (après le nom et la variété)
# Nom commun suivi d'un qualificatif d'une autre espèce (« Cognassier du Japon » = Chaenomeles)
AUTRE_ESPECE = re.compile(r"^(?:du japon|de chine|de virginie|du canada|à fleurs|d'ornement|sauvage)\b", re.I)
# Mots latins génériques collés à la variété (« Malus Communis Elstar Domestica »)
MOTS_LATINS = {"communis", "domestica", "sativa", "sativum", "vulgaris", "greffe", "greffé"}
DEBUT_FORMAT = re.compile(
    r"\s[-–]\s|\(|\b(?:godet|gac|pot|conteneur|scion|tigette|quenouille|demi-tige|haute|tige|basse-tige|"
    r"racine|diam[eè]tre|greff[ée]|semis|bio\b|conventionnel|non[- ]trait|\d+\s*x\s*\d+)", re.I)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _format(titre):
    t = titre
    cont = contenant(t)
    if re.search(r"racines?[- ]nues?", t, re.I):
        cont = cont or "Racines nues"
    m = re.search(r"\b(\d+)\s*x\s*(\d+)\s*x\s*(\d+)", t, re.I)
    if m and (not cont or cont == "Godet"):
        cont = f"Godet {m.group(1)}x{m.group(2)}x{m.group(3)} cm"
    f = forme(t)
    if not f and re.search(r"quenouille", t, re.I):
        f = "Quenouille"
    h = hauteur(t)
    if not h:
        m = re.search(r"(\d+)\s*/\s*(\d+)\s*cm", t, re.I)
        h = f"{m.group(1)}-{m.group(2)} cm" if m else ""
    calibre = re.search(r"diam[eè]tre\s*([\d-]+)\s*cm", t, re.I)
    return cont, h, f, (f"diamètre du tronc {calibre.group(1)} cm" if calibre else "")


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("arboriverse", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None

    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_arf_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
                log.info("Index lu depuis le cache %s (%d produits)", f, len(self.produits))
                return self.produits
        produits = []
        for n in range(1, 200):
            lot = self.http.get(CATALOGUE.format(n=n)).json().get("products") or []
            if not lot:
                break
            for p in lot:
                produits.append({
                    "handle": p["handle"], "titre": _texte(p.get("title")), "tags": p.get("tags") or [],
                    "texte": _texte(p.get("body_html")),
                    "variantes": [{"titre": v.get("title") or "", "prix": v.get("price"),
                                   "barre": v.get("compare_at_price"), "dispo": bool(v.get("available"))}
                                  for v in p.get("variants") or []],
                })
        f = os.path.join(config.DOSSIER_CACHE, f"index_arf_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        latins = M.noms_latins(plante)
        noms = sorted({n for n in [plante.nom_commun] + plante.autres_noms if n}, key=len, reverse=True)
        for p in self._index():
            titre = p["titre"]
            tn, dn = M.norm(titre), M.norm(p["texte"])
            par_latin = any(M.contient(tn, n) or M.contient(dn, n) for n in latins)
            par_nom = not par_latin and any(M.contient(tn, n) for n in noms)
            if not (par_latin or par_nom):
                continue
            url = f"{BASE}/products/{p['handle']}"
            if TAG_B2C not in [t.lower() for t in p["tags"]]:
                anomalies.append((url, f"Produit sur devis pour professionnels (« {titre} ») : non relevé (B2C)"))
                continue
            if EXCLUS.search(titre):
                anomalies.append((url, f"Lot en vrac ou déstockage (« {titre} ») : non relevé (B2C)"))
                continue
            if par_nom and M.autre_espece(plante, f"{titre} {p['texte']}"):
                anomalies.append((url, f"Autre espèce sur la fiche « {titre} » : non relevé"))
                continue
            if par_nom:
                var, _ = self._variete(plante, titre, noms)
                if AUTRE_ESPECE.search(var):
                    anomalies.append((url, f"Autre espèce (« {titre} ») : non relevé"))
                    continue
            offres += self._analyser(plante, url, p, par_latin, noms, aujourdhui)
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, titre, noms):
        v = M.variete_fiches(plante, titre)
        if v:
            return v, True
        tete = re.sub(r"^\s*plants?\s+", "", titre, flags=re.I)
        cut = DEBUT_FORMAT.search(tete)
        tete = tete[: cut.start()] if cut else tete
        for n in M.noms_latins(plante) + [plante.genre] + noms:
            r = M._apres(tete, n)
            if r is not None:
                for n2 in M.noms_latins(plante) + [plante.genre]:  # « Grenadier Punica Granatum Provence »
                    r2 = M._apres(r, n2)
                    if r2 is not None:
                        r = r2
                mots_plante = {M.norm(w) for w in f"{plante.genre} {plante.espece}".split()}
                r = " ".join(w for w in r.split() if M.norm(w) not in MOTS_LATINS | mots_plante)
                r = re.sub(r"\b([DdLl]) (?=[A-ZÀ-Ýa-z])", r"\1'", r).strip(" '\"‘’()®-")
                v = M.variete_fiches(plante, r)
                if v:
                    return v, True
                return r, not r
        return "", True

    def _analyser(self, plante, url, p, par_latin, noms, aujourdhui):
        titre = p["titre"]
        notes_page = ["Prix HT (grossiste, commande en ligne)"]
        if not par_latin:
            notes_page.append("Nom latin absent de la fiche ; rapproché par le nom commun")
        variete, connue = self._variete(plante, titre, noms)
        if variete and variete == variete.lower():
            variete = variete.title()
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        sans = re.sub(r"^\s*plants?\s+", "", titre, flags=re.I)
        cut = DEBUT_FORMAT.search(sans)
        fmt = sans[cut.start():].strip(" -–") if cut else ""
        cont, h, f, calibre = _format(titre)

        variantes = p["variantes"]
        datees = [v for v in variantes if re.match(r"\d{2}/\d{2}/\d{4}$", v["titre"].strip())]
        if datees and len(datees) < len(variantes):
            notes_page.append("aussi en précommande (livraison le " + ", ".join(v["titre"] for v in datees) + ")")
            variantes = [v for v in variantes if v not in datees]
        lignes = []
        for v in variantes:
            notes = list(notes_page)
            if calibre:
                notes.append(calibre)
            if f and h:
                notes.append(f"Forme : {f}")
            if re.search(r"\bbio\b", titre, re.I):
                notes.append("Bio")
            if not cont and f:
                notes.append(f"Format : {fmt}")
            elif not cont:
                notes.append(f"Format non reconnu : « {fmt or titre} »")
            try:
                if v["barre"] and float(v["barre"]) > float(v["prix"]):
                    notes.append(f"Promotion (prix barré {prix_fr(v['barre'])} €)")
            except (TypeError, ValueError):
                pass
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(v["prix"]),
                      "EUR", cont, "En stock" if v["dispo"] else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, h or f or "")
            o.tri = taille_contenant(cont) * 1000 + float(v["prix"] or 0)
            lignes.append(o)
        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            return [Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, "", "EUR", "",
                          "Rupture", "", aujourdhui, "", "À vérifier",
                          " ; ".join(notes_page + ["Aucun format en stock"]), nom_latin, "")]
        garde, _ = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        return garde
