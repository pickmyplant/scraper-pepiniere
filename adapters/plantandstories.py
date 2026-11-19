"""Adaptateur Plant and Stories (https://plantandstories.com, Shopify, prix en EUR).

Méthode : l'index du catalogue vient de /products.json (pages de 250 produits, autorisé par
robots.txt), mis en cache 7 jours dans cache/. Il donne titre, description, formats (variantes), prix,
prix barré et disponibilité ; aucune page produit n'est ouverte.
Le nom latin est dans le titre (« Nom commun 'Cultivar' (Genre espèce) - … - Pot 21 cm - Hauteur 90 cm »
ou « Genre espèce 'Cultivar' - Nom commun - Pot de 24cm - Hauteur 70cm ») ; le pot et la hauteur sont
dans le titre, sinon dans la variante (« 12cm ») ou la description.
Variantes : seules les « Sans cache-pot » sont relevées quand elles existent ; « N plants » / « Lot de N »
notés en lot. Exclus : tout ce qui n'est pas rangé sous « Plantes d'intérieur » ou « Plantes d'extérieur »
(accessoires, cartes cadeaux), et les assortiments.
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

NOM = "Plant and Stories"
PREFIXE = "PAS"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://plantandstories.com"
CATALOGUE = BASE + "/products.json?limit=250&page={n}"
DUREE_CACHE_JOURS = 7
RAYONS_PLANTES = ("plantes d'intérieur", "plantes d'extérieur")
SOUS_RAYONS_EXCLUS = {"assortiment", "assortiments", "coffret", "coffrets"}
CACHE_POT = re.compile(r"cache-pot|panier|pot (?:blanc|gris|noir|terre|beige|rose|vert|bleu)", re.I)
SANS_CACHE_POT = re.compile(r"sans cache-pot", re.I)
NB = re.compile(r"\b(?:lot|pack) de (\d+)\b|\b(\d+) (?:plantes|plants|bulbes|pieds|godets|plantes \(lot\))\b", re.I)
CULTIVAR = M._CITE  # apostrophe intérieure gardée (« Rambour d'Hiver ») : core/matching.py, 19/11/2026


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _nb(texte):
    t = (texte or "").strip()
    if re.fullmatch(r"\d+", t):  # option « Quantité » : 1, 2…
        return int(t) if int(t) > 1 else None
    for m in NB.finditer(texte or ""):
        n = int(m.group(1) or m.group(2))
        if n > 1:
            return n
    return None


def _format_titre(titre, handle):
    """Format écrit dans le titre (« | Pot 17 cm », « pot Ø6 cm ») ou l'adresse (« …-pot-17cm-70cm »)."""
    m = re.search(r"pot\s*(?:de\s*)?Ø?\s*(\d+)\s*cm", titre, re.I) or re.search(r"(?:^|-)pot-(\d+)-?cm", handle)
    cont = f"Pot {m.group(1)} cm" if m else ""
    m = re.search(r"\bhauteur\s*(\d+(?:\s*-\s*\d+)?)\s*cm", titre, re.I) or re.search(r"\bH\s*(\d+)\s*cm", titre) or re.search(r"pot-\d+-?cm-(?:hauteur-)?(\d+)-?cm", handle)
    return cont, (f"{re.sub(r'\s+', '', m.group(1))} cm" if m else "")


def _contenant(desc):
    """Pot livré lu dans la description (premier format cité)."""
    d = re.sub(r"(\d)\.0\b", r"\1", desc or "")
    d = re.sub(r"pot(s)? de culture", r"pot\1", d, flags=re.I)
    d = re.sub(r"(pot|godet)(s)?\s*(?:de\s*)?Ø\s*", r"\1 de ", d, flags=re.I)
    m = re.search(r"\b(?:pot|godet)s?\s+(?:de\s+)?[\d.,]+\s*(?:cm|l|litres?)\b", d, re.I)
    if m:
        return contenant(m.group(0))
    m = re.search(r"diam[eè]tre de ([\d.,]+)\s*cm", d, re.I)
    if m:
        return f"Pot {m.group(1)} cm"
    if re.search(r"\bkokedama\b", d, re.I):
        return "Kokedama"
    if re.search(r"\bbulbes?\b", d, re.I):
        return "Bulbe"
    return ""


def _hauteur(desc):
    d = re.sub(r"(\d)\.0\b", r"\1", desc or "")
    m = re.search(r"hauteur[^\d.]{0,30}([\d,]+)\s*(?:(?:/|à|-)\s*([\d,]+)\s*)?(cm|m)\b", d, re.I)
    if not m:
        m = re.search(r"(?:plants?|plante|mesure|livrée?s?)[^.\d]{0,40}?environ ([\d,]+)\s*(?:(?:/|à|-)\s*([\d,]+)\s*)?(cm|m)\b",
                      d, re.I)
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2)} {m.group(3)}" if m.group(2) else f"{m.group(1)} {m.group(3)}"


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("plantandstories", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_pas_*.json")), reverse=True):
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
                    "handle": p["handle"], "titre": _texte(p.get("title")), "type": p.get("product_type") or "",
                    "tags": p.get("tags") or [], "texte": _texte(p.get("body_html")),
                    "variantes": [{"titre": v.get("title") or "", "prix": v.get("price"),
                                   "barre": v.get("compare_at_price"), "dispo": bool(v.get("available"))}
                                  for v in p.get("variants") or []],
                })
        f = os.path.join(config.DOSSIER_CACHE, f"index_pas_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        noms_communs = [plante.nom_commun] + plante.autres_noms
        res, vus = [], set()
        for p in self._index():
            rayons = [x.strip().lower() for x in p["type"].split(">")]
            if rayons[0] not in RAYONS_PLANTES or SOUS_RAYONS_EXCLUS & set(rayons) or p["handle"] in vus:
                continue
            tn = M.norm(f"{p['titre']} {p['handle'].replace('-', ' ')}")
            if (M.contient(tn, plante.genre) or any(M.contient(tn, n) for n in noms_communs)
                    or any(M.contient(tn, n) for n in M.noms_latins(plante))):
                vus.add(p["handle"])
                res.append(p)
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

    def _variete(self, plante, titre, handle):
        v = M.variete_fiches(plante, f"{titre} {handle.replace('-', ' ')}")
        if v:
            return v, True
        m = CULTIVAR.search(titre)
        if m:
            return m.group(1).strip(), False
        # « Nom commun Cultivar - Genre espèce » ou « Nom commun Cultivar (Genre espèce), description, Pot … » :
        # ce qui suit le nom commun, avant le tiret, la parenthèse ou la virgule (19/11/2026 : la description
        # « arbuste fleuri rustique » n'est plus lue comme une variété)
        avant = re.split(r"\s[-–]\s|\s*\(|,", titre)[0]
        for n in M.noms_latins(plante) + [plante.genre]:
            r = M._apres(avant, n)
            if r is not None:
                r = re.sub(r"\(.*?\)", "", r).strip(" '\"‘’()")
                return r, not r
        if re.search(r"\s[-–]\s|\(|,", titre):
            for n in sorted([plante.nom_commun] + plante.autres_noms, key=len, reverse=True):
                r = M._apres(avant, n)
                if r:
                    return r.strip(" '\"‘’()"), False
        return "", True

    def _analyser(self, plante, url, p, aujourdhui):
        titre = p["titre"]
        entete = f"{titre} {p['handle'].replace('-', ' ')}"
        notes_page = []
        if M.latin_sur_fiche(plante, [entete]) or (
                M.latin_sur_fiche(plante, [p["texte"]]) and not M.est_melange(titre)
                and (M.contient(M.norm(entete), plante.genre) or M.candidat_nom_commun(plante, titre))):
            equiv = M.equivalence_utilisee(plante, [entete, p["texte"]])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
            if not M.latin_sur_fiche(plante, [entete]):
                notes_page.append("Nom latin lu dans la description")
        else:
            approche = M.latin_approche(plante, [entete, p["texte"]])
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(entete), plante.genre) and M.variete_fiches(plante, entete):
                notes_page.append("Espèce absente de la fiche ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Fiche « {titre} » : {plante.nom_latin} absent du titre et de la description")]
        if M.est_melange(titre):
            return [], [(url, f"Composition ou collection (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, titre, p["handle"])
        if variete and variete == variete.lower():
            variete = variete.title()
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
        if "duo plante et pot" in p["type"].lower() and not any(SANS_CACHE_POT.search(v["titre"]) for v in p["variantes"]):
            notes_page.append("vendu avec un pot décoratif")
        cont_t, h_t = _format_titre(titre, p["handle"])
        cont = cont_t or _contenant(p["texte"])
        h = h_t or _hauteur(p["texte"])
        nb_titre = _nb(titre)

        variantes = p["variantes"]
        sans_cp = [v for v in variantes if SANS_CACHE_POT.search(v["titre"])]
        if sans_cp:
            garde_v = sans_cp
            notes_page.append("aussi vendu avec cache-pot")
        else:
            garde_v = [v for v in variantes if not CACHE_POT.search(v["titre"])] or variantes
        lignes = []
        for v in garde_v:
            notes = list(notes_page)
            brut = "" if v["titre"] == "Default Title" else v["titre"]
            m_pot = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*cm\s*", brut)
            cont_v = f"Pot {m_pot.group(1)} cm" if m_pot else cont
            if m_pot:
                brut = ""
            nb = _nb(brut) or nb_titre
            if nb:
                notes.append(f"lot de {nb}")
            if brut and not nb and not re.fullmatch(r"(?:1|1 plantes?|1 plants?|unit[ée]|Sans cache-pot)", brut.strip(), re.I) \
                    and not SANS_CACHE_POT.search(brut):
                notes.append(f"Format : « {brut} »")
            if not cont_v:
                notes.append("Format non reconnu (pot non indiqué)")
            try:
                if v["barre"] and float(v["barre"]) > float(v["prix"]):
                    notes.append(f"Promotion (prix barré {prix_fr(v['barre'])} €)")
            except (TypeError, ValueError):
                pass
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(v["prix"]),
                      "EUR", cont_v, "En stock" if v["dispo"] else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, h)
            o.tri = taille_contenant(cont_v) * 1000 + (nb or 1) * 10 + float(v["prix"] or 0) / 1000
            lignes.append(o)
        if not lignes:
            return [], [(url, "Aucun format sur la fiche")]
        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            return [Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, "", "EUR", cont,
                          "Rupture", "", aujourdhui, "", "À vérifier",
                          " ; ".join(notes_page + ["Aucun format en stock"]), nom_latin, h)], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
