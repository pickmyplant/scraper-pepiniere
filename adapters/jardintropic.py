"""Adaptateur Jardin Tropic (https://jardintropic.com), boutique PrestaShop.

Méthode : sitemap public du module ets_seo (/module/ets_seo/sitemap -> /sitemap/product/N.xml,
titres « Nom latin 'Variété' – Nom commun » ou « Nom commun (Nom latin) ») pour trouver les fiches ;
la recherche du site est interdite par robots.txt. Sur chaque fiche, l'attribut data-product
(JSON PrestaShop) donne prix, prix barré, stock et format de la déclinaison choisie ; les autres
déclinaisons (liste déroulante ou boutons group[n]) sont lues avec ?group[n]=<valeur> en GET.
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

NOM = "Jardin Tropic"
PREFIXE = "JTR"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://jardintropic.com"
SITEMAP = BASE + "/module/ets_seo/sitemap"
CDATA = r"(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?"
EN_STOCK = {"available", "last_remaining_items"}


def _txt(x):
    t = html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", x or "")))
    return re.sub(r"\s+", " ", t).replace("\xa0", " ").strip()


def _format(desc):
    """« Pot de 7 litres – 100/120 cm » -> texte compris par core.formatting."""
    d = re.sub(r"(\d+(?:[.,]\d+)?)\s*litres?\b", r"\1L", desc, flags=re.I)
    return re.sub(r"(\d+)\s*/\s*(\d+)\s*cm", r"hauteur \1-\2 cm", d, flags=re.I)


def _nettoie_latin(t):
    """« Persea americana var.topa topa ADULTE » -> « Persea americana Topa topa » (mentions commerciales ôtées)."""
    q = re.search(r"^(.*?)\s*[‘'’\"]([^‘'’\"]+)[‘'’\"]?", t)
    if q:  # « Musa acuminata × balbisiana ‘Blue Java’ » : la variété est entre guillemets
        return " ".join(q.group(1).split()[:2]) + f" '{q.group(2).strip()}'"
    t = re.sub(r"\b(?:var|cv|subsp|ssp)\.\s*", " ", t, flags=re.I)
    t = re.sub(r"\b(?:ADULTE|SP[EÉ]CIMEN|XXL|de greffe|greff[ée]e?(?: et clon[ée]e?)?|clon[ée]e?)\b", " ", t, flags=re.I)
    t = re.sub(r"\s+(?:à|aux?|avec|pour)\s.*$", "", t)
    return re.sub(r"\s+", " ", t).strip()


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("jardintropic", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.entrees = None

    # ---------- sitemap ----------
    def _sitemap(self):
        if self.entrees is not None:
            return self.entrees
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        cache = os.path.join(config.DOSSIER_CACHE, f"sitemap_jardintropic_{dt.date.today():%Y-%m-%d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                self.entrees = json.load(f)
            return self.entrees
        index = self.http.get(SITEMAP).text
        entrees, vus = [], set()
        for loc in re.findall(rf"<loc>{CDATA}</loc>", index, re.S):
            loc = html.unescape(loc.strip())
            if "/sitemap/product/" not in loc:
                continue
            xml = self.http.get(loc).text
            for bloc in re.findall(r"<url>(.*?)</url>", xml, re.S):
                u = re.search(rf"<loc>{CDATA}</loc>", bloc, re.S)
                t = re.search(rf"<image:title>{CDATA}</image:title>", bloc, re.S)
                if u and u.group(1).strip() not in vus:
                    vus.add(u.group(1).strip())
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
        if not res:  # repli : nom commun et autres noms (le nom latin est vérifié sur la fiche)
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
    def _choix(page):
        """[(groupe, valeur)] de toutes les déclinaisons proposées (listes et boutons)."""
        i = page.find('class="product-variants"')
        if i < 0:
            return []
        j = page.find('class="product-add-to-cart"', i)
        bloc = page[i: j if j > 0 else i + 30000]
        res = []
        for s in re.finditer(r'<select[^>]*name="group\[(\d+)\]"[^>]*>(.*?)</select>', bloc, re.S):
            for v in re.findall(r'<option value="(\d+)"', s.group(2)):
                res.append((s.group(1), v))
        for m in re.finditer(r'<input[^>]*name="group\[(\d+)\]"[^>]*value="(\d+)"', bloc):
            res.append((m.group(1), m.group(2)))
        return res

    def _lire(self, url):
        try:
            return self.http.get(url).text, None
        except InterditParRobots:
            return None, "Interdit par robots.txt, non lu"
        except Exception as e:
            if e.__class__.__name__ == "Bloque":
                raise
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                return None, f"Fiche retirée du site (HTTP {code})"
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
            o, a = self._analyser(plante, url, page, aujourdhui)
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

    def _analyser(self, plante, url, page, aujourdhui):
        d = self._donnees(page)
        if not d:
            return [], [(url, "Pas de données produit (data-product) : page catégorie ou article")]
        nom = _txt(d.get("name", ""))
        h1 = _txt(" ".join(re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S)[:3]))
        sci = re.search(r"Nom scientifique\s*:?\s*</strong>(.*?)</p>", html.unescape(d.get("description") or ""), re.S)
        textes = [nom, h1, _txt(d.get("description_short", "")), _txt(sci.group(1)) if sci else ""]
        notes_prod = []

        var_fiches = M.variete_fiches(plante, nom)
        if not M.latin_sur_fiche(plante, textes):
            approche = M.latin_approche(plante, textes)
            if approche:
                notes_prod.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(" ".join(textes)), plante.genre) and var_fiches:
                notes_prod.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                                  f"rapproché par la variété {var_fiches} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {nom} » : aucun prix relevé")]
        eq = M.equivalence_utilisee(plante, textes)
        if eq:
            notes_prod.append(f"Nom latin écrit {eq} sur le site")
        if re.search(r"seconde vie", nom, re.I):
            notes_prod.append("Offre « Seconde vie » (plante déclassée vendue moins cher)")

        # partie latine du titre : « Latin 'Var' – Nom commun », « Nom (Latin) » ou « NOM : Latin var. X »
        nom_v = re.sub(r"^seconde vie\s+", "", nom, flags=re.I)
        morceaux = [x.strip() for x in re.split(r"\s+[–—-]\s+|\s*:\s+|[()]", nom_v) if x.strip()]
        latin = _nettoie_latin(next((x for x in morceaux if M.contient(M.norm(x), plante.genre)), nom_v))
        if M.est_melange(nom):
            notes_prod.append(f"Lot/mélange de la même espèce (« {nom} »)")
            variete, connue = "Mélange", False
        else:
            variete, connue = M.variete_du_titre(plante, latin)
            # « var. Lamb Hass » ne doit pas devenir « Hass » (variété de l'onglet Fiches contenue dans le nom)
            brut = next((r for n in M.noms_latins(plante) for r in [M._apres(latin, n)] if r), "")
            brut = brut.strip(" '\"‘’")
            if connue and brut and M.norm(brut) != M.norm(variete):
                variete, connue = brut, False
            variete = re.sub(r"[®™*]", "", variete)
            variete = re.sub(r"^(?:var\.?|ssp\.?|subsp\.?|cv\.?)\s+", "", variete.strip(" '\"‘’"),
                             flags=re.I).strip(" '\"‘’")
        if variete and variete != "Mélange" and not connue:
            notes_prod.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete and variete != "Mélange" else plante.nom_latin
        variete_col = variete or "Espèce type"

        # une donnée par déclinaison : celle de la page, puis chaque autre valeur de chaque groupe
        donnees, ids = [d], {d.get("id_product_attribute")}
        choisis = {str(a.get("id_attribute")) for a in (d.get("attributes") or {}).values()}
        for g, v in self._choix(page):
            if v in choisis:
                continue
            p2, err = self._lire(f"{url}?{quote(f'group[{g}]')}={v}")
            d2 = self._donnees(p2) if p2 else None
            if d2 and d2.get("id_product_attribute") not in ids:
                ids.add(d2.get("id_product_attribute"))
                donnees.append(d2)

        lignes = []
        for x in donnees:
            attrs = list((x.get("attributes") or {}).values())
            fmt = " - ".join(a.get("name", "") for a in attrs if not re.search(r"couleur", a.get("group", ""), re.I))
            couleurs = [a.get("name", "") for a in attrs if re.search(r"couleur", a.get("group", ""), re.I)]
            if not fmt.strip():  # fiche sans déclinaison : format écrit dans le titre
                fmt = re.sub(r"Ø\s*", "Pot ", re.sub(r"↕\s*", "hauteur ", nom))
            dispo = "En stock" if x.get("availability") in EN_STOCK and (x.get("quantity") or 0) > 0 else "Rupture"
            notes = list(notes_prod)
            tronc = re.search(r"p[ée]rim[eè]tre de tronc\s*([\d/ -]+)\s*cm", fmt, re.I)
            if tronc:
                notes.append(f"Circonférence du tronc {tronc.group(1).strip()} cm")
                fmt = fmt.replace(tronc.group(0), "")
            df = _format(fmt)
            cont = contenant(df)
            h, f = hauteur(df), forme(df)
            if h and f:
                notes.append(f"Forme : {f}")
            if couleurs:
                notes.append("Couleur : " + ", ".join(couleurs))
            n_lot = lot(fmt) or lot(nom)
            if n_lot:
                notes.append(f"lot de {n_lot}")
            prix = x.get("price_amount")
            avant = x.get("price_without_reduction")
            if x.get("has_discount") and avant and float(avant) > float(prix) + 0.005:
                notes.append(f"Prix barré avant promotion : {prix_fr(avant)}")
            if dispo == "En stock" and x.get("availability") == "last_remaining_items":
                notes.append("Derniers articles en stock")
            if re.search(r"importation", x.get("availability_message") or "", re.I):
                notes.append("Délai d'importation annoncé : 15/20 jours ouvrables")
            if not cont:
                notes.append(f"Format non reconnu : « {fmt} »")
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
