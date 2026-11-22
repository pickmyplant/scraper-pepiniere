"""Adaptateur Leaderplant (https://www.leaderplant.com), boutique Magento 2 (produits groupés).

Méthode : sitemap public (/sitemaps/sitemap.xml ; la recherche du site est interdite par robots.txt).
Les fiches sont les adresses « /acheter-<nom>-<id>.html », avec le nom commun en titre d'image.
Sur la fiche : titre h1 « Nom commun / Nom latin Variété » ; un onglet par format (« Godet - Ecoplant »,
« Ecoplant en motte », « Pot de 3L »… suivi de la hauteur livrée « 10/15cm »), et dans chaque onglet
le prix « Plante à l'unité » (microdonnées schema.org Offer : prix, disponibilité). Les lots
(« Lot de 11 plants (10+1 gratuit) ») ne sont pas relevés. Sans onglet : produit « En pause » (rupture).

Frais de port (page /frais-livraison, confirmés au panier le 22/11/2026, livraison à domicile en France métropolitaine) :
un seul colis depuis Les Sorinières (44), même prix partout (Corse sur devis). Chaque ligne porte le port de ce format
acheté seul : godet, motte, mini-motte, pot 10 cm, pot 1 L (Turboplant) et 1,5 L : 10 € ; pot 2 L : 11 € ; pot 3 à 5 L :
12 € ; pot 10 L : 28 € (au-delà de 100 € de commande Écoplant/Turboplant : 10 % du montant). Un panier mélangé paie le
port du plus gros format (godet + pot 5 L = 12 €). DPD Relais 7,90 € pour les petits formats seulement (non relevé).
"""
import datetime as dt
import html
import json
import logging
import os
import re

import config
from core import matching as M
from core.formatting import Offre, contenant, prix_fr, repartis, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Leaderplant"
PREFIXE = "LDP"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.leaderplant.com"
SITEMAP = BASE + "/sitemaps/sitemap.xml"
FICHE = re.compile(r"^https://www\.leaderplant\.com/acheter-[a-z0-9-]+\.html$")


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


# Port à domicile d'un format acheté seul (contenant -> port), vérifié au panier le 22/11/2026
PORT_PAR_FORMAT = {"Godet": 10, "Motte": 10, "Pot 10 cm": 10, "Pot 1 L": 10, "Pot 1,5 L": 10, "Pot 2 L": 11,
                   "Pot 3 L": 12, "Pot 4 L": 12, "Pot 5 L": 12, "Pot 10 L": 28}
# Formats non testés : borne basse (formats voisins vérifiés) et note
PORT_NON_VERIFIE = {"Pot 2,5 L": (11, "entre 11 et 12 €"), "Pot 7,5 L": (12, "entre 12 et 28 €")}
SEUIL_POURCENT = 100  # au-delà : 10 % du montant de la commande


def _port(cont, prix):
    """(port, note) d'un colis ne contenant que ce format."""
    note = ""
    if cont.startswith("Mini-motte"):
        port = 10
    elif cont in PORT_PAR_FORMAT:
        port = PORT_PAR_FORMAT[cont]
    elif cont in PORT_NON_VERIFIE:
        port, ecart = PORT_NON_VERIFIE[cont]
        note = f"Port du format {cont} non vérifié au panier ({ecart})"
    else:
        return "", f"Port du format « {cont or 'inconnu'} » non vérifié au panier"
    if prix > SEUIL_POURCENT:
        port = max(port, round(prix * 0.10, 2))
        if cont not in ("Godet", "Motte", "Pot 1 L"):  # 10 % confirmé pour Écoplant/Turboplant seulement
            note = " ; ".join(x for x in [note, "Port à 10 % du montant au-delà de 100 € (non vérifié pour ce format)"] if x)
    return prix_fr(port), note


def _hauteur(t):
    """« 10/15cm » -> « 10-15 cm » ; « 1m » -> « 1 m »."""
    m = re.search(r"([\d.,]+)\s*(?:cm|m)?\s*[/à-]\s*([\d.,]+)\s*(cm|m)\b", t or "", re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)} {m.group(3).lower()}"
    m = re.search(r"([\d.,]+)\s*(cm|m)\b", t or "", re.I)
    return f"{m.group(1)} {m.group(2).lower()}" if m else ""


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("leaderplant", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.entrees = None
        self.pages = {}

    # ---------- sitemap ----------
    def _sitemap(self):
        if self.entrees is not None:
            return self.entrees
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        cache = os.path.join(config.DOSSIER_CACHE, f"sitemap_ldp_{dt.date.today():%Y-%m-%d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                self.entrees = [tuple(x) for x in json.load(f)]
            return self.entrees
        xml = self.http.get(SITEMAP).text
        entrees = []
        for bloc in re.findall(r"<url>(.*?)</url>", xml, re.S):
            m = re.search(r"<loc>(.*?)</loc>", bloc)
            if not m or not FICHE.match(m.group(1).strip()):
                continue
            titre = re.search(r"<image:title>(.*?)</image:title>", bloc)
            entrees.append((m.group(1).strip(), html.unescape(titre.group(1)) if titre else ""))
        log.info("Sitemap Leaderplant : %d fiche(s)", len(entrees))
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(entrees, f, ensure_ascii=False)
        self.entrees = entrees
        return entrees

    def candidats(self, plante):
        res = []
        for url, titre in self._sitemap():
            slug = re.sub(r"^acheter-|-\d+(?:-\d+)?$", "", url.rsplit("/", 1)[-1][:-5])
            if M.est_candidat(plante, titre, slug):
                res.append(url)
        # Le nom latin n'est souvent que sur la fiche : nom commun du titre d'image, vérifié ensuite sur la fiche
        for u, t in self._sitemap():
            if u not in res and t and M.candidat_nom_commun(plante, t):
                res.append(u)
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche ----------
    def releve(self, plante, aujourdhui):
        offres, anomalies = [], []
        for url in self.candidats(plante):
            try:
                if url not in self.pages:
                    self.pages[url] = self.http.get(url).text
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            o, a = self._analyser(plante, url, self.pages[url], aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _variete(self, plante, latin):
        """(variété, connue_dans_Fiches) d'après la partie latine du titre ; vide = espèce type."""
        v = M.variete_fiches(plante, latin)
        if v:
            return v, True
        for n in M.noms_latins(plante):
            reste = M._apres(latin, n)
            if reste is not None:
                reste = re.sub(r"^(?:f|var|subsp|ssp)\.?\s+", "", reste)
                reste = re.sub(r"\s*\b(?:en\s+)?mini[- ]?mottes?\b.*$", "", reste, flags=re.I).strip(" '\"‘’()")
                if not reste or M.norm(reste) == M.norm(plante.espece):
                    return "", True
                return reste, False
        return "", True

    @staticmethod
    def _formats(page):
        """[(libellé, hauteur, prix, en_stock)] : prix « Plante à l'unité » de chaque onglet."""
        res = []
        onglets = re.findall(r'data-target="#([^"]+)"[^>]*>.*?<div class="c-name[^"]*">(.*?)</div>\s*<span>(.*?)</span>',
                             page, re.S)
        for ident, nom, haut in onglets:
            m = re.search(rf'<div class="c-tabs__item[^"]*"\s+id="{re.escape(ident)}">(.*?)(?=<div class="c-tabs__item|<div class="c-product__add)',
                          page, re.S)
            if not m:
                continue
            bloc = m.group(1)
            for u in re.finditer(r'<span class="c-kit">(.*?)</span>(.*?)(?=<span class="c-kit">|$)', bloc, re.S):
                if "unité" not in _texte(u.group(1)):
                    continue
                prix = re.search(r'itemprop="price" content="([\d.]+)"', u.group(2))
                dispo = re.search(r'itemprop="availability" content="([^"]+)"', u.group(2))
                if prix:
                    res.append((_texte(nom), _texte(haut), float(prix.group(1)),
                                bool(dispo and dispo.group(1).endswith("InStock"))))
                break
        return res

    def _analyser(self, plante, url, page, aujourdhui):
        h1 = _texte(" ".join(re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S)))
        latin = h1.split("/", 1)[1].strip() if "/" in h1 else h1
        textes = [h1]
        notes = []
        equiv = M.equivalence_utilisee(plante, textes)
        if equiv:
            notes.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if not M.latin_sur_fiche(plante, textes):
            approche = M.latin_approche(plante, textes)
            var = M.variete_fiches(plante, h1)
            if approche:
                notes.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(h1), plante.genre) and var:
                notes.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                             f"rapproché par la variété {var} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {h1} » : aucun prix relevé")]
        if M.est_melange(h1):
            notes.append(f"Lot/mélange de la même espèce (« {h1} »)")
        variete, connue = self._variete(plante, latin)
        if variete and not connue:
            notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        def ligne(prix, cont, dispo, n, haut):
            return Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix, "EUR", cont,
                         dispo, "", aujourdhui, "", "À vérifier", " ; ".join(n), nom_latin, haut)

        formats = self._formats(page)
        if not formats:
            corps = _texte(re.sub(r"<script.*?</script>", " ", page, flags=re.S))
            n = list(notes) + ["Aucun format en vente (produit en pause ou épuisé)"]
            m = re.search(r"(Cette variété sera disponible en .{0,80}?prix indicatif de [\d,]+ €)", corps)
            if m:
                n.append(m.group(1))
            return [ligne("", "", "Rupture", n, "")], []

        lignes = []
        for nom, haut, prix, stock in formats:
            mm = re.search(r"mini[- ]?motte\D*([\d.,]+)\s*cm", nom, re.I)
            cont = f"Mini-motte {mm.group(1)} cm" if mm else contenant(nom)
            n = list(notes)
            if "ecoplant" in M.norm(nom):
                n.append(f"Format « {nom} »")
            if not cont:
                n.append(f"Format non reconnu : « {nom} »")
            port, note_port = _port(cont, prix)
            if note_port:
                n.append(note_port)
            o = ligne(prix_fr(prix), cont, "En stock" if stock else "Rupture", n, _hauteur(haut))
            o.frais_port = port
            o.tri = taille_contenant(cont) * 1000 + prix
            lignes.append(o)
        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            return [ligne("", "", "Rupture", notes + ["Aucun format en stock"], "")], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
