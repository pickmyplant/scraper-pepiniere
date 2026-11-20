"""Prépare la mise à jour d'un relevé dans l'onglet Offres (même pépinière + mêmes plantes ; « <pépinière> - <fournisseur> » compte comme la même pépinière).

On ne lit que deux colonnes d'Offres (ID et Pépinière), puis on calcule les lignes
de l'ancien relevé à supprimer avant d'ajouter les nouvelles lignes en bas.

Utilisation :
  1. Lire Offres!A:A et Offres!D:D avec le connecteur Google Sheets (deux get_values),
     et enregistrer les deux résultats JSON dans des fichiers.
  2. python outils/remplacement.py ids.json pepinieres.json "Planfor" P006,P007,P013
  3. La sortie donne les numéros de lignes concernées et les requêtes `deleteDimension`
     (de bas en haut) à passer à update_spreadsheet. Ajouter ensuite les nouvelles lignes
     avec append_values, puis compléter l'onglet Relevés.
"""
import json
import sys

ID_ONGLET_OFFRES = 1200  # sheetId de l'onglet Offres


def valeurs(chemin):
    d = json.load(open(chemin, encoding="utf-8"))
    return [(r[0] if r else "") for r in d.get("values", [])]


def plages(numeros):
    """[5,6,7,10] -> [(5,7),(10,10)] (numéros de lignes, 1 = en-tête)."""
    res = []
    for n in sorted(numeros):
        if res and n == res[-1][1] + 1:
            res[-1] = (res[-1][0], n)
        else:
            res.append((n, n))
    return res


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    ids, peps = valeurs(sys.argv[1]), valeurs(sys.argv[2])
    pepiniere = sys.argv[3].strip()
    cibles = {x.strip() for x in sys.argv[4].split(",") if x.strip()}
    lignes = [i + 1 for i in range(1, max(len(ids), len(peps)))
              if i < len(ids) and i < len(peps) and ids[i].strip() in cibles
              and (peps[i].strip() == pepiniere or peps[i].strip().startswith(pepiniere + " - "))]
    groupes = plages(lignes)
    requetes = [{"deleteDimension": {"range": {"sheetId": ID_ONGLET_OFFRES, "dimension": "ROWS",
                                                "startIndex": a - 1, "endIndex": b}}}
                for a, b in sorted(groupes, reverse=True)]
    print(json.dumps({
        "lignes_a_supprimer": [f"{a}-{b}" if a != b else str(a) for a, b in groupes],
        "nombre": len(lignes),
        "requetes": requetes,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
