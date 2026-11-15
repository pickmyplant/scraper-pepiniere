#!/usr/bin/env python3
"""Analyse l'onglet Offres et prépare les corrections de la colonne Variété.

Lit les onglets publiés de la BDD (lien de l'onglet Export dans BDD_EXPORT_URL ; le numéro d'onglet est remplacé),
nettoie la colonne Variété (formes, tailles, cache-pots, nom d'espèce répété), relie aux variétés de Fiches
(orthographes, synonymes : data/corrections_varietes.txt) et écrit :
  corrections/<ID>.tsv            lignes d'Offres à modifier ou supprimer (appliquées par le bouton de la BDD)
  corrections/<ID>_varietes.tsv   variétés absentes de Fiches, à ajouter
  corrections/<ID>_plantes.tsv    fiches d'hybrides à créer (Plantes et Fiches)
  corrections/<ID>_resume.txt     résumé chiffré
Usage : python outils/corriger_offres.py CORR-AAAA-MM-JJ-A1 [dossier_csv]
"""
import csv, difflib, io, os, re, sys, unicodedata, urllib.request, collections as C

GID = {"Offres": 1200, "Export": 1815189843}
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOUVELLES = {  # fiches d'hybrides à créer : ID -> (nom commun, genre, espèce, famille)
    "P584": ("Gazania hybride", "Gazania", "Hybrides horticoles", "Astéracées"),
    "P585": ("Lilas des Indes hybride", "Lagerstroemia", "Hybrides horticoles", "Lythracées"),
    "P586": ("Agave hybride", "Agave", "Hybrides horticoles", "Asparagacées"),
    "P587": ("Coprosma hybride", "Coprosma", "Hybrides horticoles", "Rubiacées"),
    "P588": ("Magnolia hybride", "Magnolia", "Hybrides horticoles", "Magnoliacées"),
}

def lire(nom, dossier):
    if dossier:
        f = open(os.path.join(dossier, "g%d.csv" % GID[nom]), encoding="utf-8")
    else:
        url = re.sub(r"gid=\d+", "gid=%d" % GID[nom], os.environ["BDD_EXPORT_URL"])
        f = io.StringIO(urllib.request.urlopen(url, timeout=120).read().decode("utf-8"))
    return list(csv.reader(f))

def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

PARTICULES = {"de", "d", "du", "des", "la", "le", "l", "s", "f1", "hf1", "x", "et", "and", "the", "of"}
def cle(s):
    return "".join(t for t in norm(s).split() if t not in PARTICULES) or norm(s).replace(" ", "")

FORMES = [(r"1/2[- ]?tige|demi[- ]tige|half stem|½", "Demi-tige"), (r"¼|1/4 de tige|quart de tige", "Quart de tige"),
 (r"mini[- ]tige", "Mini-tige"), (r"(sur |forme |on )?(haute[- ])?tiges?\b|\bstem\b|\bstam\b|\bstandard\b|\bon trunk\b|\btrunk\b", "Tige"),
 (r"touffe|multi[- ]?troncs?|multi[- ]stam|c[ée]p[ée]e", "Touffe"), (r"boules?\b|\bball\b", "Boule"), (r"pyramide|\bc[oô]ne\b", "Pyramide"),
 (r"(taill[ée]e? en |forme )?nuages?|niwaki", "Nuage"), (r"bonsa[iï]", "Bonsaï"), (r"paliss[ée]e?s?|espalier|treillis|sur tuteur|tuteur[ée]e?", "Palissé"),
 (r"ramifi[ée]e?s?|branched|branchu", "Ramifié"), (r"buisson|\bbush\b|arbustif|forme arbustive", "Buisson"), (r"gobelet", "Gobelet"), (r"scion", "Scion"),
 (r"(en |à |pot à )?suspen(sion|dre|du)|hanging", "Suspension"), (r"sp[ée]cimen|grande taille", "Spécimen"), (r"spirale", "Spirale"),
 (r"tress[ée]e?s?|braided", "Tressé"), (r"colonne", "Colonne"), (r"\btree\b|\barbre\b|boom( de luxe)?", "Arbre"), (r"\bx+l\b|\bde luxe\b", "Grand sujet"),
 (r"quenouille", "Quenouille"), (r"fuseau", "Fuseau"), (r"\bhaie\b", "Haie"), (r"topiaire", "Topiaire")]
FORMES = [(re.compile(r"(?<![\w'])(?:" + a + r")(?![\w'])", re.I), b) for a, b in FORMES]
DECHETS = [re.compile(x, re.I) for x in [
 r"\b(with|including|incl\.?|incluant|y compris|avec|et son|en|in|dans( un)?|sur|\+)\s+(sofia |torun |p&pure |support |cache[- ]?)?(basket|elho|viber|cache[- ]pot|panier|pot|godet|coupe|mand|bac|jardini[èe]re|support|lot|c[ée]ramique)\b.*$",
 r"\bcache[- ]pot\b.*$", r"\bplante de haie.*$", r"\bplante vivace.*$", r"\bpot \d+.*$", r"\bhauteur\b.*$", r"[Øø⌀↕]\s*\d+\s*(cm)?",
 r"\b\d+(\s*[/-]\s*\d+)?\s*(cm|m|l|litres?)?\b(?!\s*saisons)", r"\b(botanic|bio|coloris( vert)?|multicolore)\b\s*[–-]?\s*$", r"\.Le$", r"\bcov\b", r"\baka\b", r"[®™©?♀♂⚥–]",
 r"\bs[ée]lection\b", r"\b(m[ée]lange|mix|mixed|mixture)\b", r"\b(femelle|m[âa]le|autofertile|auto-fertile)\b", r"\bfruit me\b", r"-? ?nain de jardin",
 r"\bobt(ention|\.)? clause\b", r"\bvari[ée]t[ée] auto.*$", r"\ben plants?( greff[ée]s)?\b", r"\bgraines( pelliculées)?\b", r"\bbesoin de pied.*$", r"\bforme m[âa]le.*$"]]
COULEURS = {"rose", "rouge", "blanc", "blanche", "violet", "violette", "mauve", "jaune", "bleu", "marron", "vert", "orange", "argent", "vif", "clair", "fonce"}
GENERIQUES = {"x", "var", "subsp", "ssp", "f", "ou", "de", "du", "des", "d", "le", "la", "les", "a", "et", "l", "commun", "commune", "vrai", "vraie",
 "officinal", "officinale", "plante", "arbre", "palmier", "faux", "appele", "dit", "type", "espece", "sp"}

class Analyse:
    def __init__(self, dossier=None):
        self.offres = lire("Offres", dossier)
        self.plantes = {r[0]: r for r in lire("Export", dossier)[1:] if r and r[0]}
        for i, (nom, g, e, fam) in NOUVELLES.items():
            self.plantes.setdefault(i, [i, nom, "", "", g, e, ""])
        self.mots = {p: set(norm(" ".join(r[1:6])).split()) | GENERIQUES for p, r in self.plantes.items()}
        self.fiche = {p: self._fiche(r[6]) for p, r in self.plantes.items()}
        self.regles = C.defaultdict(list); self.liens = []
        for l in open(os.path.join(RACINE, "data", "corrections_varietes.txt"), encoding="utf-8"):
            l = l.rstrip("\n")
            if not l or l.startswith("#"): continue
            p, reste = l.split("|", 1)
            motifs, dec = reste.rsplit("|", 1)
            for m in motifs.split(";"):
                if m.startswith("lien:"): self.liens.append((p, m[5:], dec))
                else: self.regles[p].append((re.compile(m), dec))

    @staticmethod
    def _fiche(cellule):
        out = {}
        for l in cellule.split("\n"):
            l = l.strip()
            if not l: continue
            n = l.split(" = ")[0].strip() if " = " in l else l.split(":")[0].strip()
            out.setdefault(cle(n), n)
            for m in re.finditer(r"Synonymes? : ([^;]+)", l):
                for x in re.split(r",| ou ", m.group(1)):
                    if cle(x): out.setdefault(cle(x), n)
            m = re.match(r"^[^=]+= '([^']+)'", l)
            if m: out.setdefault(cle(m.group(1)), n)
        out.pop("", None)
        return out

    def nettoyer(self, p, v):
        formes, code, t = [], "", self.mots[p]
        m = re.search(r"[®™]\s*'?\s*['‘]\s*([A-Za-z0-9 -]+)$", v)
        if m: code, v = m.group(1).strip(), v[:m.start()]
        v = re.sub(r"\(([^)]*)\)?", lambda m: " " if set(norm(m.group(1)).split()) <= t | {"nain", "naine", "geant"} or len(m.group(1)) > 25 else " " + m.group(1) + " ", v)
        v = re.sub(r"\bsyn\.? .*$", "", v.replace(")", " "))
        for rx in DECHETS: v = rx.sub(" ", v)
        for rx, f in FORMES:
            if rx.search(v): formes.append(f); v = rx.sub(" ", v)
        vs = re.sub(r"\s+", " ", v).strip(" -,;.'‘’\"/+").split()
        while vs and set(norm(vs[0]).split()) <= t: vs.pop(0)
        while vs and norm(vs[-1]) in ("commun", "type", ""): vs.pop()
        if all(set(norm(x).split()) <= t for x in vs): vs = []
        return " ".join(vs), formes, code

    def decider(self, p, v):
        """-> (ID, variété ou '' pour espèce type, formes, code, action, raison) ; action : '', ERR, SUPPR"""
        f = self.fiche[p]
        if cle(v) in f: return p, f[cle(v)], [], "", "", ""
        c, formes, code = self.nettoyer(p, v)
        if not c: return p, "", formes, code, "", ""
        if cle(c) in f: return p, f[cle(c)], formes, code, "", ""
        n = norm(c)
        for q in (p, "*"):
            for rx, dec in self.regles[q]:
                m = rx.fullmatch(n)
                if not m: continue
                if dec.startswith("=ET"): return p, "", formes, code, "", dec[4:]
                if dec.startswith("=ERR"): return p, c, formes, code, "ERR", dec[5:]
                if dec.startswith("=SUPPR"): return p, c, formes, code, "SUPPR", dec[7:]
                if dec.startswith(">"):
                    cible, _, nom = dec[1:].partition(":")
                    nom = self._nom(m.expand(nom)) if "\\" in nom else (nom or c)
                    g = self.fiche[cible]
                    if not nom.strip() or all(set(norm(x).split()) <= self.mots[cible] for x in nom.split()): nom = ""
                    return cible, g.get(cle(nom), nom), formes, code, "", ""
                nom = self._nom(m.expand(dec)) if "\\" in dec else dec
                return p, f.get(cle(nom), nom), formes, code, "", ""
        return p, c, formes, code, "", ""

    @staticmethod
    def _nom(s):
        petits = {"de", "du", "des", "d", "la", "le", "et", "of", "and", "in", "vom", "von", "van", "der", "di", "a"}
        mots = s.split()
        if s != s.lower() and s != s.upper(): return s
        return " ".join(w.lower() if i and w.lower() in petits else w.capitalize() for i, w in enumerate(mots))

    def latin(self, p, v):
        r = self.plantes[p]
        return ("%s %s" % (r[4], r[5])).strip() + (" '%s'" % v if v else "")

    def run(self):
        en_tete = self.offres[0]
        dec = {}; compte = C.Counter(); cand = C.defaultdict(C.Counter); suppr = {}
        lignes = []
        for r in self.offres[1:]:
            r = r + [""] * (16 - len(r))
            if not r[0].strip() or r[0].startswith("(exemple)") or r[0] not in self.plantes: continue
            if r[13] == "Non trouvé" or not r[4]: compte["non trouvé"] += 1; continue
            lignes.append(r)
            for p, bout, d in self.liens:   # produit qui n'est pas la plante, reconnu par son lien
                if p == r[0] and bout in r[4]:
                    suppr[(r[0], r[3], r[4], r[2], r[15])] = ["SUPPR", r[0], r[1], r[2], "", "", r[14], r[15], "", d[7:]]
            if (r[0], r[3], r[4], r[2], r[15]) in suppr: compte["à supprimer"] += 1; continue
            if r[2] in ("", "Espèce type"): compte["espèce type (inchangé)"] += 1; continue
            k = (r[0], r[2])
            if k not in dec: dec[k] = self.decider(*k)
            p, v = dec[k][0], dec[k][1]
            if v and cle(v) not in self.fiche[p] and not dec[k][4]: cand[p][v] += 1
        # fusion des orthographes voisines parmi les noms absents de Fiches
        ref = {}
        for p, noms in cand.items():
            f = self.fiche[p]; groupes = {}
            for v in sorted(noms, key=lambda x: (-noms[x], x.islower() or x.isupper(), x)):
                k = cle(v); toks = norm(v).split()
                cible = None
                m = difflib.get_close_matches(k, list(f), 1, 0.92) if len(k) >= 7 else []
                if m: cible = ("F", f[m[0]])
                else:
                    while len(toks) > 1 and toks[-1] in COULEURS:   # « Electric Pink rose » -> « Electric Pink »
                        toks = toks[:-1]; k2 = cle(" ".join(toks))
                        if k2 in f: cible = ("F", f[k2]); break
                        if k2 in groupes: cible = ("N", groupes[k2]); break
                    if not cible:
                        m = difflib.get_close_matches(k, list(groupes), 1, 0.92) if len(k) >= 7 else ([k] if k in groupes else [])
                        if m: cible = ("N", groupes[m[0]])
                if not cible:
                    groupes[k] = self._nom(v); cible = ("N", groupes[k])
                ref[(p, v)] = cible
        self.ref = ref
        # corrections ligne par ligne
        corr = {}; nouv = C.defaultdict(lambda: {"n": 0, "pep": C.Counter(), "lien": {}, "codes": set(), "vus": set()})
        corr.update(suppr)
        for r in lignes:
            if r[2] in ("", "Espèce type") or (r[0], r[3], r[4], r[2], r[15]) in suppr: continue
            p, v, formes, code, action, raison = dec[(r[0], r[2])]
            statut = ""
            if v and not action:
                typ, v = ref.get((p, v)) or ("F", self.fiche[p].get(cle(v), v))
                if typ == "N":
                    d = nouv[(p, v)]; d["n"] += 1; d["pep"][r[3]] += 1; d["lien"].setdefault(r[3], r[4]); d["vus"].add(r[2])
                    if code: d["codes"].add(code)
                    compte["nouvelle variété (à ajouter à Fiches)"] += 1
                else: compte["reliée à une variété de Fiches"] += 1
            elif action == "ERR": compte["autre plante (à vérifier)"] += 1; statut = "À vérifier"
            elif action == "SUPPR": compte["à supprimer"] += 1
            else: compte["ramenée à l'espèce type"] += 1
            if p != r[0]: compte["  dont déplacée vers une autre fiche"] += 1
            nv = v or "Espèce type"
            ajouts = []                      # notes ajoutées ; la mention « Variété trouvée… » est retirée par le bouton
            if action == "ERR": ajouts.append("Autre plante : " + raison); nv = r[2]
            elif raison and action != "SUPPR": ajouts.append(raison[0].upper() + raison[1:])
            if cle(nv) != cle(r[2]) and action != "ERR": ajouts.append("Vendu sous : " + r[2])
            if p != r[0]: ajouts.append("Déplacé depuis " + r[0])
            forme = r[15]
            if formes and not forme: forme = ", ".join(formes)
            elif formes and "Forme :" not in r[13]: ajouts.append("Forme : " + ", ".join(formes))
            reliee = action != "ERR" and (nv == "Espèce type" or cle(nv) in self.fiche[p])
            latin = self.latin(p, "" if nv == "Espèce type" else nv) if action != "ERR" else r[14]
            nouvelle = [p, self.plantes[p][1], nv, statut, " ; ".join(ajouts), latin, forme, "oui" if reliee else ""]
            if action == "SUPPR" or nouvelle[:3] + nouvelle[5:7] != [r[0], r[1], r[2], r[14], r[15]] or statut or ajouts or (reliee and "Variété trouvée" in r[13]):
                corr[(r[0], r[3], r[4], r[2], r[15])] = ["SUPPR" if action == "SUPPR" else "MODIF"] + nouvelle + ([raison] if action == "SUPPR" else [""])
        return corr, nouv, compte, len(lignes)

if __name__ == "__main__":
    ident = sys.argv[1]; dossier = sys.argv[2] if len(sys.argv) > 2 else None
    a = Analyse(dossier); corr, nouv, compte, n = a.run()
    os.makedirs(os.path.join(RACINE, "corrections"), exist_ok=True)
    base = os.path.join(RACINE, "corrections", ident)
    with open(base + ".tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["ID", "Pépinière", "Lien du produit", "Variété", "Hauteur ou forme", "Action", "Nouvel ID", "Nouvelle plante", "Nouvelle variété", "Nouveau statut", "Notes à ajouter", "Nom latin", "Nouvelle hauteur ou forme", "Retirer « Variété trouvée »", "Raison"])
        for k in sorted(corr): w.writerow(list(k) + corr[k])
    with open(base + "_varietes.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["ID", "Plante", "Nom latin", "Variété", "Vendue aussi sous", "Offres", "Pépinières", "Lien source"])
        for (p, v), d in sorted(nouv.items()):
            autres = sorted({x for x in d["vus"] if cle(x) != cle(v)} | d["codes"])
            pep = [x for x, _ in d["pep"].most_common()]
            w.writerow([p, a.plantes[p][1], a.latin(p, v), v, " ; ".join(autres), d["n"], ", ".join(pep), d["lien"][pep[0]]])
    with open(base + "_plantes.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["ID", "Nom commun", "Genre", "Espèce", "Famille"])
        for i in sorted({v[1] for v in corr.values()} & set(NOUVELLES)): w.writerow([i] + list(NOUVELLES[i]))
    res = ["Offres avec un lien : %d" % n] + ["%s : %d" % kv for kv in sorted(compte.items(), key=lambda x: -x[1])]
    res += ["Lignes à modifier ou supprimer (clés distinctes) : %d" % len(corr), "Variétés absentes de Fiches : %d (sur %d plantes)" % (len(nouv), len({p for p, _ in nouv}))]
    open(base + "_resume.txt", "w", encoding="utf-8").write("\n".join(res) + "\n")
    print("\n".join(res))
