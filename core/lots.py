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
_LOT = re.compile(r"\b(?:lot|set) de (\d+)\b", re.I)


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


def regrouper_lots(lignes):
    """lignes : rangées d'Offres sans l'en-tête (16 à 18 colonnes). Rend (lignes à 18 colonnes, nb retirées)."""
    lignes = [completer(r) for r in lignes]
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
