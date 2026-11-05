"""Formatage des colonnes de l'onglet Offres."""
import re
from dataclasses import dataclass, field, asdict

COLONNES = [
    "ID", "Plante", "Variété", "Pépinière", "Lien du produit", "Prix", "Devise", "Contenant",
    "Disponibilité", "Frais de port", "Relevé le", "Lien affilié", "Statut", "Notes", "Nom latin",
    "Hauteur ou forme",
]

FORMES = [  # (motif, libellé) — du plus précis au plus général
    (r"1/4\s*de\s*tige|quart\s*de\s*tige", "1/4 de tige"),
    (r"demi[- ]?tige", "Demi-tige"),
    (r"mini[- ]?tige", "Mini-tige"),
    (r"tigette", "Tigette"),
    (r"haute[- ]?tige", "Haute-tige"),
    (r"\btige\b", "Tige"),
    (r"buisson", "Buisson"),
    (r"cépée|cepee", "Cépée"),
    (r"palissé|espalier", "Palissé"),
    (r"scion", "Scion"),
    (r"gobelet", "Gobelet"),
    (r"boule", "Boule"),
    (r"pyramide|cône|cone", "Cône"),
]


@dataclass
class Offre:
    id: str
    plante: str
    variete: str
    pepiniere: str
    lien: str
    prix: str
    devise: str
    contenant: str
    disponibilite: str
    frais_port: str
    releve_le: str
    lien_affilie: str
    statut: str
    notes: str
    nom_latin: str
    hauteur_forme: str
    tri: float = field(default=0.0, repr=False)  # clé de tri interne (taille), non exportée

    def ligne(self):
        d = asdict(self)
        d.pop("tri")
        return list(d.values())


def prix_fr(valeur):
    if valeur in (None, ""):
        return ""
    return f"{float(valeur):.2f}".replace(".", ",")


def _nombre(txt):
    return txt.replace(",", ".")


def _fr(nb):
    s = f"{float(nb):g}"
    return s.replace(".", ",")


def contenant(desc):
    """« Pot de 2L/3L (…) » -> « Pot 2 à 3 L » ; « Godet de 8/9 cm » -> « Godet 8/9 cm »."""
    d = desc or ""
    dl = re.sub(r"\s*litres?\b", "l", d.lower())
    m = re.search(r"pot\s*(?:de\s*)?([\d.,]+)\s*l?(?:\s*(?:/|à|-)\s*([\d.,]+))?\s*l\b", dl)
    if m:
        a, b = m.group(1), m.group(2)
        return f"Pot {_fr(_nombre(a))} à {_fr(_nombre(b))} L" if b else f"Pot {_fr(_nombre(a))} L"
    m = re.search(r"pot\s*(?:de\s*)?([\d.,]+)\s*(?:/\s*([\d.,]+)\s*)?cm", dl)
    if m:
        return f"Pot {m.group(1)}{'/' + m.group(2) if m.group(2) else ''} cm"
    m = re.search(r"godet(?:\s*de)?\s*([\d.,]+(?:\s*/\s*[\d.,]+)?)\s*cm", dl)
    if m:
        return f"Godet {m.group(1).replace(' ', '')} cm"
    if "godet" in dl:
        return "Godet"
    if "conteneur" in dl:
        return "Conteneur"
    if "racines nues" in dl or "racine nue" in dl:
        return "Racines nues"
    if "motte" in dl:
        return "Motte"
    if "graine" in dl or "sachet" in dl:
        return "Sachet de graines"
    if "rhizome" in dl:
        return "Rhizome"
    if "bulbe" in dl:
        return "Bulbe"
    if re.search(r"\bpot\b", dl):
        return "Pot"
    return ""


def taille_contenant(c):
    """Valeur numérique approximative pour trier les formats (litres)."""
    m = re.search(r"Pot ([\d,]+)(?: à ([\d,]+))? L", c)
    if m:
        return float((m.group(2) or m.group(1)).replace(",", "."))
    if c.startswith("Godet"):
        return 0.3
    if c.startswith("Pot") and "cm" in c:
        return 0.8
    return 0.0


def hauteur(desc):
    """Hauteur livrée normalisée « 30-40 cm » (ou None)."""
    dl = (desc or "").lower()
    m = re.search(r"hauteur[^\d]{0,30}([\d.,]+)\s*(?:cm)?\s*(?:/|à|-)\s*([\d.,]+)\s*(cm|m)\b", dl)
    if m:
        return f"{m.group(1)}-{m.group(2)} {m.group(3)}"
    m = re.search(r"hauteur[^\d]{0,30}([\d.,]+)\s*(cm|m)\b", dl)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return None


def forme(desc):
    dl = (desc or "").lower()
    trouvees = []
    for motif, lib in FORMES:
        if re.search(motif, dl) and not any(lib.lower() in t.lower() or t.lower() in lib.lower() for t in trouvees):
            trouvees.append(lib)
    return ", ".join(trouvees) or None


def lot(desc):
    m = re.search(r"(?:lot|pack|botte)\s*de\s*(\d+)|\bpar\s*(\d+)\b|\bx\s*(\d+)\b|(\d+)\s*(?:plants|pieds|godets|pots)\b",
                  (desc or "").lower())
    if m:
        n = next(g for g in m.groups() if g)
        if int(n) > 1:
            return int(n)
    return None


def repartis(offres, plafond):
    """Garde `plafond` formats répartis du plus petit au plus grand."""
    if plafond is None or len(offres) <= plafond:
        return offres, False
    tri = sorted(offres, key=lambda o: o.tri)
    if plafond == 1:
        return [tri[0]], True
    idx = sorted({round(i * (len(tri) - 1) / (plafond - 1)) for i in range(plafond)})
    return [tri[i] for i in idx], True
