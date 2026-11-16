"""Adaptateur Pépinières Croux (https://www.croux.fr, PrestaShop, arbustes, vivaces, arbres, terre de bruyère).

Méthode : la recherche du site est interdite par robots.txt ; le catalogue vient du sitemap public
(/1_fr_0_sitemap.xml : adresse + titre de l'image, ex. « ACER palmatum Bloodgood »). Une fiche est candidate si
son titre ou son adresse contient un nom latin de la BDD (ou le genre et une variété de l'onglet Fiches).
Chaque fiche est ouverte : nom (<h1>, ex. « ACER palmatum 'Bloodgood' »), prix, prix barré et disponibilité de
la déclinaison choisie viennent des données produit (data-product). Les déclinaisons combinent « Présentation »
(pot de 5L, pot de 10L…) et « Taille » (Touffe, 60/80…) : les autres sont lues en rouvrant la fiche avec
?group[1]=<valeur> (puis ?group[1]=..&group[2]=.. pour les autres tailles du même pot), adresse permise par
robots.txt. Disponibilité : « availability » = available (le site refuse les commandes hors stock).
"""
import html
import json
import logging
import re

import config
from core import matching as M
from core.formatting import Offre, contenant, lot, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Pépinières Croux"
PREFIXE = "CRX"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.croux.fr"
SITEMAP = BASE + "/1_fr_0_sitemap.xml"
# Plafond de pages lues par fiche (une par déclinaison)
MAX_DECLINAISONS = 8
SELECT = re.compile(r'<select[^>]*data-product-attribute="(\d+)"[^>]*>(.*?)</select>', re.S)
OPTION = re.compile(r'<option value="(\d+)" title="([^"]*)"( selected)?', re.S)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("croux", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.catalogue = None
        self.fiches = {}

    # ---------- catalogue ----------
    def _catalogue(self):
        if self.catalogue is None:
            t = self.http.get(SITEMAP).text
            self.catalogue = []
            for bloc in re.findall(r"<url>(.*?)</url>", t, re.S):
                loc = re.search(r"<loc>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</loc>", bloc, re.S)
                if not loc or not re.search(r"/\d+-[^/]+\.html$", loc.group(1).strip()):
                    continue
                titre = re.search(r"<image:title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</image:title>", bloc, re.S)
                self.catalogue.append({"url": loc.group(1).strip(), "titre": _texte(titre.group(1)) if titre else ""})
            log.info("Croux : %d fiches dans le sitemap", len(self.catalogue))
        return self.catalogue

    def candidats(self, plante):
        res = [p for p in self._catalogue() if M.est_candidat(plante, p["titre"], p["url"])]
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    @staticmethod
    def _lire(t):
        d = re.search(r'data-product="([^"]*)"', t)
        d = json.loads(html.unescape(d.group(1))) if d else {}
        groupes = []
        for g, corps in SELECT.findall(t):
            opts = [(v, _texte(n), bool(s)) for v, n, s in OPTION.findall(corps)]
            groupes.append((g, opts))
        attrs = sorted((d.get("attributes") or {}).values(), key=lambda a: a.get("group") != "Présentation")
        attrs = [a.get("name", "") for a in attrs]
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S)
        return {"nom": _texte(h1.group(1)) if h1 else _texte(d.get("name", "")), "groupes": groupes,
                "decl": {"id": d.get("id_product_attribute"), "attrs": attrs, "prix": d.get("price_amount"),
                         "barre": d.get("price_without_reduction") if d.get("has_discount") else None,
                         "dispo": d.get("availability") == "available"}}

    def _fiche(self, url):
        if url in self.fiches:
            return self.fiches[url]
        base = url.split("?")[0].split("#")[0]
        p = self._lire(self.http.get(base).text)
        decls = {p["decl"]["id"]: p["decl"]}
        choisis = {g: next((v for v, _, s in o if s), None) for g, o in p["groupes"]}
        essais = []
        if p["groupes"]:
            g1, opts1 = p["groupes"][0]
            essais = [((g1, v),) for v, _, s in opts1 if not s]
            # Autres tailles du pot par défaut
            for g, opts in p["groupes"][1:]:
                essais += [((g1, choisis[g1]), (g, v)) for v, _, s in opts if not s]
        vus = set()
        while essais and len(decls) < MAX_DECLINAISONS:
            e = essais.pop(0)
            if e in vus:
                continue
            vus.add(e)
            q = "&".join(f"group%5B{g}%5D={v}" for g, v in e)
            q2 = self._lire(self.http.get(f"{base}?{q}").text)
            decls.setdefault(q2["decl"]["id"], q2["decl"])
            if len(e) == 1:  # autres tailles proposées pour ce pot
                for g, opts in q2["groupes"][1:]:
                    essais += [(e[0], (g, v)) for v, _, s in opts if not s]
        self.fiches[url] = {"nom": p["nom"], "decls": list(decls.values())}
        return self.fiches[url]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for p in self.candidats(plante):
            url = p["url"]
            try:
                fiche = self._fiche(url)
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
            o, a = self._analyser(plante, url, fiche, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, titre):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        titre = re.sub(r"(?i)(^|\s)[x×](?=\s)", " ", titre)
        v = M.variete_fiches(plante, titre)
        if v:
            return v, True
        for n in M.noms_latins(plante) + [plante.genre]:
            reste = M._apres(titre, re.sub(r"(^|\s)[x×](?=\s)", " ", n))
            if reste is not None:
                reste = re.sub(r"[®™]", "", reste).strip(" '\"‘’()")
                return reste, not reste
        return "", True

    def _analyser(self, plante, url, fiche, aujourdhui):
        titre = fiche["nom"] or url
        notes_page = []
        if M.latin_sur_fiche(plante, [titre]):
            equiv = M.equivalence_utilisee(plante, [titre])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, [titre])
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.norm(titre).split()[:1] == [M.norm(plante.genre)] and M.variete_fiches(plante, titre):
                notes_page.append(f"Espèce absente de la fiche (« {titre} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {titre} » différent de {plante.nom_latin}")]
        if M.est_melange(titre):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, titre)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        lignes = []
        for d in fiche["decls"]:
            if not d["prix"]:
                continue
            notes = list(notes_page)
            pres = d["attrs"][0] if d["attrs"] else ""
            taille = " ".join(d["attrs"][1:])
            cont = contenant(pres)
            nb = lot(pres)
            if nb:
                notes.append(f"lot de {nb}")
            if not cont:
                notes.append(f"Format non reconnu : « {pres} »" if pres else "Format non indiqué")
            if d["barre"] and d["barre"] > d["prix"] + 0.005:
                notes.append(f"Promotion (prix barré {prix_fr(d['barre'])} €)")
            h = f"{taille} cm" if re.fullmatch(r"\d+(?:/\d+)?", taille) else taille
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(d["prix"]),
                      "EUR", cont, "En stock" if d["dispo"] else "Rupture", "", aujourdhui, "", "À vérifier",
                      " ; ".join(notes), nom_latin, h)
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
