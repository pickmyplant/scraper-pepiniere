"""Adaptateur Bakker (https://fr-fr.bakker.com, Shopify, même plateforme que Willemse).

Méthode : l'index du catalogue vient de /products.json (pages de 250 produits, autorisé par
robots.txt), mis en cache 7 jours dans cache/. Il donne titre, description, formats (variantes),
prix, prix barré et disponibilité. Les titres sont en nom commun : les fiches candidates
(genre ou nom commun dans le titre, nom latin complet dans la description) sont ouvertes pour lire
le nom latin affiché sous le titre (bloc data-block-type="text-latin"), qui seul valide
le rapprochement.
Formats « …, vendu par lot de N » : lots du même format ; non relevés quand le format unitaire existe.
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
from core.http import InterditParRobots, ClientPoli

log = logging.getLogger("scraper")

NOM = "Bakker"
PREFIXE = "BAK"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://fr-fr.bakker.com"
CATALOGUE = BASE + "/products.json?limit=250&page={n}"
DUREE_CACHE_JOURS = 7
LATIN = re.compile(r'data-block-type="text-latin"[^>]*>\s*<h2[^>]*>(.*?)</h2>', re.S)
LOT_VENTE = re.compile(r",\s*vendu par lot de (\d+)\s*$", re.I)
# Produits sans plante (accessoires, cache-pots…)
TYPES_EXCLUS = {"engrais et protection", "entretien et matériel", "entretien du jardin", "pots et contenants pour cultiver vos plantes",
                "potagers serres et accessoires du jardin", "aménagement jardin et terrasse", "cadeaux"}


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _format(titre):
    """« Le pot / Ø 9cm / Hauteur livrée 20-30cm » -> (contenant, hauteur, nb_par_format, brut)."""
    t = titre.strip()
    nb = None
    m = re.match(r"Les (\d+) (pots|plants|pièces|mottes)\b", t, re.I)
    if m:
        nb = int(m.group(1))
    m = re.search(r"(?:paquet|sachet|lot) de (\d+)", t, re.I)
    if m and int(m.group(1)) > 1:
        nb = int(m.group(1))
    t2 = re.sub(r"\b(?:Les \d+ |Le )(?:pots?|plants?)\s*/\s*(?:Ø\s*)?", "pot ", t, flags=re.I)
    t2 = re.sub(r"(\d)\s*L\b", r"\1 L", t2)
    cont = contenant(t2)
    if not cont and re.search(r"racines? nues?", t, re.I):
        cont = "Racines nues"
    if not cont and re.match(r"La motte", t, re.I):
        cont = "Motte"
    return cont, hauteur(t), nb


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("bakker", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None
        self.pages = {}

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_bak_*.json")), reverse=True):
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
                    "texte": M.norm(_texte(p.get("body_html"))),
                    "variantes": [{"titre": v.get("title") or "", "prix": v.get("price"),
                                   "barre": v.get("compare_at_price"), "dispo": bool(v.get("available"))}
                                  for v in p.get("variants") or []],
                })
        f = os.path.join(config.DOSSIER_CACHE, f"index_bak_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        noms_communs = [plante.nom_commun] + plante.autres_noms
        res = []
        for p in self._index():
            if p["type"].lower() in TYPES_EXCLUS:
                continue
            tn = M.norm(f"{p['titre']} {p['handle'].replace('-', ' ')}")
            if (M.contient(tn, plante.genre) or any(M.contient(tn, n) for n in noms_communs)
                    or any(M.contient(p["texte"], n) for n in M.noms_latins(plante))):
                res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    def _latin(self, url):
        if url not in self.pages:
            m = LATIN.search(self.http.get(url).text)
            self.pages[url] = _texte(m.group(1)) if m else ""
        return self.pages[url]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for p in self.candidats(plante):
            url = f"{BASE}/products/{p['handle']}"
            try:
                latin = self._latin(url)
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            o, a = self._analyser(plante, url, p, latin, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, latin, titre):
        for texte in (latin, titre):
            v = M.variete_fiches(plante, texte)
            if v:
                return v, True
        for n in M.noms_latins(plante) + [plante.genre]:
            reste = M._apres(latin, n)
            if reste is not None:
                reste = reste.strip(" '\"‘’()®")
                return reste, not reste
        return "", True

    def _analyser(self, plante, url, p, latin, aujourdhui):
        titre = p["titre"]
        notes_page = []
        if not latin:
            return [], [(url, f"Pas de nom latin sur la fiche « {titre} » : aucun prix relevé")]
        if M.latin_sur_fiche(plante, [latin]):
            equiv = M.equivalence_utilisee(plante, [latin])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, [latin])
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(latin), plante.genre) and M.variete_fiches(plante, f"{latin} {titre}"):
                notes_page.append(f"Espèce absente de la fiche (« {latin} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {latin} » (fiche « {titre} ») différent de {plante.nom_latin}")]
        collection = M.est_melange(titre)
        if collection and re.search(r"[,+]| et ", latin):
            return [], [(url, f"Collection de plusieurs espèces (« {latin} ») : aucun prix relevé")]
        if collection:
            # Collection de plusieurs variétés de la même espèce : une seule ligne « Espèce type »
            notes_page.append(f"Collection de variétés (« {titre} »)")
            variete, connue = "", True
        else:
            variete, connue = self._variete(plante, latin, titre)
        if variete and variete == variete.lower():
            variete = variete.title()
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        variantes = p["variantes"]
        unitaires = [v for v in variantes if not LOT_VENTE.search(v["titre"])]
        if unitaires and len(unitaires) < len(variantes):
            notes_page.append("aussi vendu par lots")
        lignes = []
        for v in unitaires or variantes:
            notes = list(notes_page)
            brut = v["titre"]
            m = LOT_VENTE.search(brut)
            n_lot = int(m.group(1)) if m else None
            brut = LOT_VENTE.sub("", brut)
            cont, h, nb = _format(brut)
            nb = (nb or 1) * (n_lot or 1)
            if nb > 1:
                notes.append(f"lot de {nb}")
            f = forme(brut)
            if not cont:
                notes.append(f"Format non reconnu : « {v['titre']} »")
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
