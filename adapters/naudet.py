"""Adaptateur Pépinières Naudet (https://www.pepinieres-naudet.com), boutique PrestaShop.

Méthode : sitemap public (titres « Nom (Nom latin 'Variété') ») pour trouver les fiches,
puis l'attribut data-product de chaque fiche (prix, stock, format de la déclinaison choisie).
Les autres formats (boutons radio group[n]) sont lus avec ?group[n]=<valeur> en GET.
La recherche du site est interdite par robots.txt et n'est donc pas utilisée.
"""
import datetime as dt
import html
import json
import logging
import os
import re
from urllib.parse import quote

import config
from core import matching as M
from core.formatting import (Offre, contenant, forme, hauteur, lot, prix_fr, repartis,
                             taille_contenant)
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Pépinières Naudet"
PREFIXE = "NAU"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.pepinieres-naudet.com"
SITEMAP = BASE + "/1_index_sitemap.xml"
CDATA = r"(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?"


def _txt(x):
    """Texte brut ; les titres du sitemap sont échappés deux fois (&amp;#039;)."""
    t = html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))
    return re.sub(r"\s+", " ", t).replace("\xa0", " ").strip()


def _format_naudet(desc):
    """« Conteneur 3L - Taille 50/60cm » -> texte compris par core.formatting (Pot 3 L)."""
    d = re.sub(r"conteneur\s*([\d.,]+)\s*l\b", r"Pot de \1L", desc, flags=re.I)
    return re.sub(r"taille\s*(\d+)\s*/\s*(\d+)\s*cm", r"hauteur \1-\2 cm", d, flags=re.I)


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("naudet", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.entrees = None

    # ---------- sitemap ----------
    def _sitemap(self):
        if self.entrees is not None:
            return self.entrees
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        cache = os.path.join(config.DOSSIER_CACHE, f"sitemap_naudet_{dt.date.today():%Y-%m-%d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                self.entrees = json.load(f)
            return self.entrees
        index = self.http.get(SITEMAP).text
        entrees = []
        for loc in re.findall(rf"<loc>{CDATA}</loc>", index, re.S):
            xml = self.http.get(html.unescape(loc.strip())).text
            for bloc in re.findall(r"<url>(.*?)</url>", xml, re.S):
                u = re.search(rf"<loc>{CDATA}</loc>", bloc, re.S)
                t = re.search(rf"<image:title>{CDATA}</image:title>", bloc, re.S)
                if u and u.group(1).strip().endswith(".html"):
                    entrees.append([u.group(1).strip(), _txt(t.group(1)) if t else ""])
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(entrees, f, ensure_ascii=False)
        log.info("Sitemap : %d fiches", len(entrees))
        self.entrees = entrees
        return entrees

    def candidats(self, plante):
        vus, res = set(), []
        for url, titre in self._sitemap():
            mode = M.est_candidat(plante, titre, url)
            if mode and url not in vus:
                vus.add(url)
                res.append((url, titre, mode))
        if not res:  # repli : nom commun et autres noms
            for url, titre in self._sitemap():
                if titre and M.candidat_nom_commun(plante, titre) and url not in vus:
                    vus.add(url)
                    res.append((url, titre, "nom commun"))
        return res[: config.MAX_FICHES_PAR_PLANTE]

    # ---------- fiche produit ----------
    @staticmethod
    def _donnees(page):
        for x in re.findall(r'data-product="([^"]+)"', page):
            try:
                d = json.loads(html.unescape(x))
            except ValueError:
                continue
            if isinstance(d, dict) and "price_amount" in d:
                return d
        return None

    @staticmethod
    def _radios(page):
        """[(groupe, valeur, libellé)] des formats proposés."""
        res = []
        for m in re.finditer(r'<input class="input-radio"[^>]*>', page, re.S):
            g = re.search(r'name="group\[(\d+)\]"', m.group(0))
            v = re.search(r'value="(\d+)"', m.group(0))
            t = re.search(r'title="([^"]*)"', m.group(0))
            if g and v:
                res.append((g.group(1), v.group(1), _txt(t.group(1)) if t else ""))
        return res

    def _lire(self, url):
        try:
            return self.http.get(url).text, None
        except InterditParRobots:
            return None, "Interdit par robots.txt, non lu"
        except Exception as e:
            if e.__class__.__name__ == "Bloque":
                raise
            return None, f"Erreur de lecture : {e.__class__.__name__}"

    def releve(self, plante, aujourdhui):
        """Renvoie (offres, anomalies)."""
        offres, anomalies = [], []
        cands = self.candidats(plante)
        log.info("%s %s : %d fiche(s) candidate(s)", plante.id, plante.nom_latin, len(cands))
        for url, _titre, mode in cands:
            page, err = self._lire(url)
            if err:
                anomalies.append((url, err))
                continue
            o, a = self._analyser(plante, url, page, mode, aujourdhui)
            offres += o
            anomalies += a
        if not offres:
            offres.append(Offre(plante.id, plante.nom_commun, "", NOM, "", "", "EUR", "", "", "",
                                aujourdhui, "", "À vérifier", "Non trouvé", plante.nom_latin, ""))
        elif config.MAX_LIGNES_PAR_PLANTE:
            offres, coupe = repartis(offres, config.MAX_LIGNES_PAR_PLANTE)
            if coupe:
                offres[-1].notes = "; ".join(x for x in [offres[-1].notes, "autres formats disponibles"] if x)
        return offres, anomalies

    def _analyser(self, plante, url, page, mode, aujourdhui):
        d = self._donnees(page)
        if not d:
            return [], [(url, "Pas de données produit (data-product) : page catégorie ou article")]
        h1 = " ".join(_txt(x) for x in re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S))
        nom = h1 or _txt(d.get("name", ""))
        desc_courte = _txt(d.get("description_short", ""))
        textes = [nom, _txt(d.get("name", "")), desc_courte]
        notes_prod = []

        var_fiches = M.variete_fiches(plante, nom)
        if not M.latin_sur_fiche(plante, textes):
            if M.contient(M.norm(" ".join(textes)), plante.genre) and var_fiches:
                notes_prod.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                                  f"rapproché par la variété {var_fiches} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {nom} » : aucun prix relevé")]
        eq = M.equivalence_utilisee(plante, textes)
        if eq:
            notes_prod.append(f"Nom latin écrit {eq} sur le site")

        if M.est_melange(nom):
            notes_prod.append(f"Lot/mélange de la même espèce (« {nom} »)")
            variete, connue = "Mélange", False
        else:
            # « Bananier rustique (Musa basjoo 'Dajiao') » : la variété est lue dans la parenthèse latine
            par = re.findall(r"\(([^()]*)\)", nom)
            variete, connue = M.variete_du_titre(plante, par[-1] if par else nom)
            variete = re.sub(r"\s*®\s*", " ", variete).strip(" '")
        if variete and not connue:
            notes_prod.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete and variete != "Mélange" else plante.nom_latin
        variete_col = variete or "Espèce type"

        # une donnée par format : celle de la page, puis les autres boutons radio
        donnees = [d]
        choisis = {str(a.get("id_attribute")) for a in (d.get("attributes") or {}).values()}
        for g, v, _lib in self._radios(page):
            if v in choisis:
                continue
            p2, err = self._lire(f"{url}?{quote(f'group[{g}]')}={v}")
            d2 = self._donnees(p2) if p2 else None
            if d2:
                donnees.append(d2)

        lignes = []
        for x in donnees:
            desc = " - ".join(a.get("name", "") for a in (x.get("attributes") or {}).values())
            dispo = "En stock" if x.get("availability") == "available" else "Rupture"
            notes = list(notes_prod)
            df = _format_naudet(desc)
            cont = contenant(df)
            h, f = hauteur(df), forme(df)
            if h and f:
                notes.append(f"Forme : {f}")
            n_lot = lot(desc) or lot(nom)
            if n_lot:
                notes.append(f"lot de {n_lot}")
            prix = x.get("price_amount")
            avant = x.get("price_without_reduction")
            if x.get("has_discount") and avant and float(avant) > float(prix):
                notes.append(f"Prix barré avant promotion : {prix_fr(avant)}")
            degressifs = [f"{prix_fr(q.get('price'))} l'unité dès {q.get('quantity')}"
                          for q in x.get("quantity_discounts") or [] if isinstance(q, dict) and q.get("quantity")]
            if degressifs:
                notes.append("Prix dégressifs : " + ", ".join(degressifs))
            if not cont:
                notes.append(f"Format non reconnu : « {desc} »")
            o = Offre(plante.id, plante.nom_commun, variete_col, NOM, url, prix_fr(prix), "EUR", cont,
                      dispo, "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, h or f or "")
            o.tri = taille_contenant(cont) * 1000 + float(prix or 0)
            lignes.append(o)

        en_stock = [o for o in lignes if o.disponibilite == "En stock"]
        if not en_stock:
            n = " ; ".join(notes_prod + ["Aucun format en stock"])
            return [Offre(plante.id, plante.nom_commun, variete_col, NOM, url, "", "EUR", "", "Rupture", "",
                          aujourdhui, "", "À vérifier", n, nom_latin, "")], []
        garde, coupe = repartis(en_stock, config.MAX_LIGNES_PAR_PRODUIT)
        garde.sort(key=lambda o: o.tri)
        if coupe:
            garde[-1].notes = " ; ".join(x for x in [garde[-1].notes, "autres formats disponibles"] if x)
        return garde, []
