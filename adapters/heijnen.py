"""Adaptateur Plantes Heijnen (https://www.plantesdehaies-heijnen.fr).

Méthode : l'index vient du sitemap (/sitemap.xml, ~14 000 adresses), mis en cache 7 jours dans cache/.
Les fiches sont à la racine du site (un seul segment) ; chaque format a aussi sa propre adresse
(« <fiche>-80-100-c10 »), sautée quand la fiche est déjà lue. Candidates lues d'abord : espèce (nom accepté,
synonyme, équivalence) ou variété de Fiches dans l'adresse ; puis genre seul (garde-fou de pages). Les adresses
« <genre>-<mot>- » dont le mot n'est ni l'espèce ni une variété de Fiches (ex. « malus-domestica »,
« acer-campestre ») ne sont pas ouvertes. Gros catalogue : actualisation en 2 parties (actualiser.yml).
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


def _slug(u):
    return unquote(u.split("://", 1)[-1].split("/", 1)[-1]).strip("/")


def _n(texte):
    """Texte ou adresse -> mots normalisés séparés par des tirets (« Malus × floribunda » -> « malus-x-floribunda »)."""
    return "-".join(re.findall(r"[a-z0-9]+", M.norm((texte or "").replace("×", " x ").replace("-", " "))))


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
        """Adresses candidates, les plus sûres d'abord : (prioritaires, autres).
        Prioritaires : nom d'espèce (accepté, synonyme ou équivalence validée) ou variété de l'onglet Fiches
        dans l'adresse. Autres : genre seul dans l'adresse (fiches « Genre 'Variété' » sans espèce)."""
        especes = {}  # genre -> épithètes connues (nom accepté + synonymes)
        for nom in M.noms_latins(plante):
            mots = [w for w in _n(nom).split("-") if w and w != "x"]
            if mots:
                especes.setdefault(mots[0], set()).update(mots[1:2])
        varietes = set()
        for v in plante.varietes:
            for nom in [v.nom] + list(v.synonymes):
                if _n(nom) and _n(nom) not in ("espece-type", "autres-varietes", "autres-groupes"):
                    varietes.add(_n(nom))
        prioritaires, autres = [], []
        if not hasattr(self, "normes"):
            self.normes = {s: "-" + _n(s) + "-" for s in self._index()}
        mots_var = {w for v in varietes for w in v.split("-")}
        genre_accepte = _n(plante.genre).split("-")[0]
        deja = set()
        for genre, eps in especes.items():
            trouves = [s for s, sn in self.normes.items() if f"-{genre}-" in sn]
            apres = {}
            for s in trouves:
                m = re.search(rf"-{re.escape(genre)}-(?:x-)?([a-z]+)-", self.normes[s])
                apres[s] = m.group(1) if m else ""
            # Mot juste après le genre : une autre espèce (« acer-campestre ») ou une variété absente de Fiches
            # (« malus-red-sentinel ») ; le site met l'espèce dans l'adresse quand elle est dans le nom latin.
            # Ces fiches seraient écartées à la lecture : on ne les ouvre pas (mot vu au moins 2 fois).
            freq = Counter(apres.values())
            exclues = {w for w, n in freq.items() if w and n >= 2 and w not in eps and w not in mots_var}
            for s in trouves:
                sn = self.normes[s]
                if s in deja or apres[s] in exclues:
                    continue
                if apres[s] in eps or any(f"-{v}-" in sn for v in varietes):
                    prioritaires.append(s)
                    deja.add(s)
                elif genre == genre_accepte:  # genre d'un synonyme : seulement avec l'espèce dans l'adresse
                    autres.append(s)
                    deja.add(s)
        return prioritaires, autres

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
        prioritaires, autres = self.candidats(plante)
        for i, slug in enumerate(prioritaires + autres):
            if slug in vus or any(slug.startswith(b + "-") and slug[len(b) + 1:][:1].isdigit() for b in bases_lues):
                continue  # format d'une fiche déjà lue (« <fiche>-80-100-c10 »)
            # garde-fou sur les seules adresses « genre seul » (les prioritaires sont toujours lues)
            if i >= len(prioritaires) and lues >= 3 * config.MAX_FICHES_PAR_PLANTE:
                anomalies.append((f"{BASE}/{slug}", f"Trop de pages candidates : {len(prioritaires) + len(autres) - i} "
                                                    "adresse(s) « genre seul » non lues"))
                break
            if i >= len(prioritaires):
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
