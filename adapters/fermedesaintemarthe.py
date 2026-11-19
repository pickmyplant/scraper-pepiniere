"""Adaptateur Ferme de Sainte Marthe (https://www.fermedesaintemarthe.com, Shopify, prix en EUR).

Méthode : l'index du catalogue vient de /products.json (pages de 250 produits, autorisé par robots.txt),
mis en cache 7 jours dans cache/. Il donne titre, description, formats (variantes), prix, prix barré
et disponibilité ; aucune page produit n'est ouverte.
Titre en majuscules et en français (« POMMIER BONNE HOTTURE AB », « AGASTACHE ANISÉE AB - PLANT ») :
le nom latin est lu dans la description (« Nom latin : Agastache foeniculum », « ( Malus domestica en latin) »,
ou la première parenthèse du texte).
Format : déduit du type de produit (graines, bulbes, plants, fruitiers) et du titre (« - PLANT »,
« - RACINES NUES », « (Rhizome) ») ; variantes en grammes ou en nombre de bulbes notées telles quelles.
Exclus : livres, accessoires, cadeaux, présentoirs, ateliers, mélanges et collections.
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

NOM = "Ferme de Sainte Marthe"
PREFIXE = "FSM"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.fermedesaintemarthe.com"
CATALOGUE = BASE + "/products.json?limit=250&page={n}"
DUREE_CACHE_JOURS = 7
TYPES_EXCLUS = {"livres", "accessoires", "cadeaux", "présentoirs", "ateliers"}
NOM_LATIN = re.compile(r"Nom latin\s*:\s*([^:;]{3,120})", re.I)
EN_LATIN = re.compile(r"\(\s*([A-Z][a-z]+(?:\s+[^()]{2,60}?)?)\s+en latin\s*\)")
PARENTHESE = re.compile(r"\(\s*([A-Z][a-z]+\s+[a-z×x][^()]{1,80})\)")
# Forme d'un nom latin en tête du texte capturé : Genre espèce [subsp. x] ['Cultivar'] [(synonyme)]
FORME_LATIN = re.compile(r"[A-Z][a-zë-]+(?:\s+(?:×|x)?\s*[a-z][a-zë-]+)?(?:\s+(?:subsp|ssp|var|f)\.?\s+[a-z-]+)?"
                         r"(?:(?:\s+[\wÀ-ÿ-]+){0,3}\s+['‘’][^'‘’]{2,40}['‘’])?(?:\s+\([A-Z][^()]{2,40}\))?")
CULTIVAR = M._CITE  # apostrophe intérieure gardée (« Rambour d'Hiver ») : core/matching.py, 19/11/2026
# Fin de titre : label bio, format
SUFFIXES = re.compile(r"\s*(?:-\s*)?(?:\bPLANTS?\b|\bRACINES? NUES?\b|\(\s*rhizome\s*\)|\bAB\b|\bNT\b|®|™|-)\s*$", re.I)
NB_BULBES = re.compile(r"(\d+)\s*(?:bulbes?|bubes|griffes|tubercules|caïeux|plants)\b", re.I)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _latin(desc):
    """Nom latin écrit dans la description (vide s'il n'y est pas)."""
    for motif in (NOM_LATIN, EN_LATIN, PARENTHESE):
        m = motif.search(desc[:1500])
        if m:
            f = FORME_LATIN.match(m.group(1).strip())
            if f:
                return f.group(0).strip(" ,.")
    return ""


def _nettoyer_titre(titre):
    t = titre
    while True:
        u = SUFFIXES.sub("", t).strip()
        if u == t:
            return u
        t = u


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("fermedesaintemarthe", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.produits = None

    # ---------- index du catalogue ----------
    def _index(self):
        if self.produits is not None:
            return self.produits
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_fsm_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.produits = json.load(fh)
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
                desc = _texte(p.get("body_html"))
                produits.append({
                    "handle": p["handle"], "titre": _texte(p.get("title")), "type": p.get("product_type") or "",
                    "latin": _latin(desc), "desc": desc[:600],
                    "variantes": [{"titre": v.get("title") or "", "prix": v.get("price"),
                                   "barre": v.get("compare_at_price"), "dispo": bool(v.get("available"))}
                                  for v in p.get("variants") or []],
                })
        f = os.path.join(config.DOSSIER_CACHE, f"index_fsm_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(produits, fh, ensure_ascii=False)
        log.info("Index du catalogue : %d produits (%s)", len(produits), f)
        self.produits = produits
        return produits

    def candidats(self, plante):
        res = [p for p in self._index() if M.est_candidat(plante, f"{p['latin']} {p['titre']}", "")]
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

    def _variete(self, plante, p):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        latin, titre = p["latin"], _nettoyer_titre(p["titre"])
        v = M.variete_fiches(plante, latin) or M.variete_fiches(plante, titre)
        if v:
            return v, True
        m = CULTIVAR.search(latin) or CULTIVAR.search(titre)
        if m:
            return m.group(1).strip(), False
        # Titre « CANNA INDICA CLEOPATRA » : ce qui suit le nom latin ;
        # titre français « POMMIER BONNE HOTTURE » : ce qui suit le nom commun de la BDD
        for n in M.noms_latins(plante) + [plante.nom_commun] + plante.autres_noms:
            r = M._apres(titre, n) if n else None
            if r is not None:
                r = r.strip(" '\"‘’()-")
                return (r.title(), False) if r else ("", True)
        return "", True

    def _format(self, p, brut):
        """(contenant, notes) d'une variante."""
        t, typ, notes = p["titre"], p["type"].lower(), []
        cont = contenant(brut) if brut else ""
        if not cont:
            if "graines" in typ:
                cont = "Sachet de graines"
            elif re.search(r"racines? nues?", t, re.I):
                cont = "Racines nues"
            elif re.search(r"rhizome", t, re.I):
                cont = "Rhizome"
            elif "bulbes" in typ:
                cont = "Bulbe"
            elif re.search(r"livrée? en motte", p["desc"], re.I):
                cont = "Motte"
            elif re.search(r"\bPLANTS?\b", t):
                cont = contenant(p["desc"]) if re.search(r"\b(?:godet|pot)\b", p["desc"], re.I) else ""
                notes.append("plant")
            elif typ == "arbres fruitiers":
                notes.append("scion" if re.search(r"\bscion\b", p["desc"], re.I) else "arbre fruitier")
        if brut:
            if re.search(r"\d\s*(?:g|grammes?|kg)\b", brut, re.I):
                notes.append(f"sachet de {brut}")
            else:
                m = NB_BULBES.search(brut) or re.fullmatch(r"\s*(\d+)\s*", brut)
                if m and int(m.group(1)) > 1:
                    notes.append(f"lot de {m.group(1)}")
                elif not cont:
                    notes.append(f"Format : « {brut} »")
        if not cont and not notes:
            notes.append("Format non indiqué")
        return cont, notes

    def _analyser(self, plante, url, p, aujourdhui):
        titre, latin = p["titre"], p["latin"]
        notes_page = []
        if not latin:
            notes_page.append("Nom latin absent de la fiche (lu dans le titre)")
        equiv = M.equivalence_utilisee(plante, [latin or titre])
        if equiv:
            notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if M.est_melange(titre) or re.search(r"\bcollection\b|\bkit\b", titre, re.I):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, p)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        if re.search(r"\bAB\b", titre):
            notes_page.append("bio (AB)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        lignes = []
        for v in p["variantes"]:
            if not v["prix"]:
                continue
            brut = "" if v["titre"] == "Default Title" else v["titre"]
            cont, nf = self._format(p, brut)
            notes = notes_page + nf
            try:
                if v["barre"] and float(v["barre"]) > float(v["prix"]) + 0.005:
                    notes.append(f"Promotion (prix barré {prix_fr(v['barre'])} €)")
            except (TypeError, ValueError):
                pass
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(v["prix"]),
                      "EUR", cont, "En stock" if v["dispo"] else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, "")
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
