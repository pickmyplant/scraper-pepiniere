"""Lots (consignes de la BDD, section 7) : « Prix = toujours le prix d'UN plant », jamais une ligne à part.

Les adaptateurs notent « lot de N » (ou « set de N ») dans Notes avec le prix du lot dans Prix ; ce module
regroupe ces lignes à l'export de chaque relevé (et à nouveau dans outils/corriger_offres.py) :
- lot qui double une ligne à l'unité (même plante, lien, contenant, hauteur, variété, devise) : ligne retirée,
  « lot de N : … € (… € le plant) » ajouté aux Notes de la ligne à l'unité ;
- produit vendu seulement par lot : une ligne gardée, Prix = prix d'un plant du plus petit lot,
  colonne Lot (Q, ajoutée par Ugo le 19/11/2026) = « N ; prix du lot » (ex. « 8 ; 44,90 »),
  et le détail de tous les lots dans Notes (« Vendu seulement par lot : … »).
"""
import collections
import re

NB_COLONNES = 18          # ID … Hauteur ou forme, Lot, Expédié de
COL_NOTES, COL_LOT = 13, 16
# « lot de N » noté par l'adaptateur ; pas « lot de N : … € (… € le plant) », déjà écrit par ce module sur une
# ligne à l'unité (sinon le second passage, outils/corriger_offres.py --releve, la prenait pour un lot)
_LOT = re.compile(r"\b(?:lot|set) de (\d+)\b(?!\s*:)", re.I)


def _nombre(x):
    try:
        return float(str(x).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _fr(x):
    return ("%.2f" % x).replace(".", ",")


def n_lot(r):
    """Nombre de plants de la ligne (1 si ligne à l'unité ou lot déjà ramené au prix d'un plant)."""
    if r[COL_LOT].strip() or "Vendu seulement par lot" in r[COL_NOTES]:
        return 1
    m = _LOT.search(r[COL_NOTES])
    return int(m.group(1)) if m and int(m.group(1)) > 1 else 1


def completer(r):
    return list(r) + [""] * (NB_COLONNES - len(r))


_ABIME = re.compile(r"(?<![0-9]) : (\d+,\d{2}) € \((\d+,\d{2}) € le plant\)")
_VSL = " ; Vendu seulement par lot : "


def reparer(r):
    """Ligne à l'unité prise pour un lot avant le 22/11/2026 (second passage, « lot de N » de ses Notes effacé) :
    « … : 71,00 € (35,50 € le plant) ; Vendu seulement par lot : lot de 2 : 49,00 € (24,50 € le plant) »,
    Prix 24,50, Lot « 2 ; 49,00 » -> Prix 49,00, Lot vide, « … ; lot de 2 : 71,00 € (35,50 € le plant) »."""
    notes = r[COL_NOTES]
    if not r[COL_LOT].strip() or _VSL not in notes:
        return r
    avant = notes.split(_VSL, 1)[0]
    if not _ABIME.search(avant):
        return r
    lots = []
    for m in _ABIME.finditer(avant):
        p, pp = _nombre(m.group(1)), _nombre(m.group(2))
        if not p or not pp:
            return r
        lots.append("lot de %d : %s € (%s € le plant)" % (round(p / pp), m.group(1), m.group(2)))
    reste = _ABIME.sub("", avant).strip(" ;")
    r[5] = r[COL_LOT].split(";")[1].strip()
    r[COL_LOT] = ""
    r[COL_NOTES] = " ; ".join(x for x in [reste] + lots if x)
    return r


def regrouper_lots(lignes):
    """lignes : rangées d'Offres sans l'en-tête (16 à 18 colonnes). Rend (lignes à 18 colonnes, nb retirées)."""
    lignes = [reparer(completer(r)) for r in lignes]
    groupes = collections.OrderedDict()
    for i, r in enumerate(lignes):
        k = (r[0], re.sub(r"[?#].*$", "", r[4]).rstrip("/"), r[7], r[15], r[2], r[6]) if r[4] else ("", i)
        groupes.setdefault(k, []).append(i)
    retirer = set()
    for idx in groupes.values():
        lots = [i for i in idx if n_lot(lignes[i]) > 1]
        if not lots:
            continue
        unites = [i for i in idx if n_lot(lignes[i]) == 1]
        meilleurs = {}
        for i in lots:
            p = _nombre(lignes[i][5])
            n = n_lot(lignes[i])
            if p:
                meilleurs[n] = min(meilleurs.get(n, p), p)
        texte = " ; ".join("lot de %d : %s € (%s € le plant)" % (n, _fr(p), _fr(p / n)) for n, p in sorted(meilleurs.items()))
        if unites:
            for u in unites:
                if texte and texte not in lignes[u][COL_NOTES]:
                    lignes[u][COL_NOTES] = (lignes[u][COL_NOTES] + " ; " if lignes[u][COL_NOTES] else "") + texte
            retirer.update(lots)
        elif meilleurs:
            n0 = min(meilleurs)
            garde = next(i for i in sorted(lots, key=lambda i: n_lot(lignes[i]))
                         if n_lot(lignes[i]) == n0 and _nombre(lignes[i][5]))
            r = lignes[garde]
            r[5] = _fr(meilleurs[n0] / n0)
            r[COL_LOT] = "%d ; %s" % (n0, _fr(meilleurs[n0]))
            notes = re.sub(r"\s*;?\s*\blot de \d+\b", "", r[COL_NOTES]).strip(" ;")
            r[COL_NOTES] = (notes + " ; " if notes else "") + "Vendu seulement par lot : " + texte
            retirer.update(i for i in lots if i != garde)
    return [r for i, r in enumerate(lignes) if i not in retirer], len(retirer)
