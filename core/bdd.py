"""Lecture de la BDD Plante (export CSV des onglets Plantes + colonne Variétés de Fiches)."""
import csv
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


def lire_plantes(chemin, ids=None):
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
    return plantes
