"""Lecture de la BDD Plante (export CSV des onglets Plantes + colonne Variétés de Fiches)."""
import csv
import os
import re
from dataclasses import dataclass, field


@dataclass
class Variete:
    nom: str
    synonymes: list = field(default_factory=list)


@dataclass
class Plante:
    id: str
    nom_commun: str
    autres_noms: list
    synonymes_latins: list
    genre: str
    espece: str
    varietes: list  # [Variete]
    equivalences_latin: list = field(default_factory=list)  # noms latins écrits autrement sur les sites, validés

    @property
    def nom_latin(self):
        return f"{self.genre} {self.espece}".strip()


def _liste(texte, sep=r"[,;]"):
    return [x.strip() for x in re.split(sep, texte or "") if x.strip()]


def lire_varietes(texte):
    """Une variété par ligne : « Nom = particularités ». Les synonymes sont lus dans « Synonymes : … »."""
    res = []
    for ligne in (texte or "").splitlines():
        if "=" not in ligne:
            continue
        nom, reste = ligne.split("=", 1)
        nom = nom.strip()
        syn = []
        m = re.search(r"Synonymes\s*:\s*([^;]+)", reste)
        if m:
            syn = _liste(m.group(1), r",")
        res.append(Variete(nom, syn))
    return res


def lire_equivalences(chemin):
    """Table « Nom sur le site » -> « Nom dans Fiches ». ID vide = valable pour toutes les plantes."""
    if not chemin or not os.path.exists(chemin):
        return []
    with open(chemin, encoding="utf-8", newline="") as f:
        return [(r["ID"].strip(), r["Nom sur le site"].strip(), r["Nom dans Fiches"].strip())
                for r in csv.DictReader(f) if r.get("Nom sur le site") and r.get("Nom dans Fiches")]


def _appliquer_equivalences(plante, equivalences):
    for id_, site, fiches in equivalences:
        if id_ and id_ != plante.id:
            continue
        for v in plante.varietes:
            if v.nom.strip().lower() == fiches.lower() and site not in v.synonymes:
                v.synonymes.append(site)


def lire_equivalences_latin(chemin):
    """Table « Nom latin sur le site » validée par l'utilisatrice : ID -> [noms]."""
    if not chemin or not os.path.exists(chemin):
        return {}
    res = {}
    with open(chemin, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("ID") and r.get("Nom latin sur le site"):
                res.setdefault(r["ID"].strip(), []).append(r["Nom latin sur le site"].strip())
    return res


def lire_plantes(chemin, ids=None, equivalences=None, equivalences_latin=None):
    with open(chemin, encoding="utf-8", newline="") as f:
        plantes = [
            Plante(
                id=r["ID"].strip(),
                nom_commun=r["Nom commun"].strip(),
                autres_noms=_liste(r.get("Autres noms")),
                synonymes_latins=_liste(r.get("Synonymes")),
                genre=r["Genre"].strip(),
                espece=r["Espèce"].strip(),
                varietes=lire_varietes(r.get("Variétés")),
            )
            for r in csv.DictReader(f)
        ]
    if ids:
        plantes = [p for p in plantes if p.id in ids]
    eq = lire_equivalences(equivalences)
    eql = lire_equivalences_latin(equivalences_latin)
    for p in plantes:
        _appliquer_equivalences(p, eq)
        p.equivalences_latin = eql.get(p.id, [])
    return plantes
