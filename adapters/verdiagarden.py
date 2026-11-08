"""Adaptateur Verdia Garden (https://verdiagarden.com), jardinerie au Maroc, prix en dirhams (MAD).

Méthode : le site est une application React qui lit une API publique JSON (`/api/public`, autorisée par
robots.txt). Le catalogue entier (environ 250 produits) est lu en 3 pages (`/products?limit=100&page=N`),
puis seule la fiche détaillée des produits candidats (`/products/<slug>`) : nom, description, prix,
prix barré, stock et `specs` (taille du pot, hauteur).

Particularités :
- une fiche = un seul format (le titre porte souvent la hauteur : « Alpinia 200 Cm ») ; des doublons
  « (copie) » existent pour d'autres tailles ;
- les noms sont un mélange de latin approximatif et de noms commerciaux (« Croton pictum », « Faux bananier ») :
  le nom latin de la BDD est cherché dans le titre et la description ; fiche au seul nom de genre gardée
  avec « à confirmer » (décision du 07/11/2026), sinon écartée.
"""
import html
import logging
import re

from core import matching as M
from core.formatting import Offre, prix_fr
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Verdia Garden"
PREFIXE = "VER"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://verdiagarden.com"
API = BASE + "/api/public"
DEVISE = "MAD"
MOTS_FORMAT = r"(?:\d[\d.,]*\s*(?:-\s*\d[\d.,]*)?\s*(?:cm|m)\b|\d+\s*tiges?\b|\btouffe\b|\(copie\)|\bp\b|\bh\b)"


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _nom_seul(nom):
    """« Alpinia 200 Cm » -> « Alpinia » ; « Dracaena marginata 3 Tiges » -> « Dracaena marginata »."""
    return re.sub(r"\s+", " ", re.sub(MOTS_FORMAT, " ", nom or "", flags=re.I)).strip(" .-")


def _cm(txt):
    """« 40 Cm » -> « 40 cm » ; « 300-350 CM » -> « 300-350 cm » ; autre -> texte brut."""
    t = (txt or "").strip()
    m = re.match(r"^([\d.,]+)\s*(?:-\s*([\d.,]+))?\s*(cm|m)?\.?$", t, re.I)
    if m:
        u = (m.group(3) or "cm").lower()
        return f"{m.group(1)}-{m.group(2)} {u}" if m.group(2) else f"{m.group(1)} {u}"
    return t


def _hauteur_titre(nom):
    m = re.search(r"(\d[\d.,]*(?:\s*-\s*\d[\d.,]*)?)\s*cm\b", nom or "", re.I)
    return _cm(m.group(1) + " cm") if m else ""


class Adaptateur:
    def __init__(self):
        self.http = ClientPoli(BASE)
        self.catalogue = None

    def _json(self, chemin):
        d = self.http.get(API + chemin).json()
        if not d.get("success"):
            raise ValueError(d.get("message") or "réponse invalide")
        return d

    def _catalogue(self):
        if self.catalogue is None:
            produits, page, pages = [], 1, 1
            while page <= pages:
                d = self._json(f"/products?limit=100&page={page}")
                produits += d.get("data") or []
                pages = int((d.get("pagination") or {}).get("total_pages") or 1)
                page += 1
            log.info("Catalogue Verdia Garden : %d produits", len(produits))
            self.catalogue = produits
        return self.catalogue

    def candidats(self, plante):
        res = []
        for p in self._catalogue():
            nom, slug = (p.get("name") or "").strip(), p.get("slug") or ""
            seul = M.norm(_nom_seul(nom))
            if (M.est_candidat(plante, nom, slug) or M.candidat_nom_commun(plante, nom)
                    or M.contient(seul, plante.genre)):
                res.append(p)
        return res

    def releve(self, plante, aujourdhui):
        offres, anomalies, vus = [], [], set()
        cands = self.candidats(plante)
        log.info("%s %s : %d fiche(s) candidate(s)", plante.id, plante.nom_latin, len(cands))
        for p in cands:
            url = f"{BASE}/product/{p['slug']}"
            try:
                fiche = self._json(f"/products/{p['slug']}")["data"]
            except InterditParRobots:
                anomalies.append((url, "Interdit par robots.txt, non lu"))
                continue
            except Exception as e:
                if e.__class__.__name__ == "Bloque":
                    raise
                anomalies.append((url, f"Erreur de lecture : {e.__class__.__name__}"))
                continue
            o, a = self._analyser(plante, url, fiche, aujourdhui)
            for x in o:  # doublons « (copie) » identiques : une seule ligne
                cle = (x.variete, x.prix, x.contenant, x.hauteur_forme)
                if cle not in vus:
                    vus.add(cle)
                    offres.append(x)
            anomalies += a
        offres.sort(key=lambda o: o.tri)
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", DEVISE, "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        return offres, anomalies

    def _analyser(self, plante, url, f, aujourdhui):
        nom = (f.get("name") or "").strip()
        seul = _nom_seul(nom)
        desc = _texte(f.get("short_description")) + " " + _texte(f.get("description"))
        textes = [nom, seul, desc]
        notes = []
        equiv = M.equivalence_utilisee(plante, textes)
        approche = None if M.latin_sur_fiche(plante, textes) else M.latin_approche(plante, textes)
        if equiv:
            notes.append(f"Fiche nommée « {nom} » : équivalence « {equiv} » validée pour {plante.nom_latin}")
        if approche:
            notes.append(f"Nom latin écrit « {approche} » sur la fiche")
        elif not M.latin_sur_fiche(plante, textes):
            var = M.variete_fiches(plante, seul)
            if M.contient(M.norm(seul), plante.genre) and var:
                notes.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                             f"rapproché par la variété {var} de l'onglet Fiches")
            elif M.norm(seul) == M.norm(plante.genre):
                notes.append(f"Espèce non précisée sur la fiche (genre {plante.genre} seul, "
                             f"titre « {nom} ») : à confirmer")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {nom} » : aucun prix relevé")]
        if M.est_melange(nom):
            notes.append(f"Lot/mélange (« {nom} »)")

        variete, connue = M.variete_du_titre(plante, seul)
        if variete and M.norm(variete) == M.norm(plante.espece):
            variete, connue = "", True
        if variete and not connue:
            notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        specs = f.get("specs") or {}
        pot = _cm(specs.get("pot_size"))
        cont = f"Pot {pot}" if pot and re.search(r"\d", pot) else ""
        if not cont:
            notes.append("Contenant non précisé")
        haut = _cm(specs.get("height")) or _hauteur_titre(nom)
        if re.search(r"\d+\s*tiges?", nom, re.I):
            notes.append(re.search(r"\d+\s*tiges?", nom, re.I).group(0).lower())
        if re.search(r"\btouffe\b", nom, re.I):
            notes.append("En touffe")
        prix, orig = f.get("price"), f.get("original_price")
        try:
            if orig and float(orig) > float(prix):
                notes.append(f"Prix barré avant promotion : {prix_fr(orig)}")
        except (TypeError, ValueError):
            pass
        try:
            dispo = "En stock" if int(f.get("stock") or 0) > 0 else "Rupture"
        except (TypeError, ValueError):
            dispo = ""
        o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", NOM, url, prix_fr(prix), DEVISE,
                  cont, dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, haut)
        m = re.search(r"[\d.]+", pot.replace(",", "."))
        o.tri = (float(m.group(0)) if m else 0) * 100000 + float(prix or 0)
        return [o], []
