"""Adaptateur Le Jardin du Pic Vert (https://www.jardindupicvert.com, Magento, arbustes, vivaces, fruitiers…).

Méthode : la recherche du site et toute adresse avec « ? » sont interdites par robots.txt ; le catalogue vient
du sitemap public (/media/sitemap-jpv/sitemap.xml) :
- pages espèce « /genre/<genre>/<genre-espece> » : liste des fiches de l'espèce (1re page seulement, les
  suivantes passent par ?p=2, interdit) ;
- fiches produit « /<rayon>/<n>-<nom>.html » avec le titre de l'image (nom français, ex. « Corête du Japon
  'Pleniflora' ») : retenues si le titre contient le nom commun, un autre nom ou un nom latin de la BDD.
Chaque fiche est ouverte : « Nom botanique » (ex. « Kerria japonica 'Pleniflora' ») pour vérifier l'espèce,
déclinaisons (format, prix, prix barré, disponibilité) dans le JSON-LD ProductGroup / hasVariant.
"""
import html
import json
import logging
import re

import config
from core import matching as M
from core.formatting import Offre, contenant, hauteur, forme, lot, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Le Jardin du Pic Vert"
PREFIXE = "JPV"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.jardindupicvert.com"
SITEMAP = BASE + "/media/sitemap-jpv/sitemap.xml"
FICHE = re.compile(r"^https://www\.jardindupicvert\.com/.+/\d+-[^/]+\.html$")
GENRE = re.compile(r"^https://www\.jardindupicvert\.com/genre/([^/]+)/([^/?#]+)$")


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


def _slug(texte):
    return M.norm(texte).replace(" ", "-")


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("jardindupicvert", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.catalogue = None
        self.especes = None
        self.fiches = {}

    # ---------- catalogue ----------
    def _catalogue(self):
        if self.catalogue is None:
            self.catalogue, self.especes = [], {}
            index = self.http.get(SITEMAP).text
            for sm in re.findall(r"<loc>(.*?)</loc>", index):
                t = self.http.get(sm.strip()).text
                for bloc in re.findall(r"<url>(.*?)</url>", t, re.S):
                    loc = re.search(r"<loc>(.*?)</loc>", bloc, re.S)
                    if not loc:
                        continue
                    url = html.unescape(loc.group(1).strip())
                    g = GENRE.match(url)
                    if g:
                        self.especes[g.group(2)] = url
                    elif FICHE.match(url):
                        titre = re.search(r"<image:title>(.*?)</image:title>", bloc, re.S)
                        self.catalogue.append({"url": url, "titre": _texte(titre.group(1)) if titre else ""})
            log.info("Pic Vert : %d fiches et %d pages espèce dans le sitemap", len(self.catalogue), len(self.especes))
        return self.catalogue

    def candidats(self, plante):
        """(fiches candidates, anomalies)."""
        cat = self._catalogue()
        res, anomalies = [], []
        # Pages espèce : noms latins, et « Genre + variété de Fiches » (ex. /genre/malus/malus-evereste)
        noms = M.noms_latins(plante) + [f"{plante.genre} {n}" for v in plante.varietes
                                         if M.norm(v.nom) not in M.VARIETES_IGNOREES for n in [v.nom] + v.synonymes]
        for n in noms:
            page = self.especes.get(_slug(n))
            if not page:
                continue
            try:
                t = self.http.get(page).text
            except InterditParRobots:
                continue
            for u in re.findall(r'href="(https://www\.jardindupicvert\.com/[^"]+/\d+-[^"/]+\.html)"', t):
                if u not in res:
                    res.append(u)
            if re.search(r'href="[^"]*\?p=2', t):
                anomalies.append((page, "Page espèce sur plusieurs pages : seule la 1re est lisible (?p=2 interdit "
                                        "par robots.txt) ; complété par les noms communs du sitemap"))
        for p in cat:
            if p["url"] in res:
                continue
            if M.est_candidat(plante, p["titre"], p["url"]) or M.candidat_nom_commun(plante, p["titre"]):
                res.append(p["url"])
        return res[: config.MAX_FICHES_PAR_PLANTE], anomalies

    # ---------- fiche ----------
    def _fiche(self, url):
        if url in self.fiches:
            return self.fiches[url]
        t = self.http.get(url).text
        groupe = {}
        for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', t, re.S):
            try:
                d = json.loads(m)
            except ValueError:
                continue
            for g in d.get("@graph", [d]) if isinstance(d, dict) else []:
                if g.get("@type") in ("ProductGroup", "Product"):
                    groupe = g
        bota = re.search(r">\s*Nom botanique\s*</div>\s*<div[^>]*>(.*?)</div>", t, re.S)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S)
        decls = []
        for v in groupe.get("hasVariant") or ([groupe] if groupe.get("offers") else []):
            offres = v.get("offers") or []
            offres = offres if isinstance(offres, list) else [offres]
            for o in offres:
                barre = None
                for s in o.get("priceSpecification") or []:
                    if str(s.get("priceType", "")).endswith("StrikethroughPrice"):
                        barre = s.get("price")
                decls.append({"format": v.get("disambiguatingDescription") or "", "prix": o.get("price"),
                              "barre": barre, "dispo": str(o.get("availability", "")).endswith("InStock")})
        self.fiches[url] = {"nom": _texte(groupe.get("name", "")) or (_texte(h1.group(1)) if h1 else ""),
                            "botanique": _texte(bota.group(1)) if bota else "", "decls": decls}
        return self.fiches[url]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres = []
        urls, anomalies = self.candidats(plante)
        for url in urls:
            try:
                fiche = self._fiche(url)
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                if getattr(getattr(e, "response", None), "status_code", None) in (404, 410):
                    anomalies.append((url, "Fiche retirée du site (404, encore dans le sitemap)"))
                    continue
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            o, a = self._analyser(plante, url, fiche, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, bota, nom):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        v = M.variete_fiches(plante, bota) or M.variete_fiches(plante, nom)
        if v:
            return v, True
        q = re.search(r"['‘’\"]([^'‘’\"]+)['‘’\"]", bota) or re.search(r"['‘’\"]([^'‘’\"]+)['‘’\"]", nom)
        if q:
            return re.sub(r"[®™]", "", q.group(1)).strip(), False
        b = re.sub(r"(?i)(^|\s)[x×](?=\s)", " ", bota)
        for n in M.noms_latins(plante):
            reste = M._apres(b, re.sub(r"(^|\s)[x×](?=\s)", " ", n))
            if reste:
                reste = re.sub(r"[®™]", "", reste).strip(" '\"‘’()")
                return reste, not reste
        return "", True

    def _analyser(self, plante, url, fiche, aujourdhui):
        bota, nom = fiche["botanique"], fiche["nom"] or url
        textes = [bota] if bota else [nom]
        notes_page = []
        if M.latin_sur_fiche(plante, textes):
            equiv = M.equivalence_utilisee(plante, textes)
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, textes)
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif bota and M.norm(bota).split()[:1] == [M.norm(plante.genre)] and M.variete_fiches(plante, bota):
                notes_page.append(f"Espèce absente de la fiche (« {bota} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {bota or nom} » différent de {plante.nom_latin}")]
        if M.est_melange(nom):
            return [], [(url, f"Collection ou mélange (« {nom} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, bota, nom)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        lignes = []
        for d in fiche["decls"]:
            if d["prix"] in (None, ""):
                continue
            notes = list(notes_page)
            fmt = d["format"]
            cont = contenant(fmt)
            nb = lot(fmt)
            if nb:
                notes.append(f"lot de {nb}")
            if not cont:
                notes.append(f"Format non reconnu : « {fmt} »" if fmt else "Format non indiqué")
            if d["barre"] and float(d["barre"]) > float(d["prix"]) + 0.005:
                notes.append(f"Promotion (prix barré {prix_fr(d['barre'])} €)")
            h = hauteur(fmt)
            if not h:
                m = re.search(r"\b(\d+/\d+)\b(?!\s*(?:l|cm))", fmt.lower())
                h = f"{m.group(1)} cm" if m and not cont.startswith("Pot") else ""
            hf = ", ".join(x for x in [h, forme(fmt)] if x)
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(d["prix"]),
                      "EUR", cont, "En stock" if d["dispo"] else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, hf)
            o.tri = taille_contenant(cont) * 1000 + float(d["prix"] or 0)
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
