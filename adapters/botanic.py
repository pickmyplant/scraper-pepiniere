"""Adaptateur Botanic (https://www.botanic.com), jardineries (site Proximis / Rbs).

Méthode : sitemap public des produits (« /produit/<id>/<nom-format>.html », environ 53 000 fiches,
tous rayons) pour trouver les fiches ; la recherche du site est interdite par robots.txt.
Pré-sélection sur l'adresse : nom latin (ou genre + variété de l'onglet Fiches), plus le nom commun
dans les adresses qui ressemblent à une plante en pot ; le nom latin est ensuite vérifié sur la fiche
(titre, description, caractéristiques).
Fiche : JSON-LD schema.org Product (nom, prix, disponibilité). « InStock » ne veut pas dire livrable
(produit parfois en Click & Collect seulement) : seule la mention « Livraison » de la page compte
(décision du 14/11/2026 : on ne garde que ce qui est livrable).
Plantes livrées par un fournisseur (« expédié directement à votre domicile par notre fournisseur … ») :
pépinière notée « Botanic - <fournisseur> » (décision du 20/11/2026), pour que la BDD lui donne ses propres
frais de port (onglet Livraison) ; le montant affiché par Botanic pour ce produit est noté dans Notes.
Données lues dans les attributs de la fiche : unic_product_shipping_ldd (vrai = livré par un fournisseur),
unic_product_supplier_label (nom du fournisseur), unic_product_shipping_ldd_fee (frais de port du fournisseur).
"""
import datetime as dt
import html
import json
import logging
import os
import re

import config
from core import matching as M
from core.formatting import Offre, contenant, forme, prix_fr, taille_contenant
from core.http import ClientPoli, InterditParRobots

log = logging.getLogger("scraper")

NOM = "Botanic"
PREFIXE = "BOT"  # préfixe des identifiants de relevé (outils/lot.sh)
BASE = "https://www.botanic.com"
SITEMAP_INDEX = BASE + "/Assets/Rbs/Seo/sitemap-index.xml"
FICHE = re.compile(r"^https://www\.botanic\.com/produit/\d+/[a-z0-9/-]+\.html$")
# Adresse qui ressemble à une plante vendue en pot (pour la recherche par nom commun seulement)
FORMAT_PLANTE = re.compile(r"(^| )(pot|litres?|godet|motte|racines? nues?|c\d+|conteneur|baliveau|tige|cepee|touffe|"
                           r"plant|plants|bulbes?)( |$)")
# Rayons de vente des plantes (fil d'Ariane)
RAYONS_PLANTES = ("plantes & fleurs", "potager & verger")


def _texte(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).replace("\xa0", " ").strip()


def _slug(url):
    return url.split("/produit/", 1)[-1].split("/", 1)[-1][:-5].replace("/", " ").replace("-", " ")


def _contenant(nom):
    """« Le pot de Ø 21 x H 70 cm » -> « Pot 21 cm » ; « C10 » -> « Conteneur 10 L »."""
    n = re.sub(r"[Øø⌀]\s*", "", nom)
    m = re.search(r"\bpot\s*(?:de\s*)?([\d.,]+)\s*(?:cm)?\s*x\s*H", n, re.I)
    if m:
        return f"Pot {m.group(1)} cm"
    c = contenant(n)
    if c in ("", "Pot", "Conteneur"):
        m = re.search(r"(?:^|[\s-])C\s?(\d+(?:[.,]\d+)?)(?:\s|$|-)", nom)
        if m:
            return f"Conteneur {m.group(1).replace('.', ',')} L"
    return c


def _hauteur(nom):
    """Hauteur livrée « H 70 cm », « H180 cm », « 100/110 cm » -> « 70 cm », « 100-110 cm »."""
    m = re.search(r"\bH\s?([\d.,]+)\s*(?:(?:/|à|-)\s*([\d.,]+)\s*)?(cm|m)\b", nom)
    if not m:
        m = re.search(r"\b([\d.,]+)\s*(?:/|à|-)\s*([\d.,]+)\s*(cm|m)\s*$", nom)
    if m:
        return f"{m.group(1)}-{m.group(2)} {m.group(3)}" if m.group(2) else f"{m.group(1)} {m.group(3)}"
    return ""


def _attribut(page, cle):
    """Valeur d'un attribut produit de la fiche (JSON de la page), None s'il est absent."""
    m = re.search(r'"' + cle + r'":\{[^{}]*?"value":("(?:[^"\\]|\\.)*"|true|false|null|-?[\d.]+)', page)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def fournisseur(page):
    """(nom du fournisseur, frais de port affichés) si la plante est livrée par un fournisseur, sinon None."""
    if _attribut(page, "unic_product_shipping_ldd") is not True:
        return None
    # Nom écrit sur la fiche (« … par notre fournisseur La Bambouseraie. »), sinon l'attribut (« LA BAMBOUSERAIE »)
    m = re.search(r"par notre fournisseur ([^.\"<]{2,60}?)\s*\.", html.unescape(page.replace("\\u00e0", "à")))
    nom = m.group(1).strip() if m else ""
    if not nom:
        brut = _texte(str(_attribut(page, "unic_product_supplier_label") or ""))
        nom = brut.title() if brut.isupper() else brut
    frais = _attribut(page, "unic_product_shipping_ldd_fee")
    return (nom or "fournisseur inconnu"), (frais if isinstance(frais, (int, float)) else None)


class Adaptateur:
    def __init__(self):
        dmin, dmax = config.DELAIS_PAR_SITE.get("botanic", (None, None))
        self.http = ClientPoli(BASE, dmin, dmax)
        self.urls = None
        self.pages = {}

    # ---------- sitemap ----------
    def _sitemap(self):
        if self.urls is not None:
            return self.urls
        os.makedirs(config.DOSSIER_CACHE, exist_ok=True)
        cache = os.path.join(config.DOSSIER_CACHE, f"sitemap_bot_{dt.date.today():%Y-%m-%d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                self.urls = json.load(f)
            return self.urls
        index = self.http.get(SITEMAP_INDEX).text
        urls = []
        for sm in re.findall(r"<loc>(.*?)</loc>", index):
            if "Rbs_Catalog_Product" not in sm:
                continue
            xml = self.http.get(sm.strip()).text
            urls += [u.strip() for u in re.findall(r"<loc>(.*?)</loc>", xml) if FICHE.match(u.strip())]
        log.info("Sitemap Botanic : %d fiche(s)", len(urls))
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(urls, f)
        self.urls = urls
        return urls

    def candidats(self, plante):
        latin, commun = [], []
        for url in self._sitemap():
            s = _slug(url)
            if M.est_candidat(plante, "", s):
                latin.append(url)
            elif FORMAT_PLANTE.search(M.norm(s)) and M.candidat_nom_commun(plante, s):
                commun.append(url)
        # Nom latin d'abord ; le nom commun ensuite (Botanic n'écrit pas toujours le nom latin dans l'adresse),
        # le nom latin étant de toute façon vérifié sur chaque fiche
        return (latin + commun)[: config.MAX_FICHES_PAR_PLANTE]

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

    @staticmethod
    def _jsonld(page):
        produit, ariane = None, []
        for b in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                d = json.loads(b)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("@type") == "Product" and produit is None:
                produit = d
            elif isinstance(d, dict) and d.get("@type") == "BreadcrumbList":
                ariane = [_texte(x.get("name")) for x in d.get("itemListElement") or []]
        return produit, ariane

    @staticmethod
    def _bloc_principal(page):
        """Texte de la fiche elle-même (sans les produits associés ni le pied de page)."""
        s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
        s = html.unescape(re.sub(r"<[^>]+>", "\n", s)).replace("\xa0", " ")
        s = re.sub(r"[ \t]*\n\s*", "\n", s)
        i = s.find("réf :")
        if i < 0:
            return ""
        fin = len(s)
        for borne in ("Vous aurez aussi besoin", "Commander un végétal sur botanic", "Et si vous nous rejoigniez"):
            j = s.find(borne, i)
            if 0 <= j < fin:
                fin = j
        return s[i:fin]

    @staticmethod
    def _livraison(bloc):
        """(livrable, mention) d'après la ligne qui suit « Livraison » sur la fiche."""
        m = re.search(r"\nLivraison\n(.*?)\n", bloc)
        if not m:
            return False, "mention de livraison absente"
        mention = m.group(1).strip()
        mn = M.norm(mention)
        if "indisponible" in mn or "uniquement" in mn or "click collect" in mn:
            return False, mention
        return True, mention

    def _variete(self, plante, texte):
        """(variété, connue_dans_Fiches) d'après le nom du produit ; vide = espèce type."""
        v = M.variete_fiches(plante, texte)
        if v:
            return v, True
        tete = re.split(r"\.\s|\s-\s|,|\s(?:le|en)\s+pot\b|\spot\b", texte, maxsplit=1, flags=re.I)[0]
        for n in M.noms_latins(plante):
            reste = M._apres(tete, n)
            if reste is not None:
                reste = re.sub(r"^(?:f|var|subsp|ssp)\.?\s+", "", reste).strip(" '\"‘’()®")
                if not reste or M.norm(reste) == M.norm(plante.espece):
                    return "", True
                return reste, False
        return "", True

    def _analyser(self, plante, url, page, aujourdhui):
        p, ariane = self._jsonld(page)
        if not p:
            return [], [(url, "Pas de données produit (JSON-LD) sur la fiche")]
        # « Musa’dwarf Cavendish » : l'apostrophe collée est remplacée par une espace
        nom = re.sub(r"(?<=\w)[’'](?=\w)", " ", _texte(p.get("name")))
        if not any(M.norm(r) == M.norm(x) for x in ariane[:3] for r in RAYONS_PLANTES):
            return [], [(url, f"Hors rayon plantes (« {' > '.join(ariane[1:3])} ») : « {nom} »")]
        bloc = self._bloc_principal(page)
        textes = [nom, bloc]
        notes = []
        equiv = M.equivalence_utilisee(plante, textes)
        if equiv:
            notes.append(f"Nom latin écrit « {equiv} » sur la fiche (équivalence validée)")
        if not M.latin_sur_fiche(plante, textes):
            approche = M.latin_approche(plante, textes)
            # Variété cherchée dans le nom du produit seulement (la description cite d'autres produits)
            var = M.variete_fiches(plante, nom)
            if approche:
                notes.append(f"Nom latin écrit « {approche} » sur la fiche")
            elif M.contient(M.norm(nom), plante.genre) and var:
                notes.append(f"Espèce absente de la fiche (genre {plante.genre} seul) ; "
                             f"rapproché par la variété {var} de l'onglet Fiches")
            else:
                return [], [(url, f"Nom latin {plante.nom_latin} absent de la fiche « {nom} » : aucun prix relevé")]
        if M.est_melange(nom):
            notes.append(f"Lot/mélange de la même espèce (« {nom} »)")

        variete, connue = self._variete(plante, nom)
        if variete and not connue:
            notes.append(f"Variété trouvée : {variete} (absente de l'onglet Fiches)")
        nom_latin = f"{plante.nom_latin} '{variete}'" if variete else plante.nom_latin

        cont = _contenant(nom)
        haut = _hauteur(nom) or forme(nom) or ""
        if not cont:
            notes.append(f"Format non reconnu : « {nom} »")

        offres = p.get("offers") or []
        if isinstance(offres, dict):
            offres = [offres]
        if len(offres) > 1:
            notes.append("Plusieurs offres sur la fiche : seule la première est relevée")
        of = offres[0] if offres else {}
        livrable, mention = self._livraison(bloc)
        pepiniere = NOM
        f = fournisseur(page)
        if f:
            pepiniere = f"{NOM} - {f[0]}"
            notes.append(f"Vendu par Botanic, livré par le fournisseur {f[0]} : frais de port à part"
                         + (f" ({prix_fr(f[1])} € pour ce produit, affiché sur la fiche)" if f[1] is not None else ""))
        en_stock = str(of.get("availability", "")).endswith("InStock")
        if not (livrable and en_stock):
            notes.append(f"Non livrable (« {mention} »)" if not livrable else "Hors stock")
            return [Offre(plante.id, plante.nom_commun, variete or "Espèce type", pepiniere, url, "", "EUR", cont,
                          "Rupture", "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, haut)], []
        if M.norm(mention) != "en stock":
            notes.append(f"Livraison : {mention}")
        prix = of.get("price")
        o = Offre(plante.id, plante.nom_commun, variete or "Espèce type", pepiniere, url, prix_fr(prix), "EUR", cont,
                  "En stock", "", aujourdhui, "", "À vérifier", " ; ".join(notes), nom_latin, haut)
        o.tri = taille_contenant(cont) * 1000 + float(prix or 0)
        return [o], []
