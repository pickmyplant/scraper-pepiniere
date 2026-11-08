"""Adaptateur Planfor (https://www.planfor.fr).

Méthode : recherche du site en GET (autorisée par robots.txt) pour trouver les fiches,
puis JSON-LD schema.org Product de chaque fiche. Une fiche peut regrouper plusieurs
variétés : la variété est lue offre par offre.
"""
import html
import json
import logging
import re
from urllib.parse import quote_plus, urljoin

import config
from core import matching as M
from core.formatting import Offre, contenant, forme, hauteur, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Planfor"
BASE = "https://www.planfor.fr"
RECHERCHE = BASE + "/index.php?action=formulaire_recherche&motclef={q}&page=1"
# Début de la partie « format » dans le nom d'une offre
DEBUT_FORMAT = re.compile(r"(Photos? disponibles?|Jeune plant|Plante en |Plant |Plants |Lot de|Excellent|Livraison|Racines? nues?|Godet|Motte)")
# Noms de variété trop génériques pour une recherche (ils ramènent des dizaines de produits sans rapport)
VARIETES_GENERIQUES = {"alba", "rosea", "rubra", "lutea", "variegata", "nana", "compacta", "pendula", "aurea",
                       "purpurea", "plena", "flore pleno", "pink", "white", "red", "blue", "yellow", "mix",
                       "melange", "dwarf"}
PROMO = re.compile(r"Offre exceptionnelle\s*-\s*(\d+)\s*%\s*", re.I)


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _decoller(t):
    """« litresHauteur » -> « litres Hauteur » ; « PlenifloraPhoto » -> « Pleniflora Photo »."""
    return re.sub(r"([a-zàâçéèêëîïôûù.)'])([A-ZÉ])", r"\1 \2", t)


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("planfor", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.pages = {}

    # ---------- recherche ----------
    def _chercher(self, terme):
        url = RECHERCHE.format(q=quote_plus(terme))
        r = self.http.get(url)
        if "/achat," in r.url:  # un seul résultat : le site redirige vers la fiche
            self.pages[r.url] = r.text
            return [r.url]
        t = r.text
        i = t.find("référence(s)")
        if i < 0:
            return []
        liens = re.findall(r"href=['\"]((?:https://www\.planfor\.fr/)?achat,[^'\"#]+)['\"]", t[i:])
        vus, res = set(), []
        for l in liens:
            u = urljoin(BASE + "/", l)
            if u not in vus:
                vus.add(u)
                res.append(u)
        log.info("Recherche « %s » : %d fiche(s)", terme, len(res))
        return res

    def candidats(self, plante):
        espece = re.sub(r"[×x]\s+", "", plante.espece).strip()
        res = []

        def ajouter(liste):
            for u in liste:
                if u not in res:
                    res.append(u)

        # Le moteur de Planfor trouve mal les recherches à deux mots (« Erythrina crista-galli » : 0),
        # mais un seul mot (« Erythrina ») suffit : d'où la recherche par genre seul.
        # Le nom latin est de toute façon vérifié sur chaque fiche.
        ajouter(self._chercher(f"{plante.genre} {espece}"))
        if not res:  # synonymes latins de la BDD (ex. Solanum rantonnetii pour Lycianthes rantonnetii)
            for syn in plante.synonymes_latins[:2]:
                ajouter(self._chercher(re.sub(r"[×x]\s+", "", syn).strip()))
        if not res:
            ajouter(self._chercher(plante.genre))
        if not res:
            for n in [plante.nom_commun] + plante.autres_noms[:2]:
                ajouter(self._chercher(n))
        if not res:
            for v in plante.varietes:
                nv = M.norm(v.nom)
                if nv not in M.VARIETES_IGNOREES and nv not in VARIETES_GENERIQUES and len(nv) >= 4:
                    ajouter(self._chercher(v.nom))
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def _page(self, url):
        if url not in self.pages:
            self.pages[url] = self.http.get(url).text
        return self.pages[url]

    @staticmethod
    def _produit(page):
        for b in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                d = json.loads(b)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("@type") == "Product":
                return d
        return None

    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for url in self.candidats(plante):
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

    def _variete(self, plante, libelle, latin_h1):
        """(variété, connue_dans_Fiches) pour une offre ; variété vide = espèce type."""
        # L'offre d'abord (une fiche peut regrouper plusieurs variétés), le titre de la page ensuite
        for texte in (libelle, latin_h1):
            v = M.variete_fiches(plante, texte)
            if v:
                return v, True
            for n in M.noms_latins(plante):
                reste = M._apres(texte, n)
                if reste is not None:
                    reste = re.sub(r"^\(|\)$", "", reste)
                    reste = re.sub(r"\s+\d+$", "", reste).strip(" '\"‘’()")
                    if reste:
                        return reste, False
                    break
        return "", True

    def _analyser(self, plante, url, page, aujourdhui):
        p = self._produit(page)
        h1 = " ".join(_texte(x) for x in re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S))
        latin_h1 = h1.split("/", 1)[1].strip() if "/" in h1 else ""
        if not p:
            # Fiche sans offre en vente (ni données produit ni bouton panier) : produit indisponible
            approche = None if M.latin_sur_fiche(plante, [h1]) else M.latin_approche(plante, [h1])
            if M.latin_sur_fiche(plante, [h1]) or approche:
                var, connue = self._variete(plante, "", latin_h1)
                notes = ["Aucune offre en vente sur la fiche (produit indisponible)"]
                if approche:
                    notes.insert(0, f"Nom latin écrit « {approche} » sur la fiche")
                if var and not connue:
                    notes.append(f"Variété trouvée : {var} (absente de l'onglet Fiches)")
                nl = f"{plante.nom_latin} '{var}'" if var else plante.nom_latin
                return [Offre(plante.id, plante.nom_commun, var or "Espèce type", NOM, url, "", "EUR", "",
                              "Rupture", "", aujourdhui, "", "À vérifier", " ; ".join(notes), nl, "")], []
            return [], [(url, f"Pas de données produit (JSON-LD) et nom latin non vérifié (« {h1} »)")]
        nom = _texte(p.get("name"))
        desc = _texte(p.get("description"))
        textes = [nom, h1, desc]
        notes_page = []
        approche = None if M.latin_sur_fiche(plante, textes) else M.latin_approche(plante, textes)
        equiv = M.equivalence_utilisee(plante, textes)
        if equiv:
            notes_page.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if approche:
            notes_page.append(f"Nom latin écrit « {approche} » sur la fiche")
        elif not M.latin_sur_fiche(plante, textes):
            var = M.variete_fiches(plante, h1)
            if M.contient(M.norm(" ".join(textes)), plante.genre) and var:
                notes_page.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                                  f"rapproché par la variété {var} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {h1 or nom} » : aucun prix relevé")]
        if M.est_melange(nom):
            notes_page.append(f"Lot/mélange de la même espèce (« {nom} »)")

        lignes = []
        for of in p.get("offers") or []:
            brut = _decoller(_texte(of.get("name")))
            notes = list(notes_page)
            m = PROMO.search(brut)
            if m:
                notes.append(f"Offre exceptionnelle -{m.group(1)} % (prix avant promotion non affiché sur le site)")
                brut = PROMO.sub("", brut)
            cut = DEBUT_FORMAT.search(brut)
            libelle = brut[: cut.start()].strip(" -") if cut else brut
            fmt = brut[cut.start():] if cut else ""
            variete, connue = self._variete(plante, libelle, latin_h1)
            if variete and not connue:
                notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
            cont = contenant(fmt)
            h, f = hauteur(fmt), forme(fmt)
            if h and f:
                notes.append(f"Forme : {f}")
            age = re.search(r"âgé de (\d+) ans?", fmt)
            if age:
                notes.append(f"Plant de {age.group(1)} ans")
            if "transporteur" in fmt.lower():
                notes.append("Livraison par transporteur sur rendez-vous")
            if not cont:
                notes.append(f"Format non reconnu : « {fmt or brut} »")
            dispo = "En stock" if str(of.get("availability", "")).endswith("InStock") else "Rupture"
            nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin
            o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(of.get("price")),
                      "EUR", cont, dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, h or f or "")
            o.tri = taille_contenant(cont) * 1000 + float(of.get("price") or 0)
            lignes.append(o)

        if not lignes:
            return [], [(url, "Aucune offre dans les données produit")]
        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            o = lignes[0]
            return [Offre(plante.id, plante.nom_commun, o.variete, NOM, url, "", "EUR", "", "Rupture", "",
                          aujourdhui, "", "À vérifier", " ; ".join(notes_page + ["Aucun format en stock"]),
                          o.nom_latin, "")], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
