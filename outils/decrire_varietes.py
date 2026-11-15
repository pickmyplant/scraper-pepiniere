#!/usr/bin/env python3
"""Prépare le petit texte et la source des variétés absentes de Fiches, à partir de la page du vendeur.

Lit corrections/<ID>_varietes.tsv, ouvre une seule page par variété (celle de la pépinière qui a le plus d'offres),
avec le client poli du dépôt (robots.txt, délais, arrêt sur blocage ; un fil par site, une requête à la fois par site).
Les pages lues sont gardées dans un cache JSON (hors dépôt) pour pouvoir reprendre.
Écrit corrections/<ID>_fiches.tsv : ID, Variété, ligne à ajouter dans la colonne Variétés de Fiches.
Usage : python outils/decrire_varietes.py <ID> <cache.json> [--sans-reseau]
"""
import csv, html, json, os, re, sys, threading, collections as C
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
from core.http import ClientPoli

VERROU = threading.Lock()
ACCROCHES = re.compile(r"^(offrez[- ]vous|d[ée]couvrez|achetez|commandez|adoptez|craquez pour|plantez|optez pour|cultivez|profitez d[eu]s?|vente d[eu']|achat d[eu']|acheter)\b[^,.:;!]*?(?=,|\bun\b|\bune\b|\bdes\b|:|\.)[,: ]*", re.I)
ACCROCHES2 = re.compile(r"^(apportez|offrez|donnez|ajoutez|illuminez|habillez|sublimez|[ée]gayez|invitez|cr[ée]ez)\b[^.]*?\bavec\s+(?:l[ea]s?\s|l'|ce(?:tte)?\s|une?\s)[^,.]*,\s*(?:qui\s+)?", re.I)
VIDES = re.compile(r"propose d'acheter|vous accompagne|plus grande p[ée]pini[èe]re|au meilleur|comme leur nom l'indique|atout charme|large choix|meilleur prix|en ligne sur", re.I)
PRECISES = {"Promesse de Fleurs", "Ma Pépinière", "Plantes Avenue", "Plant and Stories", "La Green Touch", "Rayon de Serre", "Willemse", "Pépinières Naudet", "La Maison du Bananier"}
FINS = re.compile(r"\s*(commandez|livraison|achetez|d[ée]couvrez|en vente|prix|exp[ée]di|paiement|profitez|convient aux d[ée]butants|✓|✅|➤|\|).*$", re.I)

def extraire(t):
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content="([^"]*)"', t, re.I) or re.search(r'<meta[^>]+content="([^"]*)"[^>]+name=["\']description["\']', t, re.I)
    d = html.unescape(html.unescape(m.group(1))).strip() if m else ""
    txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style).*?</\1>", " ", t))))
    h = re.search(r"Hauteur (?:à maturité|adulte)\s*:?\s*((?:De |de )?\d+(?:[.,]\d+)?\s*(?:c?m)?(?:\s*(?:à|-)\s*\d+(?:[.,]\d+)?)?\s*c?m)", txt)
    r = re.search(r"Rusticité[^0-9°]{0,25}([-−–]\s?\d+(?:[.,]\d)?)\s*°\s*C", txt) or re.search(r"([-−]\s?\d+)\s*°\s*C", d)
    return {"d": d, "h": h.group(1).strip() if h else "", "r": r.group(1).replace("−", "-").replace("–", "-").replace(" ", "") if r else ""}

def lire_site(base, urls, cache, chemin):
    try:
        client = ClientPoli(base)
    except Exception as e:
        print("arrêt", base, repr(e)[:80], flush=True); return
    for i, u in enumerate(urls):
        try:
            res = extraire(client.get(u).text)
        except Exception as e:
            nom = type(e).__name__
            if nom in ("Bloque",): print("blocage, arrêt", base, flush=True); return
            res = {"erreur": nom}
        with VERROU:
            cache[u] = res
            if i % 25 == 0: json.dump(cache, open(chemin, "w"), ensure_ascii=False)
    with VERROU: json.dump(cache, open(chemin, "w"), ensure_ascii=False)

def simple(x):
    import unicodedata
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower())

def texte(d, variete, pepiniere):
    """Texte court tiré de la description de la page ; vide si elle ne décrit pas cette variété."""
    d = re.sub(r"\s+", " ", d.replace("’", "'").replace("\u00a0", " "))
    if VIDES.search(d): return ""
    if pepiniere not in PRECISES and simple(variete) not in simple(d): return ""
    d = ACCROCHES2.sub("", ACCROCHES.sub("", d))
    phrases = [FINS.sub("", p).strip() for p in re.split(r"(?<=[.!?])\s+", d)]
    out = ""
    for p in phrases:
        if len(p) < 15: continue
        if len(out) + len(p) > 190: break
        out = (out + " " + p).strip()
    if not out and phrases: out = phrases[0][:190].rsplit(" ", 1)[0]
    out = out.strip(" .!").replace("\t", " ")
    return out[:1].lower() + out[1:] if out and not out[:2].isupper() else out

def main():
    ident, chemin = sys.argv[1], sys.argv[2]
    base = os.path.join(RACINE, "corrections", ident)
    var = list(csv.DictReader(open(base + "_varietes.tsv", encoding="utf-8"), delimiter="\t"))
    cache = json.load(open(chemin)) if os.path.exists(chemin) else {}
    if "--sans-reseau" not in sys.argv:
        par_site = C.defaultdict(list)
        for v in var:
            u = v["Lien source"]
            if u and u not in cache: par_site[re.match(r"https?://[^/]+", u).group(0)].append(u)
        fils = [threading.Thread(target=lire_site, args=(b, us, cache, chemin)) for b, us in par_site.items()]
        for f in fils: f.start()
        for f in fils: f.join()
    n = C.Counter()
    with open(base + "_fiches.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["ID", "Variété", "Ligne à ajouter dans Fiches (colonne Variétés)", "Lien source"])
        for v in var:
            pep = v["Pépinières"].split(", ")[0]; src = pep.replace(" ", "-")
            c = cache.get(v["Lien source"]) or {}
            t = texte(c["d"], v["Variété"], pep) if c.get("d") else ""
            n["avec texte" if t else "sans texte"] += 1
            ligne = "%s = %s (%s)" % (v["Variété"], t or "variété vendue par " + pep + ", description à compléter", src)
            if v["Vendue aussi sous"]: ligne += " ; Synonymes : " + v["Vendue aussi sous"].replace(" ; ", ", ")
            if c.get("h"): ligne += " ; Hauteur : " + c["h"]
            if c.get("r"): ligne += " ; Rusticité : %s °C (%s)" % (c["r"], src)
            w.writerow([v["ID"], v["Variété"], ligne, v["Lien source"]])
    print(dict(n))

if __name__ == "__main__":
    main()
