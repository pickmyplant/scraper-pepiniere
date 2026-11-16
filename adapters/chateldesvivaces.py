"""Adaptateur Le Châtel des Vivaces (https://www.le-chatel-des-vivaces.com, PrestaShop, vivaces, arbustes, bambous).

Méthode : pas de robots.txt (404, tout est permis) ni de sitemap public ; la recherche du site (/recherche?s=)
est utilisée, avec le genre puis le nom commun. Elle est très tolérante (un mot inconnu renvoie tout le
catalogue) : seule la première page (100 fiches) est lue, et une fiche n'est candidate que si son titre ou son
adresse contient le genre, un nom latin ou un nom commun de la BDD.
Chaque fiche candidate est ouverte : le nom latin vient de la première ligne en gras de l'encadré
« fiche-variété » (« Geranium wallichianum ‘Rozanne’ — géranium vivace… »), sinon du titre <h1>.
Les formats (« Godet 9x9x10cm », « Lot de 3 godets », « Pot 3 L »), prix, prix barrés et disponibilités
viennent des cartes de déclinaisons (product-variants__card, « --out » = épuisé) ; une fiche sans déclinaison
prend le prix et la disponibilité des données produit (data-product).
"""
import html
import json
import logging
import re
from urllib.parse import quote_plus

import config
from core import matching as M
from core.formatting import Offre, contenant, forme, hauteur, lot, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Le Châtel des Vivaces"
PREFIXE = "CDV"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.le-chatel-des-vivaces.com"
# Fiches sans plante (outils, terreaux, cartes cadeaux…), reconnues à leur titre
EXCLUS = re.compile(r"\b(carte cadeau|ch[eè]que cadeau|terreau|engrais|sécateur|secateur|gant|tuteur|paillage|"
                    r"arrosoir|livre)\b", re.I)
MINIATURE = re.compile(r'<a class="product-miniature__title" href="([^"]+)"[^>]*>(.*?)</a>', re.S)
CARTE = re.compile(r'<div class="product-variants__card([^"]*)">(.*?)</label>', re.S)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))).replace("\xa0", " ").strip()


def _prix(txt):
    m = re.search(r"(\d[\d\s.]*,\d{2}|\d+(?:\.\d+)?)\s*€", txt or "")
    if not m:
        return None
    v = m.group(1).replace(" ", "")
    return float(v.replace(".", "").replace(",", ".") if "," in v else v)


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("chateldesvivaces", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.recherches = {}
        self.pages = {}

    # ---------- recherche ----------
    def _recherche(self, mots):
        cle = M.norm(mots)
        if cle not in self.recherches:
            t = self.http.get(f"{BASE}/recherche?s={quote_plus(mots)}&resultsPerPage=100").text
            self.recherches[cle] = [{"url": u.split("#")[0], "titre": _texte(n)} for u, n in MINIATURE.findall(t)]
        return self.recherches[cle]

    def candidats(self, plante):
        noms_communs = [n for n in [plante.nom_commun] + plante.autres_noms if n]
        genre = M.norm(plante.genre)
        res, vus = [], set()
        for q in [plante.genre, plante.nom_commun]:
            if not q:
                continue
            for p in self._recherche(q):
                if p["url"] in vus or EXCLUS.search(p["titre"]):
                    continue
                slug = re.sub(r"^\d+-", "", p["url"].rsplit("/", 1)[-1][:-5]).replace("-", " ")
                tn = M.norm(f"{p['titre']} {slug}")
                if (any(M.contient(tn, n) for n in M.noms_latins(plante)) or (genre and M.contient(tn, plante.genre))
                        or any(M.contient(tn, n) for n in noms_communs)):
                    vus.add(p["url"])
                    res.append(p)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def _page(self, url):
        if url not in self.pages:
            t = self.http.get(url).text
            h1 = re.search(r'<h1 class="product-hero__title">(.*?)</h1>', t, re.S)
            fv = re.search(r'class="fiche-vari[^"]*">\s*<p>\s*<strong>(.*?)</strong>', t, re.S)
            latin = _texte(fv.group(1)) if fv else ""
            latin = re.split(r"\s+[—–-]\s+", latin)[0].strip()
            lignes = []
            for classes, c in CARTE.findall(t):
                nom = re.search(r'<span class="product-variants__name">(.*?)</span>', c, re.S)
                prix = re.search(r'<span class="product-variants__price">(.*?)</span>', c, re.S)
                barre = re.search(r'<s class="product-variants__old">(.*?)</s>', c, re.S)
                lignes.append({"titre": _texte(nom.group(1)).lstrip("⭐ ").strip() if nom else "",
                               "prix": _prix(_texte(prix.group(1))) if prix else None,
                               "barre": _prix(_texte(barre.group(1))) if barre else None,
                               "dispo": "--out" not in classes and "Épuisé" not in c})
            if not lignes:
                d = re.search(r'js-product-details" data-product="([^"]*)"', t)
                d = json.loads(html.unescape(d.group(1))) if d else {}
                if d.get("price_amount"):
                    attr = " ".join(a.get("name", "") for a in (d.get("attributes") or {}).values())
                    lignes.append({"titre": attr, "prix": d.get("price_amount"),
                                   "barre": d.get("price_without_reduction") if d.get("has_discount") else None,
                                   "dispo": d.get("availability") == "available"})
            self.pages[url] = {"nom": _texte(h1.group(1)) if h1 else "", "latin": latin, "lignes": lignes}
        return self.pages[url]

    # ---------- relevé ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for p in self.candidats(plante):
            url = p["url"]
            try:
                page = self._page(url)
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            o, a = self._analyser(plante, url, page, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, latin, titre):
        """(variété, connue_dans_Fiches) ; variété vide = espèce type."""
        v = M.variete_fiches(plante, latin) or M.variete_fiches(plante, titre)
        if v:
            return v, True
        for n in M.noms_latins(plante) + [plante.genre]:
            reste = M._apres(latin, n)
            if reste is not None:
                reste = re.sub(r"[®™]", "", reste).strip(" '\"‘’()")
                return reste, not reste
        return "", True

    def _analyser(self, plante, url, page, aujourdhui):
        titre = page["nom"] or url
        latin = page["latin"] or titre
        notes_page = []
        if M.latin_sur_fiche(plante, [latin]):
            equiv = M.equivalence_utilisee(plante, [latin])
            if equiv:
                notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        else:
            approche = M.latin_approche(plante, [latin])
            mots = M.norm(latin).split()
            if approche:
                notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif mots[:1] == [M.norm(plante.genre)] and M.variete_fiches(plante, f"{latin} {titre}"):
                notes_page.append(f"Espèce absente de la fiche (« {latin} ») ; rapproché par la variété de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin « {latin} » (fiche « {titre} ») différent de {plante.nom_latin}")]
        if M.est_melange(titre):
            return [], [(url, f"Collection ou mélange (« {titre} ») : aucun prix relevé")]
        variete, connue = self._variete(plante, latin, titre)
        if variete and not connue:
            notes_page.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        lignes = []
        for v in page["lignes"]:
            notes = list(notes_page)
            brut = v["titre"]
            cont = contenant(brut)
            nb = lot(brut)
            if nb:
                notes.append(f"lot de {nb}")
            if not cont:
                notes.append(f"Format non reconnu : « {brut} »" if brut else "Format non indiqué")
            if v["barre"] and v["prix"] and v["barre"] > v["prix"]:
                notes.append(f"Promotion (prix barré {prix_fr(v['barre'])} €)")
            h = hauteur(brut) or forme(brut) or ""
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(v["prix"]),
                      "EUR", cont, "En stock" if v["dispo"] else "Rupture", "", aujourdhui, "", "À vérifier",
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
