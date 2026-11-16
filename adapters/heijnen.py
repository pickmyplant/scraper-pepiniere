"""Adaptateur Plantes Heijnen (https://www.plantesdehaies-heijnen.fr).

Méthode : l'index vient du sitemap (/sitemap.xml, ~14 000 adresses), mis en cache 7 jours dans cache/.
Les fiches sont à la racine du site (un seul segment) ; chaque format a aussi sa propre adresse
(« <fiche>-80-100-c10 »), ramenée à la fiche de base. Candidates : genre dans l'adresse (les adresses
« <genre>-<autre espèce>- » fréquentes, ex. « malus-domestica », sont écartées d'avance).
Chaque fiche contient un objet JSON-LD (ProductGroup) écrit en JavaScript : format principal + hasVariant,
avec taille, prix, disponibilité et propriétés (« Nom latin », « Type de racine »). Le « Nom latin »
seul valide le rapprochement. Les pages /<genre>/ (catégories) sont interdites par robots.txt et non lues.
Prix dégressifs par quantité ignorés (prix unitaire).
"""
import datetime as dt
import glob
import json
import logging
import os
import re
from collections import Counter
from urllib.parse import unquote

import config
from core import matching as M
from core.formatting import Offre, forme, prix_fr, repartis, taille_contenant
from core.http import InterditParRobots, ClientPoli

log = logging.getLogger("scraper")

NOM = "Plantes Heijnen"
PREFIXE = "HEI"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.plantesdehaies-heijnen.fr"
DUREE_CACHE_JOURS = 7
JSONLD = re.compile(r"const jsonLdObject = (\{.*?\});\s*const scriptEl", re.S)
EPITHETE = re.compile(r"^[a-z]+(?:a|um|us|is|ica|ii|ae|ensis|ata|oides|ana|ina|ella|ens|or|x)$")


def _slug(u):
    return unquote(u.split("://", 1)[-1].split("/", 1)[-1]).strip("/")


def _jsonld(texte):
    m = JSONLD.search(texte)
    if not m:
        return None
    s = re.sub(r'abs\(("[^"]*")\)', r"\1", m.group(1))
    s = re.sub(r",(\s*[\]}])", r"\1", s)
    try:
        return json.loads(s)
    except ValueError:
        return None


def _prop(item, nom):
    for p in item.get("additionalProperty") or []:
        if p.get("name", "").strip().lower() == nom.lower():
            return (p.get("value") or "").strip()
    return ""


def _contenant(nom, racine):
    m = re.search(r"\bpot\s*([\d,]+)\s*l\b", nom, re.I) or re.search(r"\bC\s?(\d+(?:[,.]\d+)?)\s*$", nom)
    if m:
        return f"Pot {m.group(1).replace('.', ',')} L"
    r = (racine or "").lower()
    if "nue" in r:
        return "Racines nues"
    if "motte" in r:
        return "Motte"
    if r.startswith("pot") or "conteneur" in r:
        return "Pot"
    return ""


def _hauteur(item):
    taille = (item.get("size") or "").replace(" ", "")
    m = re.match(r"^([\d,.]+(?:-[\d,.]+)?)(cm|m)$", taille)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.search(r"\b(\d{2,3}-\d{2,3})\s+C\s?[\d,]+\s*$", item.get("name") or "")
    return f"{m.group(1)} cm" if m else ""


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("heijnen", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.slugs = None
        self.pages = {}

    # ---------- index (sitemap) ----------
    def _index(self):
        if self.slugs is not None:
            return self.slugs
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        limite = dt.date.today() - dt.timedelta(days=DUREE_CACHE_JOURS)
        for f in sorted(glob.glob(os.path.join(config.DOSSIER_CACHE, "index_hei_*.json")), reverse=True):
            if dt.date.fromisoformat(os.path.basename(f)[10:20]) >= limite:
                with open(f, encoding="utf-8") as fh:
                    self.slugs = json.load(fh)
                log.info("Index lu depuis le cache %s (%d adresses)", f, len(self.slugs))
                return self.slugs
        xml = self.http.get(BASE + "/sitemap.xml").text
        slugs = sorted({_slug(u) for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)})
        slugs = [s for s in slugs if s and "/" not in s]
        f = os.path.join(config.DOSSIER_CACHE, f"index_hei_{dt.date.today().isoformat()}.json")
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(slugs, fh, ensure_ascii=False)
        log.info("Index du sitemap : %d adresses (%s)", len(slugs), f)
        self.slugs = slugs
        return slugs

    def candidats(self, plante):
        genre = M.norm(plante.genre).replace(" ", "-")
        if not genre:
            return []
        motif = re.compile(rf"(?:^|-){re.escape(genre)}(?:-|$)")
        trouves = [s for s in self._index() if motif.search(M.norm(s.replace("-", " ")).replace(" ", "-"))]
        # Toutes gardées (une page de présentation sans prix peut préfixer les fiches) ; les adresses de format
        # d'une fiche déjà lue sont sautées dans releve(). Pour les épithètes : fiches de base seulement.
        bases = [s for s in trouves if not any(s != b and s.startswith(b + "-") for b in trouves)]
        # épithètes d'autres espèces fréquentes juste après le genre
        apres = Counter()
        for s in bases:
            m = re.search(rf"(?:^|-){re.escape(genre)}-([a-z]+)-", M.norm(s.replace("-", " ")).replace(" ", "-") + "-")
            if m:
                apres[m.group(1)] += 1
        espece = (M.norm(plante.espece or "").split() or [""])[-1]
        varietes = M.norm(" ".join(v.nom + " " + " ".join(v.synonymes) for v in plante.varietes))
        exclues = {w for w, n in apres.items() if n >= 3 and EPITHETE.match(w) and w != espece and w not in varietes.split()}
        res = []
        for s in trouves:
            sn = M.norm(s.replace("-", " ")).replace(" ", "-") + "-"
            if any(f"{genre}-{w}-" in sn for w in exclues):
                continue
            res.append(s)
        if exclues:
            log.info("%s : adresses « %s-(%s) » écartées", plante.id, genre, ", ".join(sorted(exclues)))
        return res

    def _page(self, slug):
        if slug not in self.pages:
            self.pages[slug] = _jsonld(self.http.get(f"{BASE}/{slug}").text)
        return self.pages[slug]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        vus = set()  # adresses de formats déjà relevées (une fiche = un ProductGroup)
        groupes = set()
        lues = 0
        bases_lues = set()
        for slug in self.candidats(plante):
            if slug in vus or any(slug.startswith(b + "-") and slug[len(b) + 1:][:1].isdigit() for b in bases_lues):
                continue  # format d'une fiche déjà lue (« <fiche>-80-100-c10 »)
            if lues >= 3 * config.MAX_FICHES_PAR_PLANTE:  # garde-fou (pages de présentation et formats compris)
                anomalies.append((f"{BASE}/{slug}", "Trop de pages candidates : suite non lue"))
                break
            lues += 1
            url = f"{BASE}/{slug}"
            try:
                d = self._page(slug)
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            if not d:
                log.info("%s : page sans prix (présentation de l'espèce), ignorée", url)
                continue
            bases_lues.add(slug)
            items = [d] + list(d.get("hasVariant") or [])
            for it in items:
                vus.add(_slug(it.get("url") or it.get("@id") or ""))
            gid = d.get("productGroupID") or d.get("@id")
            if gid in groupes:
                continue
            groupes.add(gid)
            o, a = self._analyser(plante, d.get("url") or url, items, aujourdhui)
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

    def _analyser(self, plante, url, items, aujourdhui):
        titre = items[0].get("name") or ""
        latin = _prop(items[0], "Nom latin") or next((_prop(i, "Nom latin") for i in items if _prop(i, "Nom latin")), "")
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
            elif M.contient(M.norm(latin), plante.genre) and M.variete_fiches(plante, latin):
                notes_page.append(f"Espèce absente de la fiche (« {latin} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {latin} » (fiche « {titre} ») différent de {plante.nom_latin}")]
        if M.est_melange(titre):
            return [], [(url, f"Mélange ou collection (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, latin, "")
        if variete and variete == variete.lower():
            variete = variete.title()
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        lignes = []
        for it in items:
            of = it.get("offers") or {}
            prix = of.get("price")
            if prix in (None, ""):
                continue
            nom = it.get("name") or ""
            lien = it.get("url") or it.get("@id") or url
            cont = _contenant(nom, _prop(it, "Type de racine"))
            notes = list(notes_page)
            if not cont:
                notes.append(f"Format non reconnu : « {nom} »")
            h = _hauteur(it)
            f = forme(re.sub(r"(?i)hauteur (?:de|du) (?:tige|tronc)", "", nom))
            if re.search(r"(?i)baliveau", nom):
                f = ", ".join(x for x in [f, "Baliveau"] if x)
            m = re.search(r"(?i)hauteur (?:de |du )(?:tige|tronc)\s*(\d+)", nom)
            if m:
                h = f"tronc {m.group(1)} cm" + (f", {h}" if h and h != f"{m.group(1)} cm" else "")
            m = re.search(r"(?i)circonf[ée]rence (?:du )?(?:tronc|tige)\s*(\d+(?:-\d+)?)", nom)
            if m:
                h = ", ".join(x for x in [f"circonférence {m.group(1)} cm", h] if x)
            dispo = "En stock" if "InStock" in (of.get("availability") or "") else "Rupture"
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, lien, prix_fr(prix),
                      "EUR", cont, dispo, "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, ", ".join(x for x in [f, h] if x))
            o.tri = taille_contenant(cont) * 1000 + float(prix)
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
